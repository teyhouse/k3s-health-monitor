import json

import pytest

from k8s_monitor import config, discord_client, groq, kube, orchestrate, reporting
from utils import shell

HEALTHY_STATE = {
    "nodes": "NAME STATUS ROLES\nnode1 Ready control-plane",
    "kubectl_ok": True,
    "node_total": 1,
    "node_ready": 1,
    "non_running_pods": "None",
    "non_running_count": 0,
    "crashlooping": "None",
    "crashloop_count": 0,
    "degraded_workloads": "None",
    "degraded_workload_count": 0,
    "failed_jobs": "None",
    "failed_jobs_count": 0,
    "pending_pods": "None",
    "pending_count": 0,
    "warning_events": "None",
    "warning_count": 0,
    "velero_failed_backups": "None",
    "velero_failed_count": 0,
    "expired_certs": "None",
    "expired_cert_count": 0,
    "node_pressure": "None",
    "node_pressure_count": 0,
    "disk_issues": "None",
    "disk_issue_count": 0,
    "disk_max_percent": 53,
    "k3s_cert_issue": "None",
    "k3s_cert_days_left": 313,
    "externaldns_ok": True,
    "externaldns_status": "None",
    "externaldns_resolved": "192.168.10.2",
    "flux_failed": "None",
    "flux_failed_count": 0,
    "flux_total": 47,
    "cnpg_issues": "None",
    "cnpg_unhealthy_count": 0,
    "cnpg_total": 1,
    "sealed_secrets_failed": "None",
    "sealed_secrets_failed_count": 0,
    "sealed_secrets_total": 16,
    "server_version": "v1.36.2+k3s1",
}


def fake_velero(args, now=None):
    """Simulate velero backups and schedules: recent/old, failed/partial/completed, stale."""
    from datetime import UTC, datetime, timedelta

    now = now or datetime.now(UTC)

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    backups = {
        "items": [
            {
                "kind": "Backup",
                "metadata": {
                    "name": "daily-app-recent",
                    "creationTimestamp": iso(now - timedelta(hours=2)),
                },
                "status": {
                    "phase": "Failed",
                    "failureReason": "error putting object: Header 'x-amz-tagging' not implemented",
                },
            },
            {
                "kind": "Backup",
                "metadata": {
                    "name": "test-backup-recent",
                    "creationTimestamp": iso(now - timedelta(hours=1)),
                },
                "status": {"phase": "Completed", "failureReason": None},
            },
            {
                "kind": "Backup",
                "metadata": {
                    "name": "daily-app-old",
                    "creationTimestamp": iso(now - timedelta(hours=48)),
                },
                "status": {"phase": "Failed", "failureReason": "old failure"},
            },
            {
                "kind": "Backup",
                "metadata": {
                    "name": "daily-db-partial",
                    "creationTimestamp": iso(now - timedelta(hours=3)),
                },
                "status": {"phase": "PartiallyFailed", "errors": 2},
            },
            {
                "kind": "Schedule",
                "metadata": {
                    "name": "daily-fresh",
                    "creationTimestamp": iso(now - timedelta(days=30)),
                },
                "status": {"lastBackup": iso(now - timedelta(hours=6))},
            },
            {
                "kind": "Schedule",
                "metadata": {
                    "name": "daily-stale",
                    "creationTimestamp": iso(now - timedelta(days=30)),
                },
                "status": {"lastBackup": iso(now - timedelta(hours=50))},
            },
            {
                "kind": "Schedule",
                "metadata": {
                    "name": "paused-stale",
                    "creationTimestamp": iso(now - timedelta(days=30)),
                },
                "spec": {"paused": True},
                "status": {"lastBackup": iso(now - timedelta(days=10))},
            },
        ]
    }
    return json.dumps(backups), ""


def fake_certs(args, now=None):
    """Simulate cert-manager Certificate output: expired, expiring, healthy, not-ready."""
    from datetime import UTC, datetime, timedelta

    now = now or datetime.now(UTC)

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    certs = {
        "items": [
            {
                "metadata": {"namespace": "istio-system", "name": "expired-cert"},
                "status": {
                    "notAfter": iso(now - timedelta(days=10)),
                    "conditions": [
                        {"type": "Ready", "status": "False", "message": "Certificate has expired"}
                    ],
                },
            },
            {
                "metadata": {"namespace": "default", "name": "expiring-soon"},
                "status": {
                    "notAfter": iso(now + timedelta(days=3)),
                    "conditions": [
                        {"type": "Ready", "status": "True", "message": "Certificate is up to date"}
                    ],
                },
            },
            {
                "metadata": {"namespace": "kube-system", "name": "healthy-cert"},
                "status": {
                    "notAfter": iso(now + timedelta(days=60)),
                    "conditions": [
                        {"type": "Ready", "status": "True", "message": "Certificate is up to date"}
                    ],
                },
            },
            {
                "metadata": {"namespace": "default", "name": "not-ready-cert"},
                "status": {
                    "notAfter": iso(now + timedelta(days=30)),
                    "conditions": [
                        {
                            "type": "Ready",
                            "status": "False",
                            "message": "Issuing certificate as secret does not exist",
                        }
                    ],
                },
            },
        ]
    }
    return json.dumps(certs), ""


