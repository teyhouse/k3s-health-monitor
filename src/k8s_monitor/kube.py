import base64
import json
import socket
import ssl
import sys
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from k8s_monitor import config
from utils.shell import run_cmd


def run_kubectl(args: list[str]) -> tuple[str, str]:
    """Run a kubectl command and return (stdout, stderr)."""
    return run_cmd("kubectl", args)


def run_dig(args: list[str]) -> tuple[str, str]:
    return run_cmd("dig", args, timeout=5)


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


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def gather_velero_backups() -> dict:
    """Collect failed Velero backups and schedules without a backup in VELERO_LOOKBACK_HOURS."""
    empty = {"velero_failed_backups": "None", "velero_failed_count": 0}
    if not config.VELERO_CHECK_ENABLED:
        return empty

    out, _ = run_kubectl(
        ["get", "backups.velero.io,schedules.velero.io", "--all-namespaces", "-o", "json"]
    )
    if not out:
        return empty

    try:
        velero_data = json.loads(out)
    except json.JSONDecodeError:
        print("⚠️  Failed to parse velero JSON", file=sys.stderr)
        return empty

    cutoff = datetime.now(UTC) - timedelta(hours=config.VELERO_LOOKBACK_HOURS)
    failed = []
    for item in velero_data.get("items", []):
        name = item["metadata"]["name"]
        status = item.get("status", {})
        created_dt = _parse_ts(item["metadata"].get("creationTimestamp", ""))

        if item.get("kind") == "Schedule":
            if item.get("spec", {}).get("paused") or not created_dt or created_dt >= cutoff:
                continue
            last_backup = _parse_ts(status.get("lastBackup", ""))
            if not last_backup or last_backup < cutoff:
                failed.append(
                    f"{name}\tschedule has no backup since {status.get('lastBackup', 'never')}"
                )
            continue

        if not created_dt or created_dt < cutoff:
            continue
        phase = status.get("phase")
        if phase not in config.VELERO_FAILED_PHASES:
            continue
        reason = status.get("failureReason") or f"{status.get('errors', 0)} errors"
        if len(reason) > 140:
            reason = reason[:140] + "…"
        failed.append(f"{name}\t{phase}: {reason}")

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


def get_k3s_cert_not_after() -> datetime | None:
    out, _ = run_kubectl(["config", "view", "--minify", "--raw", "-o", "json"])
    try:
        cluster = json.loads(out)["clusters"][0]["cluster"]
    except (json.JSONDecodeError, KeyError, IndexError):
        return None

    url = urlparse(cluster.get("server", ""))
    if url.scheme != "https" or not url.hostname:
        return None

    try:
        ca_data = cluster.get("certificate-authority-data")
        ctx = ssl.create_default_context(
            cafile=cluster.get("certificate-authority"),
            cadata=base64.b64decode(ca_data).decode() if ca_data else None,
        )
        ctx.check_hostname = False
        with socket.create_connection((url.hostname, url.port or 443), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=url.hostname) as tls:
                cert = tls.getpeercert()
        return datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), UTC)
    except (OSError, ValueError, KeyError) as exc:
        print(f"⚠️  Failed to read API server certificate: {exc}", file=sys.stderr)
        return None


def gather_k3s_cert() -> dict:
    empty = {"k3s_cert_issue": "None", "k3s_cert_days_left": None}
    if not config.K3S_CERT_CHECK_ENABLED:
        return empty

    not_after = get_k3s_cert_not_after()
    if not not_after:
        return empty

    days_left = (not_after - datetime.now(UTC)).days
    issue = "None"
    if days_left < config.K3S_CERT_WARNING_DAYS:
        issue = f"API server certificate expires {not_after:%Y-%m-%d %H:%M} UTC ({days_left}d left)"
    return {"k3s_cert_issue": issue, "k3s_cert_days_left": days_left}


