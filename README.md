# k3s-health-monitor

[![CI](https://github.com/teyhouse/k3s-health-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/teyhouse/k3s-health-monitor/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](pyproject.toml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Automated Kubernetes (k3s) cluster health monitoring that posts a formatted report
to a Discord channel via webhook. Cluster state is collected with `kubectl`,
analyzed with Groq's free LLM API, and delivered as a Discord embed.

## Behavior

Every run always posts exactly one report:

- **Healthy** — green embed. No LLM call is made; a static report with live
  summary stats for every check below is posted.
- **Issues found** — red embed with an LLM-generated analysis of the collected
  cluster state plus the same summary stats.
- **`kubectl` failure** — amber embed. The run cannot collect state and is
  reported as such rather than falsely reported as healthy.

The k8s/k3s server version is shown in the embed footer.

### Checks

Any of the following counts as an issue and triggers the red report:

- Non-running, pending, or crashlooping pods, and failed jobs
- Deployments, StatefulSets, or DaemonSets with fewer ready replicas than desired
- Node pressure conditions (disk, memory, PID)
- Node filesystems at or above `DISK_USAGE_WARNING_PERCENT`
- A k3s API server certificate expiring within `K3S_CERT_WARNING_DAYS`
- Failed or partially failed Velero backups, and Velero schedules without a
  backup within `VELERO_LOOKBACK_HOURS`
- Expired, soon-to-expire, or not ready cert-manager certificates
- CloudNativePG clusters that are unhealthy, failing WAL archiving or backups,
  or without a completed backup within `CNPG_BACKUP_MAX_AGE_HOURS`
- Flux sources, Kustomizations, or HelmReleases reporting `Ready=False`
- SealedSecrets that failed to sync
- ExternalDNS resolving the check hostname to an unexpected IP

Recent warning events are counted and passed to the LLM, but do not trigger a
report on their own.

## Requirements

- Python >= 3.13
- [uv](https://docs.astral.sh/uv/)
- `kubectl` configured with access to the target cluster
- Velero (for the backup check; only required when `VELERO_CHECK_ENABLED=true`)
- `cert-manager` (for the certificate check; only required when `CERT_CHECK_ENABLED=true`)
- Flux (for the Flux check; only required when `FLUX_CHECK_ENABLED=true`)

## Setup

```sh
uv sync
cp .env.example .env   # then fill in real values
```

Configuration is read from `.env` (or real environment variables, which take
precedence):

| Variable                   | Description                        | Default                        |
| -------------------------- | ---------------------------------- | ------------------------------ |
| `GROQ_API_KEY`             | Groq API key for the LLM analysis  | — (required for issue reports) |
| `DISCORD_WEBHOOK`          | Discord webhook URL for the reports| — (required)                   |
| `GROQ_MODEL`               | Groq model used for the analysis   | `llama-3.3-70b-versatile`      |
| `VELERO_CHECK_ENABLED`     | Enable the Velero backup check     | `true`                         |
| `VELERO_LOOKBACK_HOURS`    | Window for failed backups and max age of a schedule's last backup (hours) | `24` |
| `CERT_CHECK_ENABLED`       | Enable the cert-manager certificate check | `true`                  |
| `CERT_EXPIRY_WARNING_DAYS` | Warn on certificates expiring within this many days | `7`           |
| `K3S_CERT_CHECK_ENABLED`   | Enable the k3s API server certificate expiry check | `true`              |
| `K3S_CERT_WARNING_DAYS`    | Warn when the k3s API server certificate expires within this many days | `30` |
| `DISK_CHECK_ENABLED`       | Enable the node disk usage check   | `true`                         |
| `DISK_USAGE_WARNING_PERCENT` | Warn when a node filesystem is at least this full (percent) | `80` |
| `CNPG_CHECK_ENABLED`       | Enable the CloudNativePG cluster and backup check | `true`               |
| `CNPG_BACKUP_MAX_AGE_HOURS` | Max age of the last completed CNPG backup for scheduled clusters (hours) | `24` |
| `FLUX_CHECK_ENABLED`       | Enable the Flux reconciliation check | `true`                       |
| `SEALED_SECRETS_CHECK_ENABLED` | Enable the SealedSecrets sync check | `true`                    |

`.env` is git-ignored and must never be committed.

## Privacy

When issues are detected, collected cluster state (node, pod, workload and job
names, namespaces, warning events, backup/certificate details, failing Flux
resource, Postgres cluster and SealedSecret names with their error messages,
disk usage) is sent to Groq's cloud LLM API for analysis. Only use this tool with clusters whose
metadata you are comfortable sharing with a third-party service. Healthy runs
make no external calls other than posting to your Discord webhook.

## Usage

```sh
uv run k8s-monitor.py    # or: uv run k8s-monitor
```

## Cron

Runs twice daily at 08:00 and 20:00 UTC:

```cron
0 8,20 * * * cd /path/to/k3s-health-monitor && PATH=/usr/local/bin:/usr/bin:/bin uv run k8s-monitor.py >> /path/to/k3s-health-monitor/k8s-monitor.log 2>&1
```

## Development

Lint and tests are run by a GitHub Actions workflow that triggers only when
`k8s-monitor.py`, the tests, or the project configuration change:

```sh
uv run ruff check .        # lint
uv run ruff format --check .   # formatting
uv run pytest              # tests
```

## Project layout

```
pyproject.toml                uv project definition and dependencies
k8s-monitor.py                thin entry point — calls orchestrate.run()
src/k8s_monitor/
  config.py                      env vars, constants (model, colors, filters)
  kube.py                        kubectl/dig commands + cluster state gathering
  groq.py                        Groq LLM client
  discord_client.py              Discord webhook sender
  reporting.py                   pure functions: embed fields, prompt, health check
  orchestrate.py                 main flow: gather → decide → report
utils/
  shell.py                       generic subprocess runner (dedupes run_kubectl/dig)
tests/                          pytest test suite
.github/workflows/ci.yml       CI pipeline (lint + tests)
.env.example                     example configuration (copy to .env)
```

## Notes

- The LLM model defaults to `llama-3.3-70b-versatile` and can be changed via
  the `GROQ_MODEL` environment variable.
- Discord embed colors and summary field labels are defined in
  `src/k8s_monitor/config.py`.
