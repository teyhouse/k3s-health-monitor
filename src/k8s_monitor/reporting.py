def footer_text(now: str, state: dict) -> str:
    """Build the embed footer, including the server version when available."""
    version = state.get("server_version", "")
    version_part = f" • k8s {version}" if version else ""
    return f"k8s-monitor{version_part} • ran at {now}"


def _icon(failed: int) -> str:
    return "❌" if failed else "✅"


def _ratio_value(failed: int, total: int, label: str) -> str:
    return f"{_icon(failed)} {total - failed}/{total} {label}"


def build_summary_fields(state: dict) -> list:
    """Build Discord embed summary fields from collected state counts."""
    return [
        {
            "name": "🖥️ Nodes",
            "value": f"{state['node_ready']}/{state['node_total']} Ready",
            "inline": True,
        },
        {"name": "📦 Non-Running Pods", "value": str(state["non_running_count"]), "inline": True},
        {"name": "🔄 CrashLoops", "value": str(state["crashloop_count"]), "inline": True},
        {
            "name": "🧱 Degraded Workloads",
            "value": str(state["degraded_workload_count"]),
            "inline": True,
        },
        {"name": "🚫 Failed Jobs", "value": str(state["failed_jobs_count"]), "inline": True},
        {"name": "⏳ Pending Pods", "value": str(state["pending_count"]), "inline": True},
        {"name": "⚠️ Warnings (1h)", "value": str(state["warning_count"]), "inline": True},
        {
            "name": "🛟 Backup Issues (24h)",
            "value": str(state["velero_failed_count"]),
            "inline": True,
        },
        {
            "name": "🔏 Cert Issues",
            "value": str(state["expired_cert_count"]),
            "inline": True,
        },
        {
            "name": "💾 Node Pressure",
            "value": str(state["node_pressure_count"]),
            "inline": True,
        },
        {
            "name": "🌐 ExternalDNS-Health",
            "value": (
                "✅ OK"
                if state["externaldns_ok"]
                else f"❌ FAIL ({state['externaldns_resolved'] or 'no answer'})"
            ),
            "inline": True,
        },
        {
            "name": "💽 Disk Usage",
            "value": (
                "n/a"
                if state["disk_max_percent"] is None
                else f"{_icon(state['disk_issue_count'])} {state['disk_max_percent']}%"
            ),
            "inline": True,
        },
        {
            "name": "🔑 K3s Cert",
            "value": (
                "n/a"
                if state["k3s_cert_days_left"] is None
                else f"{_icon(state['k3s_cert_issue'] != 'None')} {state['k3s_cert_days_left']}d"
            ),
            "inline": True,
        },
        {
            "name": "🐘 Postgres",
            "value": _ratio_value(state["cnpg_unhealthy_count"], state["cnpg_total"], "Healthy"),
            "inline": True,
        },
        {
            "name": "🔁 Flux",
            "value": _ratio_value(state["flux_failed_count"], state["flux_total"], "Ready"),
            "inline": True,
        },
        {
            "name": "🔐 SealedSecrets",
            "value": _ratio_value(
                state["sealed_secrets_failed_count"], state["sealed_secrets_total"], "Synced"
            ),
            "inline": True,
        },
    ]


def has_issues(state: dict) -> bool:
    """Quick check: are there any obvious problems worth reporting?"""
    keys = [
        "non_running_pods",
        "crashlooping",
        "degraded_workloads",
        "failed_jobs",
        "pending_pods",
        "velero_failed_backups",
        "expired_certs",
        "node_pressure",
        "disk_issues",
        "k3s_cert_issue",
        "externaldns_status",
        "flux_failed",
        "cnpg_issues",
        "sealed_secrets_failed",
    ]
    for key in keys:
        if state.get(key, "None") not in ("None", ""):
            return True
    return False


def build_prompt(state: dict, now: str) -> str:
    """Build the LLM prompt from collected cluster state."""
    return f"""Kubernetes cluster state as of {now}:

## Nodes
{state["nodes"]}

## Node Pressure Conditions (DiskPressure, MemoryPressure, PIDPressure)
{state["node_pressure"]}

## Node Disk Usage Above Threshold
{state["disk_issues"]}

## k3s API Server Certificate (k3s rotates it on restart when close to expiry)
{state["k3s_cert_issue"]}

## Non-Running / Non-Completed Pods
{state["non_running_pods"]}

## CrashLooping / Error Containers
{state["crashlooping"]}

## Degraded Workloads (Deployments, StatefulSets, DaemonSets with fewer ready replicas than desired)
{state["degraded_workloads"]}

## Failed Jobs
{state["failed_jobs"]}

## Pending Pods
{state["pending_pods"]}

## Velero Backups (failed, partially failed, or schedules without a backup in the last 24h)
{state["velero_failed_backups"]}

## Expired / Expiring Certificates
{state["expired_certs"]}

## Recent Warning Events (last 30)
{state["warning_events"]}

## ExternalDNS Health
{state["externaldns_status"]}

## Flux Resources Not Ready
{state["flux_failed"]}

## CloudNativePG Postgres Clusters
{state["cnpg_issues"]}

## SealedSecrets Not Synced
{state["sealed_secrets_failed"]}

Summarize any issues found. If everything looks healthy, say so briefly.
"""
