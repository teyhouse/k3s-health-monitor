import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from k8s_monitor import config, discord_client, groq, kube, orchestrate, reporting  # noqa: E402
from utils import shell  # noqa: E402

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
    "velero_failed_backups": "None",
    "velero_failed_count": 0,
    "expired_certs": "None",
    "expired_cert_count": 0,
    "server_version": "v1.36.2+k3s1",
}


def fake_velero(args, now=None):
    """Simulate velero output with a mix of recent/old and failed/completed backups."""
    from datetime import UTC, datetime, timedelta

    now = now or datetime.now(UTC)

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    backups = {
        "items": [
            {
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
                "metadata": {
                    "name": "test-backup-recent",
                    "creationTimestamp": iso(now - timedelta(hours=1)),
                },
                "status": {"phase": "Completed", "failureReason": None},
            },
            {
                "metadata": {
                    "name": "daily-app-old",
                    "creationTimestamp": iso(now - timedelta(hours=48)),
                },
                "status": {"phase": "Failed", "failureReason": "old failure"},
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
    assert len(fields) == 8
    assert all(field["inline"] for field in fields)
    by_name = {field["name"]: field["value"] for field in fields}
    assert by_name["🖥️ Nodes"] == "1/1 Ready"
    assert by_name["📦 Non-Running Pods"] == "0"
    assert by_name["⚠️ Warnings (1h)"] == "0"
    assert by_name["🛟 Failed Backups (24h)"] == "0"
    assert by_name["🔏 Cert Issues"] == "0"


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
    monkeypatch.setattr(kube, "run_velero", lambda args: ("", ""))
    state = kube.gather_cluster_state()

    assert state["kubectl_ok"] is True
    assert state["node_total"] == 2
    assert state["node_ready"] == 2
    assert state["non_running_count"] == 1
    assert state["crashloop_count"] == 1
    assert state["failed_jobs_count"] == 1
    assert state["pending_count"] == 1
    assert state["warning_count"] == 2
    assert state["velero_failed_count"] == 0
    assert state["expired_cert_count"] == 3
    assert state["server_version"] == "v1.36.2+k3s1"


def test_gather_cluster_state_healthy_outputs(monkeypatch):
    monkeypatch.setattr(kube, "run_kubectl", lambda args: ("", ""))
    monkeypatch.setattr(kube, "run_velero", lambda args: ("", ""))
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
    state = kube.gather_cluster_state()
    assert state["failed_jobs"] == "None"
    assert state["failed_jobs_count"] == 0


# ── run_kubectl / run_velero (via utils.shell) ──────────────────────────────


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


def test_run_velero_file_not_found(monkeypatch):
    def boom(args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(shell.subprocess, "run", boom)
    out, err = kube.run_velero(["get", "backups"])
    assert out == ""
    assert "velero not found" in err


# ── velero ──────────────────────────────────────────────────────────────────


def test_gather_velero_backups_counts_recent_failures_only(monkeypatch):
    monkeypatch.setattr(config, "VELERO_CHECK_ENABLED", True)
    monkeypatch.setattr(kube, "run_velero", fake_velero)
    result = kube.gather_velero_backups()

    assert result["velero_failed_count"] == 1
    assert "daily-app-recent" in result["velero_failed_backups"]
    assert "daily-app-old" not in result["velero_failed_backups"]
    assert "test-backup-recent" not in result["velero_failed_backups"]
    assert "x-amz-tagging" in result["velero_failed_backups"]


def test_gather_velero_backups_disabled_skips_call(monkeypatch):
    monkeypatch.setattr(config, "VELERO_CHECK_ENABLED", False)
    monkeypatch.setattr(kube, "run_velero", lambda args: pytest.fail("velero must not be called"))
    result = kube.gather_velero_backups()

    assert result == {"velero_failed_backups": "None", "velero_failed_count": 0}


def test_gather_velero_backups_empty_output(monkeypatch):
    monkeypatch.setattr(config, "VELERO_CHECK_ENABLED", True)
    monkeypatch.setattr(kube, "run_velero", lambda args: ("", ""))
    result = kube.gather_velero_backups()

    assert result == {"velero_failed_backups": "None", "velero_failed_count": 0}


def test_gather_velero_backups_invalid_json(monkeypatch):
    monkeypatch.setattr(config, "VELERO_CHECK_ENABLED", True)
    monkeypatch.setattr(kube, "run_velero", lambda args: ("not json", ""))
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