def fake_kubectl(args):
    """Simulate kubectl output for the commands gather_cluster_state() runs."""
    joined = " ".join(args)
    if "get nodes" in joined:
        if "jsonpath" in joined:
            return (
                "node1\tReady=True\tDiskPressure=False\tMemoryPressure=False\tPIDPressure=False\n"
                "node2\tReady=True\tDiskPressure=False\tMemoryPressure=False\tPIDPressure=False\n",
                "",
            )
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
    if "certificates.cert-manager.io" in joined:
        return fake_certs(args)
    if "deployments,statefulsets,daemonsets" in joined:
        workloads = {
            "items": [
                {
                    "kind": "Deployment",
                    "metadata": {"namespace": "auth", "name": "dex"},
                    "spec": {"replicas": 1},
                    "status": {"readyReplicas": 1},
                },
                {
                    "kind": "Deployment",
                    "metadata": {"namespace": "m2m-api", "name": "m2m-api"},
                    "spec": {"replicas": 1},
                    "status": {},
                },
                {
                    "kind": "Deployment",
                    "metadata": {"namespace": "n8n", "name": "n8n"},
                    "spec": {"replicas": 0},
                    "status": {},
                },
                {
                    "kind": "DaemonSet",
                    "metadata": {"namespace": "kube-system", "name": "cilium"},
                    "status": {"desiredNumberScheduled": 2, "numberReady": 1},
                },
            ]
        }
        return json.dumps(workloads), ""
    if "fluxcd.io" in joined:
        flux = {
            "items": [
                {
                    "kind": "Kustomization",
                    "metadata": {"namespace": "flux-system", "name": "apps"},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                },
                {
                    "kind": "GitRepository",
                    "metadata": {"namespace": "flux-system", "name": "flux-system"},
                    "status": {
                        "conditions": [
                            {
                                "type": "Ready",
                                "status": "False",
                                "reason": "GitOperationFailed",
                                "message": "failed to checkout and determine revision",
                            }
                        ]
                    },
                },
                {
                    "kind": "HelmRelease",
                    "metadata": {"namespace": "velero", "name": "velero"},
                    "status": {"conditions": [{"type": "Ready", "status": "Unknown"}]},
                },
            ]
        }
        return json.dumps(flux), ""
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


def test_has_issues_returns_false_for_healthy_state():
    assert reporting.has_issues(HEALTHY_STATE) is False


def test_has_issues_detects_problems():
    state = dict(HEALTHY_STATE)
    state["pending_pods"] = "default/pod-2"
    assert reporting.has_issues(state) is True


def test_has_issues_detects_failed_backups():
    state = dict(HEALTHY_STATE)
    state["velero_failed_backups"] = "daily-app\terror putting object"
    assert reporting.has_issues(state) is True


def test_has_issues_detects_expired_certs():
    state = dict(HEALTHY_STATE)
    state["expired_certs"] = "istio-system\texpired-cert\tEXPIRED 2026-07-22T00:00:00Z"
    assert reporting.has_issues(state) is True


def test_has_issues_detects_node_pressure():
    state = dict(HEALTHY_STATE)
    state["node_pressure"] = "node1\tDiskPressure"
    assert reporting.has_issues(state) is True


def test_has_issues_detects_externaldns_failure():
    state = dict(HEALTHY_STATE)
    state["externaldns_status"] = (
        "auth.fulda-cloud.com via 192.168.237.99 resolved to 195.201.217.35, expected 192.168.10.2"
    )
    assert reporting.has_issues(state) is True


def test_has_issues_detects_flux_failure():
    state = dict(HEALTHY_STATE)
    state["flux_failed"] = "flux-system\tGitRepository/flux-system\tGitOperationFailed: x"
    assert reporting.has_issues(state) is True


# ── footer_text ─────────────────────────────────────────────────────────────


