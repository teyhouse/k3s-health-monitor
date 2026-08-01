# k3s-health-monitor

![CI status](https://github.com/teyhouse/k3s-health-monitor/actions/workflows/ci.yml/badge.svg)

Automated Kubernetes (k3s) cluster health monitoring that posts a formatted report
to a Discord channel via webhook. Cluster state is collected with `kubectl`,
analyzed with Groq's free LLM API, and delivered as a Discord embed.

## Behavior

Every run always posts exactly one report:

- **Healthy** — green embed. No LLM call is made; a static report with live
  summary stats (nodes ready, non-running pods, crashloops, failed jobs, pending
  pods, failed Velero backups in the last 24h, recent warning events) is posted.
- **Issues found** — red embed with an LLM-generated analysis of the collected
  cluster state plus the same summary stats. Failed Velero backups count as an
  issue and trigger this report.
- **`kubectl` failure** — amber embed. The run cannot collect state and is
  reported as such rather than falsely reported as healthy.

The k8s/k3s server version is shown in the embed footer.

## Requirements

- Python >= 3.13
- [uv](https://docs.astral.sh/uv/)
- `kubectl` configured with access to the target cluster
- `velero` CLI (for the backup check; only required when `VELERO_CHECK_ENABLED=true`)

## Setup

```sh
uv sync
cp .env.example .env   # then fill in real values
```

Configuration is read from `.env` (or real environment variables, which take
precedence):

| Variable          | Description                        |
| ----------------- | ---------------------------------- |
| `GROQ_API_KEY`    | Groq API key for the LLM analysis  |
| `DISCORD_WEBHOOK` | Discord webhook URL for the reports|
| `VELERO_CHECK_ENABLED` | Enable the Velero backup check (default `true`). Set to `false` to disable |

`.env` is git-ignored and must never be committed.

## Usage

```sh
uv run k8s-monitor.py
```

## Cron

Runs twice daily at 08:00 and 20:00 UTC:

```cron
0 8,20 * * * cd /home/pi/k3s-monitoring && PATH=/home/pi/.local/bin:/usr/local/bin:/usr/bin:/bin uv run k8s-monitor.py >> /home/pi/k3s-monitoring/k8s-monitor.log 2>&1
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
  kube.py                        kubectl/velero commands + cluster state gathering
  groq.py                        Groq LLM client
  discord_client.py              Discord webhook sender
  reporting.py                   pure functions: embed fields, prompt, health check
  orchestrate.py                 main flow: gather → decide → report
utils/
  shell.py                       generic subprocess runner (dedupes run_kubectl/velero)
tests/                          pytest test suite
.github/workflows/ci.yml       CI pipeline (lint + tests)
.env.example                     example configuration (copy to .env)
```

## Notes

- The LLM model is set via `GROQ_MODEL` in the script; it defaults to
  `llama-3.3-70b-versatile`.
- Discord embed colors and summary field labels are defined in the script.
