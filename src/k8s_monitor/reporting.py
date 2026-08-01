def footer_text(now: str, state: dict) -> str:
    """Build the embed footer, including the server version when available."""
    version = state.get("server_version", "")
    version_part = f" • k8s {version}" if version else ""
    return f"k8s-monitor{version_part} • ran at {now}"


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
        {"name": "🚫 Failed Jobs", "value": str(state["failed_jobs_count"]), "inline": True},
        {"name": "⏳ Pending Pods", "value": str(state["pending_count"]), "inline": True},
        {"name": "⚠️ Warnings (1h)", "value": str(state["warning_count"]), "inline": True},
        {
            "name": "🛟 Failed Backups (24h)",
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
    ]


def has_issues(state: dict) -> bool:
    """Quick check: are there any obvious problems worth reporting?"""
    keys = [
        "non_running_pods",
        "crashlooping",
        "failed_jobs",
        "pending_pods",
        "velero_failed_backups",
        "expired_certs",
        "node_pressure",
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

## Non-Running / Non-Completed Pods
{state["non_running_pods"]}

## CrashLooping / Error Containers
{state["crashlooping"]}

## Failed Jobs
{state["failed_jobs"]}

## Pending Pods
{state["pending_pods"]}

## Velero Backups (last 24h)
{state["velero_failed_backups"]}

## Expired / Expiring Certificates
{state["expired_certs"]}

## Recent Warning Events (last 30)
{state["warning_events"]}

Summarize any issues found. If everything looks healthy, say so briefly.
"""