def test_footer_text_includes_server_version():
    expected = "k8s-monitor • k8s v1.36.2+k3s1 • ran at 2026-08-01 08:00 UTC"
    assert reporting.footer_text("2026-08-01 08:00 UTC", HEALTHY_STATE) == expected


def test_footer_text_omits_version_when_unavailable():
    state = dict(HEALTHY_STATE, server_version="")
    assert "k8s v1.36.2+k3s1" not in reporting.footer_text("2026-08-01 08:00 UTC", state)


# ── build_summary_fields ────────────────────────────────────────────────────


def test_build_summary_fields():
    fields = reporting.build_summary_fields(HEALTHY_STATE)
    assert len(fields) == 16
    assert all(field["inline"] for field in fields)
    by_name = {field["name"]: field["value"] for field in fields}
    assert by_name["🖥️ Nodes"] == "1/1 Ready"
    assert by_name["📦 Non-Running Pods"] == "0"
    assert by_name["⚠️ Warnings (1h)"] == "0"
    assert by_name["🛟 Backup Issues (24h)"] == "0"
    assert by_name["🔏 Cert Issues"] == "0"
    assert by_name["💾 Node Pressure"] == "0"
    assert by_name["🌐 ExternalDNS-Health"] == "✅ OK"
    assert by_name["🔁 Flux"] == "✅ 47/47 Ready"
    assert by_name["🐘 Postgres"] == "✅ 1/1 Healthy"
    assert by_name["💽 Disk Usage"] == "✅ 53%"
    assert by_name["🔑 K3s Cert"] == "✅ 313d"
    assert by_name["🔐 SealedSecrets"] == "✅ 16/16 Synced"


def test_build_summary_fields_externaldns_failure():
    state = dict(HEALTHY_STATE, externaldns_ok=False, externaldns_resolved="195.201.217.35")
    fields = reporting.build_summary_fields(state)
    by_name = {field["name"]: field["value"] for field in fields}
    assert by_name["🌐 ExternalDNS-Health"] == "❌ FAIL (195.201.217.35)"


def test_build_summary_fields_flux_failure():
    state = dict(HEALTHY_STATE, flux_failed_count=2)
    by_name = {field["name"]: field["value"] for field in reporting.build_summary_fields(state)}
    assert by_name["🔁 Flux"] == "❌ 45/47 Ready"


# ── get_server_version ──────────────────────────────────────────────────────


def test_get_server_version_parses_git_version(monkeypatch):
    monkeypatch.setattr(
        kube,
        "run_kubectl",
        lambda args: (json.dumps({"serverVersion": {"gitVersion": "v1.36.2+k3s1"}}), ""),
    )
    assert kube.get_server_version() == "v1.36.2+k3s1"


def test_get_server_version_returns_empty_on_no_output(monkeypatch):
    monkeypatch.setattr(kube, "run_kubectl", lambda args: ("", ""))
    assert kube.get_server_version() == ""


def test_get_server_version_returns_empty_on_invalid_json(monkeypatch):
    monkeypatch.setattr(kube, "run_kubectl", lambda args: ("not json", ""))
    assert kube.get_server_version() == ""


# ── gather_cluster_state ────────────────────────────────────────────────────


def test_gather_cluster_state_counts(monkeypatch):
    monkeypatch.setattr(kube, "run_kubectl", fake_kubectl)
    monkeypatch.setattr(kube, "run_dig", lambda args: ("192.168.10.2\n", ""))
    state = kube.gather_cluster_state()

    assert state["kubectl_ok"] is True
    assert state["node_total"] == 2
    assert state["node_ready"] == 2
    assert state["non_running_count"] == 1
    assert state["crashloop_count"] == 1
    assert state["degraded_workload_count"] == 2
    assert "m2m-api\tDeployment/m2m-api\t0/1 ready" in state["degraded_workloads"]
    assert "DaemonSet/cilium\t1/2 ready" in state["degraded_workloads"]
    assert state["failed_jobs_count"] == 1
    assert state["pending_count"] == 1
    assert state["warning_count"] == 2
    assert state["velero_failed_count"] == 0
    assert state["expired_cert_count"] == 3
    assert state["node_pressure_count"] == 0
    assert state["externaldns_ok"] is True
    assert state["flux_total"] == 3
    assert state["flux_failed_count"] == 1
    assert "GitRepository/flux-system\tGitOperationFailed" in state["flux_failed"]
    assert state["server_version"] == "v1.36.2+k3s1"