def gather_disk_usage() -> dict:
    empty = {"disk_issues": "None", "disk_issue_count": 0, "disk_max_percent": None}
    if not config.DISK_CHECK_ENABLED:
        return empty

    out, _ = run_kubectl(["get", "nodes", "-o", "name"])
    nodes = [line.removeprefix("node/") for line in out.splitlines() if line.startswith("node/")]

    issues = []
    max_percent = None
    for node in nodes:
        out, _ = run_kubectl(["get", "--raw", f"/api/v1/nodes/{node}/proxy/stats/summary"])
        try:
            summary = json.loads(out)["node"]
        except (json.JSONDecodeError, KeyError):
            print(f"⚠️  Failed to read disk stats for node {node}", file=sys.stderr)
            continue

        seen = set()
        for label, fs in (
            ("nodefs", summary.get("fs", {})),
            ("imagefs", summary.get("runtime", {}).get("imageFs", {})),
        ):
            capacity = fs.get("capacityBytes")
            available = fs.get("availableBytes")
            if not capacity or available is None or (capacity, available) in seen:
                continue
            seen.add((capacity, available))
            percent = round(100 - available / capacity * 100)
            max_percent = percent if max_percent is None else max(max_percent, percent)
            if percent >= config.DISK_USAGE_WARNING_PERCENT:
                free_gib = available / 2**30
                issues.append(f"{node}\t{label}\t{percent}% used ({free_gib:.1f} GiB free)")

    return {
        "disk_issues": "\n".join(issues) if issues else "None",
        "disk_issue_count": len(issues),
        "disk_max_percent": max_percent,
    }


def gather_degraded_workloads() -> dict:
    out, _ = run_kubectl(
        ["get", "deployments,statefulsets,daemonsets", "--all-namespaces", "-o", "json"]
    )
    degraded = []
    if out:
        try:
            workload_data = json.loads(out)
        except json.JSONDecodeError:
            print("⚠️  Failed to parse workloads JSON", file=sys.stderr)
            workload_data = {"items": []}
        for item in workload_data.get("items", []):
            kind = item.get("kind", "")
            status = item.get("status", {})
            if kind == "DaemonSet":
                desired = status.get("desiredNumberScheduled", 0)
                ready = status.get("numberReady", 0)
            else:
                desired = item.get("spec", {}).get("replicas", 1)
                ready = status.get("readyReplicas", 0)
            if ready < desired:
                ns = item["metadata"]["namespace"]
                name = item["metadata"]["name"]
                degraded.append(f"{ns}\t{kind}/{name}\t{ready}/{desired} ready")

    return {
        "degraded_workloads": "\n".join(degraded) if degraded else "None",
        "degraded_workload_count": len(degraded),
    }


def gather_cnpg_status() -> dict:
    empty = {"cnpg_issues": "None", "cnpg_unhealthy_count": 0, "cnpg_total": 0}
    if not config.CNPG_CHECK_ENABLED:
        return empty

    kinds = (
        "clusters.postgresql.cnpg.io,backups.postgresql.cnpg.io,scheduledbackups.postgresql.cnpg.io"
    )
    out, _ = run_kubectl(["get", kinds, "--all-namespaces", "-o", "json"])
    if not out:
        return empty

    try:
        cnpg_data = json.loads(out)
    except json.JSONDecodeError:
        print("⚠️  Failed to parse CloudNativePG JSON", file=sys.stderr)
        return empty

    items = cnpg_data.get("items", [])
    clusters = [i for i in items if i.get("kind") == "Cluster"]
    cutoff = datetime.now(UTC) - timedelta(hours=config.CNPG_BACKUP_MAX_AGE_HOURS)

    last_completed = {}
    for backup in items:
        if backup.get("kind") != "Backup" or backup.get("status", {}).get("phase") != "completed":
            continue
        key = (
            backup["metadata"]["namespace"],
            backup.get("spec", {}).get("cluster", {}).get("name"),
        )
        stopped = _parse_ts(backup["status"].get("stoppedAt", ""))
        if stopped and (key not in last_completed or stopped > last_completed[key]):
            last_completed[key] = stopped

    issues = []
    unhealthy = set()
    for cluster in clusters:
        ns = cluster["metadata"]["namespace"]
        name = cluster["metadata"]["name"]
        status = cluster.get("status", {})
        if status.get("phase") != config.CNPG_HEALTHY_PHASE:
            issues.append(f"{ns}\t{name}\tphase: {status.get('phase', 'unknown')}")
            unhealthy.add((ns, name))
        for cond in status.get("conditions", []):
            if cond.get("type") in config.CNPG_CONDITIONS and cond.get("status") == "False":
                msg = cond.get("message", "unknown reason")
                if len(msg) > 100:
                    msg = msg[:100] + "…"
                issues.append(f"{ns}\t{name}\t{cond['type']}: {msg}")
                unhealthy.add((ns, name))

    for sched in items:
        if sched.get("kind") != "ScheduledBackup" or sched.get("spec", {}).get("suspend"):
            continue
        created = _parse_ts(sched["metadata"].get("creationTimestamp", ""))
        if not created or created >= cutoff:
            continue
        key = (sched["metadata"]["namespace"], sched.get("spec", {}).get("cluster", {}).get("name"))
        last = last_completed.get(key)
        if not last or last < cutoff:
            since = last.strftime("%Y-%m-%dT%H:%M:%SZ") if last else "never"
            issues.append(f"{key[0]}\t{key[1]}\tno completed backup since {since}")
            unhealthy.add(key)

    return {
        "cnpg_issues": "\n".join(issues) if issues else "None",
        "cnpg_unhealthy_count": len(unhealthy),
        "cnpg_total": len(clusters),
    }


