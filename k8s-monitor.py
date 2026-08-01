#!/usr/bin/env python3
"""
k8s-monitor.py — Kubernetes cluster health check via Groq (free) + Discord webhook.

Usage:
  uv run k8s-monitor.py

Cron example (twice daily at 08:00 and 20:00):
  0 8,20 * * * /home/pi/.local/bin/uv run --project /home/pi/k3s-monitoring \
      k8s-monitor.py >> /home/pi/k3s-monitoring/k8s-monitor.log 2>&1
"""

import subprocess
import sys
import os
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone

from dotenv import load_dotenv

# ── Config — values come from .env / environment variables ───────────────────

load_dotenv()

GROQ_API_KEY    = os.environ.get("GROQ_API_KEY",    "YOUR_GROQ_API_KEY_HERE")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "YOUR_DISCORD_WEBHOOK_URL_HERE")

# Groq free tier — fast and free for small models.
# Alternatives: "llama-3.1-8b-instant" (faster), "mixtral-8x7b-32768" (smarter)
GROQ_MODEL = "llama-3.3-70b-versatile"

# Discord embed colors (decimal values, 0xRRGGBB).
DISCORD_COLORS = {
    "green": 0x2ECC71,
    "red": 0xE74C3C,
    "amber": 0xF1C40F,
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def run_kubectl(args: list[str]) -> tuple[str, str]:
    """Run a kubectl command and return (stdout, stderr)."""
    try:
        result = subprocess.run(
            ["kubectl"] + args,
            capture_output=True, text=True, timeout=30
        )
        return result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return "", "kubectl not found. Is it installed and in PATH?"
    except subprocess.TimeoutExpired:
        return "", f"kubectl {' '.join(args)} timed out after 30s"


def get_server_version() -> str:
    """Return the k8s/k3s server version (e.g. 'v1.36.2+k3s1') or '' if unavailable."""
    out, _ = run_kubectl(["version", "--output=json"])
    if not out:
        return ""
    try:
        return json.loads(out).get("serverVersion", {}).get("gitVersion", "")
    except json.JSONDecodeError:
        return ""


def gather_cluster_state() -> dict:
    """Collect the relevant cluster state for analysis."""
    state = {}

    # Nodes
    out, err = run_kubectl(["get", "nodes", "-o", "wide"])
    state["nodes"] = out or err
    state["kubectl_ok"] = bool(out)
    node_total = node_ready = 0
    for line in out.splitlines():
        fields = line.split()
        if fields and fields[0] == "NAME":
            continue
        if len(fields) >= 2:
            node_total += 1
            if fields[1].startswith("Ready"):
                node_ready += 1
    state["node_total"] = node_total
    state["node_ready"] = node_ready

    # All pods across namespaces — filter to non-Running/Completed only
    out, _ = run_kubectl([
        "get", "pods", "--all-namespaces",
        "--field-selector=status.phase!=Running,status.phase!=Succeeded"
    ])
    pod_lines = [l for l in out.splitlines() if l.strip()]
    state["non_running_pods"] = "\n".join(pod_lines) if pod_lines else "None"
    state["non_running_count"] = max(len(pod_lines) - 1, 0)

    # CrashLoopBackOff / Error pods (broader check)
    out, _ = run_kubectl([
        "get", "pods", "--all-namespaces", "-o",
        "jsonpath={range .items[*]}{.metadata.namespace}{'\\t'}{.metadata.name}{'\\t'}"
        "{range .status.containerStatuses[*]}{.state.waiting.reason}{'\\n'}{end}{end}"
    ])
    crash_lines = [
        line for line in out.splitlines()
        if any(r in line for r in ["CrashLoopBackOff", "OOMKilled", "Error", "ImagePullBackOff"])
    ]
    state["crashlooping"] = "\n".join(crash_lines) if crash_lines else "None"
    state["crashloop_count"] = len(crash_lines)

    # Failed jobs — parse JSON because field-selectors on conditions are not
    # universally supported across Kubernetes versions.
    out, _ = run_kubectl(["get", "jobs", "--all-namespaces", "-o", "json"])
    failed_jobs = []
    if out:
        try:
            job_data = json.loads(out)
        except json.JSONDecodeError:
            print("⚠️  Failed to parse jobs JSON", file=sys.stderr)
            job_data = {"items": []}
        for job in job_data.get("items", []):
            for cond in job.get("status", {}).get("conditions", []):
                if cond.get("type") == "Failed" and cond.get("status") == "True":
                    ns = job["metadata"]["namespace"]
                    name = job["metadata"]["name"]
                    failed_jobs.append(f"{ns}\t{name}")
                    break
    state["failed_jobs"] = "\n".join(failed_jobs) if failed_jobs else "None"
    state["failed_jobs_count"] = len(failed_jobs)

    # Pending pods
    out, _ = run_kubectl([
        "get", "pods", "--all-namespaces",
        "--field-selector=status.phase=Pending"
    ])
    pending_lines = [l for l in out.splitlines() if l.strip()]
    state["pending_pods"] = "\n".join(pending_lines) if pending_lines else "None"
    state["pending_count"] = max(len(pending_lines) - 1, 0)

    # Recent events (warnings only, last 1h)
    out, _ = run_kubectl([
        "get", "events", "--all-namespaces",
        "--field-selector=type=Warning",
        "--sort-by=.lastTimestamp"
    ])
    # Trim to last 30 lines to keep the prompt short
    lines = out.splitlines()
    state["warning_events"] = "\n".join(lines[-30:]) if lines else "None"
    state["warning_count"] = len([
        l for l in lines
        if l.strip() and not l.strip().startswith("NAMESPACE")
        and "No resources" not in l
    ])

    state["server_version"] = get_server_version()

    return state


def ask_groq(prompt: str) -> str:
    """Send a prompt to Groq's free API and return the response text."""
    if not GROQ_API_KEY or GROQ_API_KEY == "YOUR_GROQ_API_KEY_HERE":
        return "⚠️  GROQ_API_KEY is not set. Cannot contact LLM."

    payload = json.dumps({
        "model": GROQ_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a Kubernetes SRE assistant. Analyze the provided cluster state "
                    "and produce a concise health report. Be specific about namespaces, pod names, "
                    "and job names. Do NOT suggest fixes unless explicitly asked — only report issues. "
                    "Ignore everything related to Recent warnings for DNS config forming in kube-system namespace. "
                    "Use bullet points. Keep it under 1500 characters so it fits in a Discord message."
                )
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
        "max_tokens": 512,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
            # Needed to pass Cloudflare bot protection on Groq's API.
            # Python's default urllib User-Agent triggers a 403/1010 error.
            "User-Agent": "k8s-monitor/1.0",
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
            return body["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        return f"⚠️  Groq API error {e.code}: {e.read().decode()}"
    except Exception as e:
        return f"⚠️  Groq request failed: {e}"


def send_discord_embed(embed: dict) -> None:
    """Post a rich embed message (dict) to a Discord webhook."""
    if not DISCORD_WEBHOOK or DISCORD_WEBHOOK == "YOUR_DISCORD_WEBHOOK_URL_HERE":
        print("⚠️  DISCORD_WEBHOOK is not set. Printing embed to stdout instead:\n")
        print(json.dumps({"embeds": [embed]}, indent=2))
        return

    payload = json.dumps({"embeds": [embed]}).encode("utf-8")
    req = urllib.request.Request(
        DISCORD_WEBHOOK,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "k8s-monitor/1.0",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status not in (200, 204):
                print(f"⚠️  Discord returned HTTP {resp.status}", file=sys.stderr)
    except Exception as e:
        print(f"⚠️  Failed to send Discord message: {e}", file=sys.stderr)


def footer_text(now: str, state: dict) -> str:
    """Build the embed footer, including the server version when available."""
    version = state.get("server_version", "")
    version_part = f" • k8s {version}" if version else ""
    return f"k8s-monitor{version_part} • ran at {now}"


def build_summary_fields(state: dict) -> list:
    """Build Discord embed summary fields from the collected state counts."""
    return [
        {"name": "🖥️ Nodes", "value": f"{state['node_ready']}/{state['node_total']} Ready", "inline": True},
        {"name": "📦 Non-Running Pods", "value": str(state["non_running_count"]), "inline": True},
        {"name": "🔄 CrashLoops", "value": str(state["crashloop_count"]), "inline": True},
        {"name": "🚫 Failed Jobs", "value": str(state["failed_jobs_count"]), "inline": True},
        {"name": "⏳ Pending Pods", "value": str(state["pending_count"]), "inline": True},
        {"name": "⚠️ Warnings (1h)", "value": str(state["warning_count"]), "inline": True},
    ]


def has_issues(state: dict) -> bool:
    """Quick check: are there any obvious problems worth reporting?"""
    for key in ["non_running_pods", "crashlooping", "failed_jobs", "pending_pods"]:
        if state.get(key, "None") not in ("None", ""):
            return True
    return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"[{now}] Starting Kubernetes health check...")

    state = gather_cluster_state()

    # If kubectl itself failed, we can't trust the "healthy" signal — alert instead.
    if not state["kubectl_ok"]:
        print("❌  kubectl failed. Sending error report to Discord...")
        send_discord_embed({
            "title": "⚠️ K8s Health Check — Could Not Run",
            "description": (
                "**The health check could not collect cluster state.**\n\n"
                f"```\n{state['nodes']}\n```"
            ),
            "color": DISCORD_COLORS["amber"],
            "footer": {"text": footer_text(now, state)},
        })
        print("Done.")
        return

    # No issues found — post a nice green "all good" report instead of skipping.
    if not has_issues(state):
        print("✅  No issues detected. Sending healthy report to Discord...")
        send_discord_embed({
            "title": "✅ K8s Health Check",
            "description": (
                "**Status: All Systems Nominal**\n"
                "🟢 No cluster health issues detected.\n"
                "Health check completed successfully."
            ),
            "color": DISCORD_COLORS["green"],
            "fields": build_summary_fields(state),
            "footer": {"text": footer_text(now, state)},
        })
        print("Done.")
        return

    # Build the prompt — keep it concise to save tokens
    prompt = f"""Kubernetes cluster state as of {now}:

## Nodes
{state['nodes']}

## Non-Running / Non-Completed Pods
{state['non_running_pods']}

## CrashLooping / Error Containers
{state['crashlooping']}

## Failed Jobs
{state['failed_jobs']}

## Pending Pods
{state['pending_pods']}

## Recent Warning Events (last 30)
{state['warning_events']}

Summarize any issues found. If everything looks healthy, say so briefly.
"""

    print("Sending state to LLM for analysis...")
    analysis = ask_groq(prompt)

    print("Sending report to Discord...")
    send_discord_embed({
        "title": "🔴 K8s Health Report — Issues Found",
        "description": (
            f"**Cluster issues detected** as of {now}\n"
            f"Model: `{GROQ_MODEL}`\n\n"
            f"{analysis}"
        ),
        "color": DISCORD_COLORS["red"],
        "fields": build_summary_fields(state),
        "footer": {"text": footer_text(now, state)},
    })
    print("Done.")


if __name__ == "__main__":
    main()
