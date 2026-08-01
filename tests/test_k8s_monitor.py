import json

import pytest

HEALTHY_STATE = {
    "nodes": "NAME STATUS ROLES\nnode1 Ready control-plane",
    "kubectl_ok": True,
    "node_total": 1,
    "node_ready": 1,
    "non_running_pods": "None",
    "non_running_count": 0,
    "crashlooping": "None",
    "crashloop_count": 0,
    "failed_jobs": "None",
    "failed_jobs_count": 0,
    "pending_pods": "None",
    "pending_count": 0,
    "warning_events": "None",
    "warning_count": 0,
    "server_version": "v1.36.2+k3s1",
}


def fake_kubectl(args):
    """Simulate kubectl output for the commands gather_cluster_state() runs."""
    joined = " ".join(args)
    if "get nodes" in joined:
        return "NAME STATUS ROLES\nnode1 Ready control-plane\nnode2 Ready worker\n", ""
    if "jsonpath" in joined:
        return "default\tpod-1\tCrashLoopBackOff\n", ""
    if "get jobs" in joined:
        jobs = {
            "items": [
                {
                    "metadata": {"namespace": "batch", "name": "job-ok"},
                    "status": {"conditions": []},
                },
                {
                    "metadata": {"namespace": "batch", "name": "job-failed"},
                    "status": {"conditions": [{"type": "Failed", "status": "True"}]},
                },
            ]
        }
        return json.dumps(jobs), ""
    if "get events" in joined:
        return (
            "NAMESPACE LAST SEEN TYPE REASON OBJECT MESSAGE\n"
            "kube-system 1m Warning FailedScheduling pod/x 0/1 nodes available\n"
            "default 2m Warning BackOff pod/y restart loop\n",
            "",
        )
    if "status.phase=Pending" in joined:
        return "NAMESPACE NAME READY STATUS RESTARTS AGE\ndefault pod-2 0/1 Pending 0 1m\n", ""
    if "status.phase!=Running" in joined:
        return (
            "NAMESPACE NAME READY STATUS RESTARTS AGE\ndefault pod-1 0/1 CrashLoopBackOff 3 5m\n",
            "",
        )
    if "version" in joined:
        return json.dumps({"serverVersion": {"gitVersion": "v1.36.2+k3s1"}}), ""
    return "", ""


# ── has_issues ──────────────────────────────────────────────────────────────


def test_has_issues_returns_false_for_healthy_state(km):
    assert km.has_issues(HEALTHY_STATE) is False


def test_has_issues_detects_problems(km):
    state = dict(HEALTHY_STATE)
    state["pending_pods"] = "default/pod-2"
    assert km.has_issues(state) is True


# ── footer_text ─────────────────────────────────────────────────────────────


def test_footer_text_includes_server_version(km):
    expected = "k8s-monitor • k8s v1.36.2+k3s1 • ran at 2026-08-01 08:00 UTC"
    assert km.footer_text("2026-08-01 08:00 UTC", HEALTHY_STATE) == expected


def test_footer_text_omits_version_when_unavailable(km):
    state = dict(HEALTHY_STATE, server_version="")
    assert "k8s v1.36.2+k3s1" not in km.footer_text("2026-08-01 08:00 UTC", state)


# ── build_summary_fields ────────────────────────────────────────────────────


def test_build_summary_fields(km):
    fields = km.build_summary_fields(HEALTHY_STATE)
    assert len(fields) == 6
    assert all(field["inline"] for field in fields)
    by_name = {field["name"]: field["value"] for field in fields}
    assert by_name["🖥️ Nodes"] == "1/1 Ready"
    assert by_name["📦 Non-Running Pods"] == "0"
    assert by_name["⚠️ Warnings (1h)"] == "0"


# ── get_server_version ──────────────────────────────────────────────────────


def test_get_server_version_parses_git_version(km, monkeypatch):
    monkeypatch.setattr(
        km,
        "run_kubectl",
        lambda args: (json.dumps({"serverVersion": {"gitVersion": "v1.36.2+k3s1"}}), ""),
    )
    assert km.get_server_version() == "v1.36.2+k3s1"


def test_get_server_version_returns_empty_on_no_output(km, monkeypatch):
    monkeypatch.setattr(km, "run_kubectl", lambda args: ("", ""))
    assert km.get_server_version() == ""


def test_get_server_version_returns_empty_on_invalid_json(km, monkeypatch):
    monkeypatch.setattr(km, "run_kubectl", lambda args: ("not json", ""))
    assert km.get_server_version() == ""


# ── gather_cluster_state ────────────────────────────────────────────────────


def test_gather_cluster_state_counts(km, monkeypatch):
    monkeypatch.setattr(km, "run_kubectl", fake_kubectl)
    state = km.gather_cluster_state()

    assert state["kubectl_ok"] is True
    assert state["node_total"] == 2
    assert state["node_ready"] == 2
    assert state["non_running_count"] == 1
    assert state["crashloop_count"] == 1
    assert state["failed_jobs_count"] == 1
    assert state["pending_count"] == 1
    assert state["warning_count"] == 2
    assert state["server_version"] == "v1.36.2+k3s1"


