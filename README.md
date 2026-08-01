# k3s-health-monitor

Automated Kubernetes (k3s) cluster health monitoring that posts a formatted report
to a Discord channel via webhook. Cluster state is collected with `kubectl`,
analyzed with Groq's free LLM API, and delivered as a Discord embed.

## Behavior

Every run always posts exactly one report:

- **Healthy** — green embed. No LLM call is made; a static report with live
  summary stats (nodes ready, non-running pods, crashloops, failed jobs, pending
  pods, recent warning events) is posted.
- **Issues found** — red embed with an LLM-generated analysis of the collected
  cluster state plus the same summary stats.
- **`kubectl` failure** — amber embed. The run cannot collect state and is
  reported as such rather than falsely reported as healthy.

The k8s/k3s server version is shown in the embed footer.

## Requirements

- Python >= 3.13
- [uv](https://docs.astral.sh/uv/)
- `kubectl` configured with access to the target cluster

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

`.env` is git-ignored and must never be committed.

## Usage

```sh
uv run k8s-monitor.py
```

## Cron

Runs twice daily at 08:00 and 20:00 UTC:

```cron
0 8,20 * * * /home/pi/.local/bin/uv run --project /home/pi/k3s-monitoring k8s-monitor.py >> /home/pi/k3s-monitoring/k8s-monitor.log 2>&1
```

## Project layout

```
pyproject.toml        uv project definition and dependencies
k8s-monitor.py        main monitoring script
.env.example          example configuration (copy to .env)
```

## Notes

- The LLM model is set via `GROQ_MODEL` in the script; it defaults to
  `llama-3.3-70b-versatile`.
- Discord embed colors and summary field labels are defined in the script.
