import json
import sys
from datetime import UTC, datetime, timedelta

from k8s_monitor import config
from utils.shell import run_cmd


def run_kubectl(args: list[str]) -> tuple[str, str]:
    """Run a kubectl command and return (stdout, stderr)."""
    return run_cmd("kubectl", args)


def run_velero(args: list[str]) -> tuple[str, str]:
    """Run a velero command and return (stdout, stderr)."""
    return run_cmd("velero", args)


def get_server_version() -> str:
    """Return the k8s/k3s server version (e.g. 'v1.36.2+k3s1') or '' if unavailable."""
    out, _ = run_kubectl(["version", "--output=json"])
    if not out:
        return ""
    try:
        return json.loads(out).get("serverVersion", {}).get("gitVersion", "")
    except json.JSONDecodeError:
        return ""


def _warning_is_filtered(line: str) -> bool:
    """True if the warning's REASON column is one we intentionally ignore."""
    fields = line.split()
    return len(fields) > 3 and fields[3] in config.WARNING_FILTER


def gather_velero_backups() -> dict:
    """Collect failed Velero backups created in the last VELERO_LOOKBACK_HOURS."""
    if not config.VELERO_CHECK_ENABLED:
        return {"velero_failed_backups": "None", "velero_failed_count": 0}

    out, _ = run_velero(["get", "backups", "-o", "json"])
    if not out:
        return {"velero_failed_backups": "None", "velero_failed_count": 0}

    try:
        backup_data = json.loads(out)
    except json.JSONDecodeError:
        print("⚠️  Failed to parse velero backups JSON", file=sys.stderr)
        return {"velero_failed_backups": "None", "velero_failed_count": 0}

    cutoff = datetime.now(UTC) - timedelta(hours=config.VELERO_LOOKBACK_HOURS)
    failed = []
    for backup in backup_data.get("items", []):
        created = backup.get("metadata", {}).get("creationTimestamp", "")
        try:
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            continue
        if created_dt < cutoff:
            continue
        if backup.get("status", {}).get("phase") != "Failed":
            continue
        name = backup["metadata"]["name"]
        reason = backup.get("status", {}).get("failureReason", "unknown reason") or "unknown reason"
        if len(reason) > 140:
            reason = reason[:140] + "…"
        failed.append(f"{name}\t{reason}")

    return {
        "velero_failed_backups": "\n".join(failed) if failed else "None",
        "velero_failed_count": len(failed),
    }


def gather_expired_certs() -> dict:
    """Collect expired, soon-to-expire, and not-ready cert-manager certificates."""
    if not config.CERT_CHECK_ENABLED:
        return {"expired_certs": "None", "expired_cert_count": 0}

    out, _ = run_kubectl(["get", "certificates.cert-manager.io", "--all-namespaces", "-o", "json"])
    if not out:
        return {"expired_certs": "None", "expired_cert_count": 0}

    try:
        cert_data = json.loads(out)
    except json.JSONDecodeError:
        print("⚠️  Failed to parse certificates JSON", file=sys.stderr)
        return {"expired_certs": "None", "expired_cert_count": 0}

    now = datetime.now(UTC)
    threshold = now + timedelta(days=config.CERT_EXPIRY_WARNING_DAYS)
    flagged = []
    for cert in cert_data.get("items", []):
        ns = cert["metadata"]["namespace"]
        name = cert["metadata"]["name"]
        status = cert.get("status", {})
        already_flagged = False

        not_after = status.get("notAfter", "")
        if not_after:
            try:
                expiry = datetime.fromisoformat(not_after.replace("Z", "+00:00"))
            except ValueError:
                continue
            if expiry < now:
                flagged.append(f"{ns}\t{name}\tEXPIRED {not_after}")
                already_flagged = True
            elif expiry < threshold:
                days_left = (expiry - now).days
                flagged.append(f"{ns}\t{name}\tEXPIRES in {days_left}d ({not_after})")
                already_flagged = True

        # Also flag certs with Ready=False — catches renewal failures and
        # never-issued certs even before notAfter is reached. Skip if already
        # flagged by the expiry check above to avoid duplicates.
        if already_flagged:
            continue
        for cond in status.get("conditions", []):
            if cond.get("type") == "Ready" and cond.get("status") == "False":
                msg = cond.get("message", "unknown reason")
                if len(msg) > 100:
                    msg = msg[:100] + "…"
                flagged.append(f"{ns}\t{name}\tNOT READY: {msg}")

    return {
        "expired_certs": "\n".join(flagged) if flagged else "None",
        "expired_cert_count": len(flagged),
    }


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
    out, _ = run_kubectl(
        [
            "get",
            "pods",
            "--all-namespaces",
            "--field-selector=status.phase!=Running,status.phase!=Succeeded",
        ]
    )
    pod_lines = [line for line in out.splitlines() if line.strip()]
    state["non_running_pods"] = "\n".join(pod_lines) if pod_lines else "None"
    state["non_running_count"] = max(len(pod_lines) - 1, 0)

    # CrashLoopBackOff / Error pods (broader check)
    out, _ = run_kubectl(
        [
            "get",
            "pods",
            "--all-namespaces",
            "-o",
            "jsonpath={range .items[*]}{.metadata.namespace}{'\\t'}{.metadata.name}{'\\t'}"
            "{range .status.containerStatuses[*]}{.state.waiting.reason}{'\\n'}{end}{end}",
        ]
    )
    crash_lines = [
        line
        for line in out.splitlines()
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
    out, _ = run_kubectl(
        ["get", "pods", "--all-namespaces", "--field-selector=status.phase=Pending"]
    )
    pending_lines = [line for line in out.splitlines() if line.strip()]
    state["pending_pods"] = "\n".join(pending_lines) if pending_lines else "None"
    state["pending_count"] = max(len(pending_lines) - 1, 0)

    # Recent events (warnings only, last 1h)
    out, _ = run_kubectl(
        [
            "get",
            "events",
            "--all-namespaces",
            "--field-selector=type=Warning",
            "--sort-by=.lastTimestamp",
        ]
    )
    warning_lines = [
        line
        for line in out.splitlines()
        if line.strip()
        and not line.strip().startswith("NAMESPACE")
        and "No resources" not in line
        and not _warning_is_filtered(line)
    ]
    # Trim to last 30 lines to keep the prompt short
    state["warning_events"] = "\n".join(warning_lines[-30:]) if warning_lines else "None"
    state["warning_count"] = len(warning_lines)

    # Failed Velero backups (last 24h)
    state.update(gather_velero_backups())

    # Expired / expiring cert-manager certificates
    state.update(gather_expired_certs())

    state["server_version"] = get_server_version()

    return state
