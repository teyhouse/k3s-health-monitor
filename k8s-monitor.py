#!/usr/bin/env python3
"""
k8s-monitor.py — Kubernetes cluster health check via Groq (free) + Discord webhook.

Usage:
  uv run k8s-monitor.py

Cron example (twice daily at 08:00 and 20:00):
  0 8,20 * * * cd /home/pi/k3s-monitoring && PATH=/home/pi/.local/bin:/usr/local/bin:/usr/bin:/bin \
      uv run k8s-monitor.py >> /home/pi/k3s-monitoring/k8s-monitor.log 2>&1
"""

from k8s_monitor.orchestrate import run

if __name__ == "__main__":
    run()