def test_gather_cluster_state_healthy_outputs(km, monkeypatch):
    monkeypatch.setattr(km, "run_kubectl", lambda args: ("", ""))
    state = km.gather_cluster_state()

    assert state["kubectl_ok"] is False
    assert state["non_running_pods"] == "None"
    assert state["crashlooping"] == "None"
    assert state["failed_jobs"] == "None"
    assert state["pending_pods"] == "None"


def test_gather_cluster_state_failed_jobs_invalid_json(km, monkeypatch):
    def fake(args):
        if "get jobs" in " ".join(args):
            return "not json", ""
        return "", ""

    monkeypatch.setattr(km, "run_kubectl", fake)
    state = km.gather_cluster_state()
    assert state["failed_jobs"] == "None"
    assert state["failed_jobs_count"] == 0


# ── run_kubectl ─────────────────────────────────────────────────────────────


def test_run_kubectl_file_not_found(km, monkeypatch):
    def boom(args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(km.subprocess, "run", boom)
    out, err = km.run_kubectl(["get", "nodes"])
    assert out == ""
    assert "kubectl not found" in err


def test_run_kubectl_timeout(km, monkeypatch):
    def boom(args, **kwargs):
        raise km.subprocess.TimeoutExpired(cmd=args, timeout=30)

    monkeypatch.setattr(km.subprocess, "run", boom)
    out, err = km.run_kubectl(["get", "nodes"])
    assert out == ""
    assert "timed out" in err


# ── ask_groq ────────────────────────────────────────────────────────────────


def test_ask_groq_returns_warning_when_key_missing(km, monkeypatch):
    monkeypatch.setattr(km, "GROQ_API_KEY", "YOUR_GROQ_API_KEY_HERE")
    result = km.ask_groq("prompt")
    assert "GROQ_API_KEY" in result
    assert "not set" in result


# ── send_discord_embed ──────────────────────────────────────────────────────


def test_send_discord_embed_prints_when_webhook_missing(km, monkeypatch, capsys):
    monkeypatch.setattr(km, "DISCORD_WEBHOOK", "YOUR_DISCORD_WEBHOOK_URL_HERE")
    km.send_discord_embed({"title": "Test", "color": 1})
    out = capsys.readouterr().out
    assert '"embeds"' in out


# ── main() report selection ─────────────────────────────────────────────────


def test_main_posts_green_embed_when_healthy(km, monkeypatch):
    monkeypatch.setattr(km, "gather_cluster_state", lambda: dict(HEALTHY_STATE))
    monkeypatch.setattr(km, "ask_groq", lambda prompt: pytest.fail("LLM must not be called"))
    sent = []
    monkeypatch.setattr(km, "send_discord_embed", sent.append)

    km.main()

    assert len(sent) == 1
    embed = sent[0]
    assert embed["title"] == "✅ K8s Health Check"
    assert embed["color"] == km.DISCORD_COLORS["green"]
    assert embed["fields"]
    assert "k8s v1.36.2+k3s1" in embed["footer"]["text"]


def test_main_posts_red_embed_when_issues_found(km, monkeypatch):
    state = dict(HEALTHY_STATE)
    state["pending_pods"] = "default/pod-2"
    monkeypatch.setattr(km, "gather_cluster_state", lambda: state)
    prompts = []
    monkeypatch.setattr(
        km,
        "ask_groq",
        lambda prompt: prompts.append(prompt) or "- pod-2 unschedulable",
    )
    sent = []
    monkeypatch.setattr(km, "send_discord_embed", sent.append)

    km.main()

    assert prompts, "LLM should be called when issues exist"
    assert "## Pending Pods" in prompts[0]
    assert len(sent) == 1
    embed = sent[0]
    assert embed["title"] == "🔴 K8s Health Report — Issues Found"
    assert embed["color"] == km.DISCORD_COLORS["red"]
    assert "- pod-2 unschedulable" in embed["description"]


def test_main_posts_amber_embed_when_kubectl_fails(km, monkeypatch):
    state = dict(
        HEALTHY_STATE,
        kubectl_ok=False,
        nodes="kubectl not found. Is it installed and in PATH?",
    )
    monkeypatch.setattr(km, "gather_cluster_state", lambda: state)
    monkeypatch.setattr(km, "ask_groq", lambda prompt: pytest.fail("LLM must not be called"))
    sent = []
    monkeypatch.setattr(km, "send_discord_embed", sent.append)

    km.main()

    assert len(sent) == 1
    embed = sent[0]
    assert embed["title"] == "⚠️ K8s Health Check — Could Not Run"
    assert embed["color"] == km.DISCORD_COLORS["amber"]