def test_gather_cluster_state_healthy_outputs(monkeypatch):
    monkeypatch.setattr(kube, "run_kubectl", lambda args: ("", ""))
    monkeypatch.setattr(kube, "run_dig", lambda args: ("192.168.10.2\n", ""))
    state = kube.gather_cluster_state()

    assert state["kubectl_ok"] is False
    assert state["non_running_pods"] == "None"
    assert state["crashlooping"] == "None"
    assert state["failed_jobs"] == "None"
    assert state["pending_pods"] == "None"


def test_gather_cluster_state_filters_dnsconfigforming_warnings(monkeypatch):
    def fake(args):
        if "get events" in " ".join(args):
            return (
                "NAMESPACE LAST SEEN TYPE REASON OBJECT MESSAGE\n"
                "kube-system 1m Warning DNSConfigForming pod/coredns-x "
                "Nameserver limits were exceeded, some nameservers have been omitted, "
                "the applied nameserver line is: 2a01:4ff:ff00::add:2 "
                "2a01:4ff:ff00::add:1 185.12.64.1\n"
                "kube-system 2m Warning DNSConfigForming pod/cilium-y "
                "Nameserver limits were exceeded, some nameservers have been omitted, "
                "the applied nameserver line is: 2a01:4ff:ff00::add:2 "
                "2a01:4ff:ff00::add:1 185.12.64.1\n"
                "default 3m Warning BackOff pod/z restart loop\n",
                "",
            )
        return "", ""

    monkeypatch.setattr(kube, "run_kubectl", fake)
    monkeypatch.setattr(kube, "run_dig", lambda args: ("192.168.10.2\n", ""))
    state = kube.gather_cluster_state()

    assert state["warning_count"] == 1
    assert "DNSConfigForming" not in state["warning_events"]
    assert "BackOff" in state["warning_events"]


def test_warning_filter_ignores_dnsconfigforming():
    assert "DNSConfigForming" in config.WARNING_FILTER


def test_gather_cluster_state_failed_jobs_invalid_json(monkeypatch):
    def fake(args):
        if "get jobs" in " ".join(args):
            return "not json", ""
        return "", ""

    monkeypatch.setattr(kube, "run_kubectl", fake)
    monkeypatch.setattr(kube, "run_dig", lambda args: ("192.168.10.2\n", ""))
    state = kube.gather_cluster_state()
    assert state["failed_jobs"] == "None"
    assert state["failed_jobs_count"] == 0


# ── run_kubectl / run_dig (via utils.shell) ─────────────────────────────────