def gather_flux_status() -> dict:
    empty = {"flux_failed": "None", "flux_failed_count": 0, "flux_total": 0}
    if not config.FLUX_CHECK_ENABLED:
        return empty

    out, _ = run_kubectl(["get", ",".join(config.FLUX_KINDS), "--all-namespaces", "-o", "json"])
    if not out:
        return empty

    try:
        flux_data = json.loads(out)
    except json.JSONDecodeError:
        print("⚠️  Failed to parse Flux resources JSON", file=sys.stderr)
        return empty

    items = flux_data.get("items", [])
    failed = []
    for item in items:
        for cond in item.get("status", {}).get("conditions", []):
            if cond.get("type") == "Ready" and cond.get("status") == "False":
                ns = item["metadata"]["namespace"]
                name = f"{item.get('kind', '')}/{item['metadata']['name']}"
                msg = cond.get("message", "unknown reason")
                if len(msg) > 100:
                    msg = msg[:100] + "…"
                failed.append(f"{ns}\t{name}\t{cond.get('reason', '')}: {msg}")

    return {
        "flux_failed": "\n".join(failed) if failed else "None",
        "flux_failed_count": len(failed),
        "flux_total": len(items),
    }


def gather_externaldns_check() -> dict:
    if not config.EXTERNALDNS_CHECK_ENABLED:
        return {"externaldns_ok": True, "externaldns_status": "None", "externaldns_resolved": ""}

    hostname = config.EXTERNALDNS_CHECK_HOSTNAME
    resolver = config.EXTERNALDNS_CHECK_RESOLVER
    expected = config.EXTERNALDNS_CHECK_EXPECTED_IP

    out, err = run_dig(["+short", hostname, f"@{resolver}"])
    resolved = out.splitlines()[0].strip() if out.strip() else ""

    if resolved == expected:
        return {
            "externaldns_ok": True,
            "externaldns_status": "None",
            "externaldns_resolved": resolved,
        }

    if not resolved:
        status = f"{hostname} via {resolver}: no answer" + (f" ({err})" if err else "")
    else:
        status = f"{hostname} via {resolver} resolved to {resolved}, expected {expected}"

    return {"externaldns_ok": False, "externaldns_status": status, "externaldns_resolved": resolved}


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

    # Node pressure conditions (DiskPressure, MemoryPressure, PIDPressure)
    out, _ = run_kubectl(
        [
            "get",
            "nodes",
            "-o",
            "jsonpath={range .items[*]}{.metadata.name}{'\\t'}"
            "{range .status.conditions[*]}{.type}{'='}{.status}{'\\t'}{end}{'\\n'}{end}",
        ]
    )
    pressure_lines = []
    for line in out.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        node_name = fields[0]
        for field in fields[1:]:
            for cond_type in ("DiskPressure", "MemoryPressure", "PIDPressure"):
                if cond_type in field and "=True" in field:
                    pressure_lines.append(f"{node_name}\t{cond_type}")
    state["node_pressure"] = "\n".join(pressure_lines) if pressure_lines else "None"
    state["node_pressure_count"] = len(pressure_lines)

    state.update(gather_disk_usage())

    state.update(gather_k3s_cert())

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
        if line.split("\t")[-1] in {"CrashLoopBackOff", "OOMKilled", "Error", "ImagePullBackOff"}
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

    state.update(gather_degraded_workloads())

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

    state.update(gather_cnpg_status())

    state.update(gather_flux_status())

    state.update(gather_externaldns_check())

    state["server_version"] = get_server_version()

    return state