def test_run_kubectl_file_not_found(monkeypatch):
    def boom(args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(shell.subprocess, "run", boom)
    out, err = kube.run_kubectl(["get", "nodes"])
    assert out == ""
    assert "kubectl not found" in err


def test_run_kubectl_timeout(monkeypatch):
    def boom(args, **kwargs):
        raise shell.subprocess.TimeoutExpired(cmd=args, timeout=30)

    monkeypatch.setattr(shell.subprocess, "run", boom)
    out, err = kube.run_kubectl(["get", "nodes"])
    assert out == ""
    assert "timed out" in err


def test_run_dig_file_not_found(monkeypatch):
    def boom(args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(shell.subprocess, "run", boom)
    out, err = kube.run_dig(["+short", "auth.fulda-cloud.com"])
    assert out == ""
    assert "dig not found" in err


# ── velero ──────────────────────────────────────────────────────────────────


def test_gather_velero_backups_counts_recent_failures_and_stale_schedules(monkeypatch):
    monkeypatch.setattr(config, "VELERO_CHECK_ENABLED", True)
    monkeypatch.setattr(kube, "run_kubectl", fake_velero)
    result = kube.gather_velero_backups()

    assert result["velero_failed_count"] == 3
    assert "daily-app-recent\tFailed: error putting object" in result["velero_failed_backups"]
    assert "daily-db-partial\tPartiallyFailed: 2 errors" in result["velero_failed_backups"]
    assert "daily-stale\tschedule has no backup since" in result["velero_failed_backups"]
    assert "daily-app-old" not in result["velero_failed_backups"]
    assert "test-backup-recent" not in result["velero_failed_backups"]
    assert "daily-fresh" not in result["velero_failed_backups"]
    assert "paused-stale" not in result["velero_failed_backups"]


def test_gather_velero_backups_disabled_skips_call(monkeypatch):
    monkeypatch.setattr(config, "VELERO_CHECK_ENABLED", False)
    monkeypatch.setattr(kube, "run_kubectl", lambda args: pytest.fail("kubectl must not be called"))
    result = kube.gather_velero_backups()

    assert result == {"velero_failed_backups": "None", "velero_failed_count": 0}


def test_gather_velero_backups_empty_output(monkeypatch):
    monkeypatch.setattr(config, "VELERO_CHECK_ENABLED", True)
    monkeypatch.setattr(kube, "run_kubectl", lambda args: ("", ""))
    result = kube.gather_velero_backups()

    assert result == {"velero_failed_backups": "None", "velero_failed_count": 0}


def test_gather_velero_backups_invalid_json(monkeypatch):
    monkeypatch.setattr(config, "VELERO_CHECK_ENABLED", True)
    monkeypatch.setattr(kube, "run_kubectl", lambda args: ("not json", ""))
    result = kube.gather_velero_backups()

    assert result == {"velero_failed_backups": "None", "velero_failed_count": 0}


# ── cert-manager certificates ───────────────────────────────────────────────


def test_gather_expired_certs_counts_expired_and_expiring(monkeypatch):
    monkeypatch.setattr(config, "CERT_CHECK_ENABLED", True)
    monkeypatch.setattr(kube, "run_kubectl", fake_certs)
    result = kube.gather_expired_certs()

    # expired-cert (EXPIRED) + expiring-soon (EXPIRES in 3d) + not-ready-cert (NOT READY)
    assert result["expired_cert_count"] == 3
    assert "expired-cert" in result["expired_certs"]
    assert "EXPIRED" in result["expired_certs"]
    assert "expiring-soon" in result["expired_certs"]
    assert "EXPIRES in" in result["expired_certs"]
    assert "healthy-cert" not in result["expired_certs"]
    assert "not-ready-cert" in result["expired_certs"]
    assert "NOT READY" in result["expired_certs"]


def test_gather_expired_certs_disabled_skips_call(monkeypatch):
    monkeypatch.setattr(config, "CERT_CHECK_ENABLED", False)
    monkeypatch.setattr(kube, "run_kubectl", lambda args: pytest.fail("kubectl must not be called"))
    result = kube.gather_expired_certs()

    assert result == {"expired_certs": "None", "expired_cert_count": 0}


def test_gather_expired_certs_empty_output(monkeypatch):
    monkeypatch.setattr(config, "CERT_CHECK_ENABLED", True)
    monkeypatch.setattr(kube, "run_kubectl", lambda args: ("", ""))
    result = kube.gather_expired_certs()

    assert result == {"expired_certs": "None", "expired_cert_count": 0}


def test_gather_expired_certs_invalid_json(monkeypatch):
    monkeypatch.setattr(config, "CERT_CHECK_ENABLED", True)
    monkeypatch.setattr(kube, "run_kubectl", lambda args: ("not json", ""))
    result = kube.gather_expired_certs()

    assert result == {"expired_certs": "None", "expired_cert_count": 0}


# ── ExternalDNS check ───────────────────────────────────────────────────────


def test_gather_externaldns_check_matches_expected(monkeypatch):
    monkeypatch.setattr(config, "EXTERNALDNS_CHECK_ENABLED", True)
    monkeypatch.setattr(config, "EXTERNALDNS_CHECK_HOSTNAME", "auth.fulda-cloud.com")
    monkeypatch.setattr(config, "EXTERNALDNS_CHECK_RESOLVER", "192.168.237.99")
    monkeypatch.setattr(config, "EXTERNALDNS_CHECK_EXPECTED_IP", "192.168.10.2")
    monkeypatch.setattr(kube, "run_dig", lambda args: ("192.168.10.2\n", ""))

    result = kube.gather_externaldns_check()

    assert result["externaldns_ok"] is True
    assert result["externaldns_status"] == "None"
    assert result["externaldns_resolved"] == "192.168.10.2"


def test_gather_externaldns_check_mismatch(monkeypatch):
    monkeypatch.setattr(config, "EXTERNALDNS_CHECK_ENABLED", True)
    monkeypatch.setattr(config, "EXTERNALDNS_CHECK_HOSTNAME", "auth.fulda-cloud.com")
    monkeypatch.setattr(config, "EXTERNALDNS_CHECK_RESOLVER", "192.168.237.99")
    monkeypatch.setattr(config, "EXTERNALDNS_CHECK_EXPECTED_IP", "192.168.10.2")
    monkeypatch.setattr(kube, "run_dig", lambda args: ("195.201.217.35\n", ""))

    result = kube.gather_externaldns_check()

    assert result["externaldns_ok"] is False
    assert result["externaldns_resolved"] == "195.201.217.35"
    assert "195.201.217.35" in result["externaldns_status"]
    assert "192.168.10.2" in result["externaldns_status"]


def test_gather_externaldns_check_no_answer(monkeypatch):
    monkeypatch.setattr(config, "EXTERNALDNS_CHECK_ENABLED", True)
    monkeypatch.setattr(kube, "run_dig", lambda args: ("", "connection timed out"))

    result = kube.gather_externaldns_check()

    assert result["externaldns_ok"] is False
    assert result["externaldns_resolved"] == ""
    assert "no answer" in result["externaldns_status"]
    assert "connection timed out" in result["externaldns_status"]


def test_gather_externaldns_check_disabled_skips_call(monkeypatch):
    monkeypatch.setattr(config, "EXTERNALDNS_CHECK_ENABLED", False)
    monkeypatch.setattr(kube, "run_dig", lambda args: pytest.fail("dig must not be called"))

    result = kube.gather_externaldns_check()

    assert result == {
        "externaldns_ok": True,
        "externaldns_status": "None",
        "externaldns_resolved": "",
    }


def test_gather_k3s_cert_flags_expiring_certificate(monkeypatch):
    from datetime import UTC, datetime, timedelta

    not_after = datetime.now(UTC) + timedelta(days=10, hours=1)
    monkeypatch.setattr(config, "K3S_CERT_CHECK_ENABLED", True)
    monkeypatch.setattr(config, "K3S_CERT_WARNING_DAYS", 30)
    monkeypatch.setattr(kube, "get_k3s_cert_not_after", lambda: not_after)

    result = kube.gather_k3s_cert()

    assert result["k3s_cert_days_left"] == 10
    assert "(10d left)" in result["k3s_cert_issue"]
    assert reporting.has_issues(dict(HEALTHY_STATE, **result)) is True
    fields = reporting.build_summary_fields(dict(HEALTHY_STATE, **result))
    assert {f["name"]: f["value"] for f in fields}["🔑 K3s Cert"] == "❌ 10d"


def test_gather_disk_usage_flags_full_filesystems(monkeypatch):
    gib = 2**30
    stats = {
        "node1": {"fs": {"capacityBytes": 100 * gib, "availableBytes": 15 * gib}},
        "node2": {
            "fs": {"capacityBytes": 100 * gib, "availableBytes": 60 * gib},
            "runtime": {"imageFs": {"capacityBytes": 100 * gib, "availableBytes": 60 * gib}},
        },
    }

    def fake(args):
        if args[:2] == ["get", "nodes"]:
            return "node/node1\nnode/node2", ""
        node = args[2].split("/")[4]
        return json.dumps({"node": stats[node]}), ""

    monkeypatch.setattr(config, "DISK_CHECK_ENABLED", True)
    monkeypatch.setattr(config, "DISK_USAGE_WARNING_PERCENT", 80)
    monkeypatch.setattr(kube, "run_kubectl", fake)

    result = kube.gather_disk_usage()

    assert result["disk_issue_count"] == 1
    assert result["disk_max_percent"] == 85
    assert result["disk_issues"] == "node1\tnodefs\t85% used (15.0 GiB free)"
    fields = reporting.build_summary_fields(dict(HEALTHY_STATE, **result))
    assert {f["name"]: f["value"] for f in fields}["💽 Disk Usage"] == "❌ 85%"


def test_gather_cnpg_status_flags_unhealthy_and_stale_backups(monkeypatch):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def cluster(name, archiving):
        return {
            "kind": "Cluster",
            "metadata": {"namespace": "postgresql", "name": name},
            "status": {
                "phase": "Cluster in healthy state",
                "conditions": [
                    {"type": "Ready", "status": "True"},
                    {
                        "type": "ContinuousArchiving",
                        "status": archiving,
                        "message": "unexpected failure invoking barman-cloud-wal-archive",
                    },
                ],
            },
        }

    def scheduled(name, cluster_name):
        return {
            "kind": "ScheduledBackup",
            "metadata": {
                "namespace": "postgresql",
                "name": name,
                "creationTimestamp": iso(now - timedelta(days=30)),
            },
            "spec": {"cluster": {"name": cluster_name}},
        }

    def backup(cluster_name, age_hours):
        return {
            "kind": "Backup",
            "metadata": {"namespace": "postgresql", "name": f"{cluster_name}-{age_hours}"},
            "spec": {"cluster": {"name": cluster_name}},
            "status": {"phase": "completed", "stoppedAt": iso(now - timedelta(hours=age_hours))},
        }

    items = [
        cluster("healthy", "True"),
        cluster("broken", "False"),
        scheduled("healthy-daily", "healthy"),
        scheduled("broken-daily", "broken"),
        backup("healthy", 5),
        backup("broken", 50),
    ]
    monkeypatch.setattr(config, "CNPG_CHECK_ENABLED", True)
    monkeypatch.setattr(kube, "run_kubectl", lambda args: (json.dumps({"items": items}), ""))

    result = kube.gather_cnpg_status()

    assert result["cnpg_total"] == 2
    assert result["cnpg_unhealthy_count"] == 1
    assert "broken\tContinuousArchiving: unexpected failure" in result["cnpg_issues"]
    assert "broken\tno completed backup since" in result["cnpg_issues"]
    assert "\thealthy\t" not in result["cnpg_issues"]


def test_gather_sealed_secrets_flags_unsynced(monkeypatch):
    items = [
        {
            "metadata": {"namespace": "auth", "name": "dex-secrets"},
            "status": {"conditions": [{"type": "Synced", "status": "True"}]},
        },
        {
            "metadata": {"namespace": "n8n", "name": "n8n-env"},
            "status": {
                "conditions": [
                    {"type": "Synced", "status": "False", "message": "no key could decrypt secret"}
                ]
            },
        },
    ]
    monkeypatch.setattr(config, "SEALED_SECRETS_CHECK_ENABLED", True)
    monkeypatch.setattr(kube, "run_kubectl", lambda args: (json.dumps({"items": items}), ""))

    result = kube.gather_sealed_secrets()

    assert result["sealed_secrets_failed"] == "n8n\tn8n-env\tno key could decrypt secret"
    fields = reporting.build_summary_fields(dict(HEALTHY_STATE, **result))
    assert {f["name"]: f["value"] for f in fields}["🔐 SealedSecrets"] == "❌ 1/2 Synced"


def test_gather_flux_status_disabled_skips_call(monkeypatch):
    monkeypatch.setattr(config, "FLUX_CHECK_ENABLED", False)
    monkeypatch.setattr(kube, "run_kubectl", lambda args: pytest.fail("kubectl must not be called"))

    result = kube.gather_flux_status()

    assert result == {"flux_failed": "None", "flux_failed_count": 0, "flux_total": 0}


# ── ask_groq ────────────────────────────────────────────────────────────────


def test_ask_groq_returns_warning_when_key_missing(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "YOUR_GROQ_API_KEY_HERE")
    result = groq.ask_groq("prompt")
    assert "GROQ_API_KEY" in result
    assert "not set" in result


# ── send_discord_embed ──────────────────────────────────────────────────────


def test_send_discord_embed_prints_when_webhook_missing(monkeypatch, capsys):
    monkeypatch.setattr(config, "DISCORD_WEBHOOK", "YOUR_DISCORD_WEBHOOK_URL_HERE")
    discord_client.send_discord_embed({"title": "Test", "color": 1})
    out = capsys.readouterr().out
    assert '"embeds"' in out


# ── orchestrate.run() report selection ──────────────────────────────────────


def test_main_posts_green_embed_when_healthy(monkeypatch):
    monkeypatch.setattr(kube, "gather_cluster_state", lambda: dict(HEALTHY_STATE))
    monkeypatch.setattr(groq, "ask_groq", lambda prompt: pytest.fail("LLM must not be called"))
    sent = []
    monkeypatch.setattr(discord_client, "send_discord_embed", sent.append)

    orchestrate.run()

    assert len(sent) == 1
    embed = sent[0]
    assert embed["title"] == "✅ K8s Health Check"
    assert embed["color"] == config.DISCORD_COLORS["green"]
    assert embed["fields"]
    assert "k8s v1.36.2+k3s1" in embed["footer"]["text"]


def test_main_posts_red_embed_when_issues_found(monkeypatch):
    state = dict(HEALTHY_STATE)
    state["pending_pods"] = "default/pod-2"
    monkeypatch.setattr(kube, "gather_cluster_state", lambda: state)
    prompts = []
    monkeypatch.setattr(
        groq,
        "ask_groq",
        lambda prompt: prompts.append(prompt) or "- pod-2 unschedulable",
    )
    sent = []
    monkeypatch.setattr(discord_client, "send_discord_embed", sent.append)

    orchestrate.run()

    assert prompts, "LLM should be called when issues exist"
    assert "## Pending Pods" in prompts[0]
    assert len(sent) == 1
    embed = sent[0]
    assert embed["title"] == "🔴 K8s Health Report — Issues Found"
    assert embed["color"] == config.DISCORD_COLORS["red"]
    assert "- pod-2 unschedulable" in embed["description"]


def test_main_posts_red_embed_when_backups_fail(monkeypatch):
    state = dict(HEALTHY_STATE)
    state["velero_failed_backups"] = (
        "daily-app\terror putting object: x-amz-tagging not implemented"
    )
    state["velero_failed_count"] = 1
    monkeypatch.setattr(kube, "gather_cluster_state", lambda: state)
    prompts = []
    monkeypatch.setattr(
        groq,
        "ask_groq",
        lambda prompt: prompts.append(prompt) or "- backups failing",
    )
    sent = []
    monkeypatch.setattr(discord_client, "send_discord_embed", sent.append)

    orchestrate.run()

    assert prompts, "LLM should be called when backups fail"
    assert "## Velero Backups" in prompts[0]
    assert len(sent) == 1
    embed = sent[0]
    assert embed["title"] == "🔴 K8s Health Report — Issues Found"
    assert embed["color"] == config.DISCORD_COLORS["red"]


def test_main_posts_red_embed_when_certs_expired(monkeypatch):
    state = dict(HEALTHY_STATE)
    state["expired_certs"] = "istio-system\texpired-cert\tEXPIRED 2026-07-22T00:00:00Z"
    state["expired_cert_count"] = 1
    monkeypatch.setattr(kube, "gather_cluster_state", lambda: state)
    prompts = []
    monkeypatch.setattr(
        groq,
        "ask_groq",
        lambda prompt: prompts.append(prompt) or "- certificate expired",
    )
    sent = []
    monkeypatch.setattr(discord_client, "send_discord_embed", sent.append)

    orchestrate.run()

    assert prompts, "LLM should be called when certs expired"
    assert "## Expired / Expiring Certificates" in prompts[0]
    assert len(sent) == 1
    embed = sent[0]
    assert embed["title"] == "🔴 K8s Health Report — Issues Found"
    assert embed["color"] == config.DISCORD_COLORS["red"]


def test_main_posts_red_embed_when_node_pressure(monkeypatch):
    state = dict(HEALTHY_STATE)
    state["node_pressure"] = "node1\tDiskPressure"
    state["node_pressure_count"] = 1
    monkeypatch.setattr(kube, "gather_cluster_state", lambda: state)
    prompts = []
    monkeypatch.setattr(
        groq,
        "ask_groq",
        lambda prompt: prompts.append(prompt) or "- node1 disk pressure",
    )
    sent = []
    monkeypatch.setattr(discord_client, "send_discord_embed", sent.append)

    orchestrate.run()

    assert prompts, "LLM should be called when node pressure detected"
    assert "## Node Pressure Conditions" in prompts[0]
    assert len(sent) == 1
    embed = sent[0]
    assert embed["title"] == "🔴 K8s Health Report — Issues Found"
    assert embed["color"] == config.DISCORD_COLORS["red"]


def test_main_posts_red_embed_when_externaldns_fails(monkeypatch):
    state = dict(HEALTHY_STATE)
    state["externaldns_ok"] = False
    state["externaldns_resolved"] = "195.201.217.35"
    state["externaldns_status"] = (
        "auth.fulda-cloud.com via 192.168.237.99 resolved to 195.201.217.35, expected 192.168.10.2"
    )
    monkeypatch.setattr(kube, "gather_cluster_state", lambda: state)
    prompts = []
    monkeypatch.setattr(
        groq,
        "ask_groq",
        lambda prompt: prompts.append(prompt) or "- ExternalDNS resolving to the wrong IP",
    )
    sent = []
    monkeypatch.setattr(discord_client, "send_discord_embed", sent.append)

    orchestrate.run()

    assert prompts, "LLM should be called when ExternalDNS check fails"
    assert "## ExternalDNS Health" in prompts[0]
    assert len(sent) == 1
    embed = sent[0]
    assert embed["title"] == "🔴 K8s Health Report — Issues Found"
    assert embed["color"] == config.DISCORD_COLORS["red"]
    fields_by_name = {f["name"]: f["value"] for f in embed["fields"]}
    assert fields_by_name["🌐 ExternalDNS-Health"] == "❌ FAIL (195.201.217.35)"


def test_main_posts_amber_embed_when_kubectl_fails(monkeypatch):
    state = dict(
        HEALTHY_STATE,
        kubectl_ok=False,
        nodes="kubectl not found. Is it installed and in PATH?",
    )
    monkeypatch.setattr(kube, "gather_cluster_state", lambda: state)
    monkeypatch.setattr(groq, "ask_groq", lambda prompt: pytest.fail("LLM must not be called"))
    sent = []
    monkeypatch.setattr(discord_client, "send_discord_embed", sent.append)

    orchestrate.run()

    assert len(sent) == 1
    embed = sent[0]
    assert embed["title"] == "⚠️ K8s Health Check — Could Not Run"
    assert embed["color"] == config.DISCORD_COLORS["amber"]
