import json
import sys
import urllib.request

from k8s_monitor import config


def send_discord_embed(embed: dict) -> None:
    """Post a rich embed message (dict) to a Discord webhook."""
    if not config.DISCORD_WEBHOOK or config.DISCORD_WEBHOOK == "YOUR_DISCORD_WEBHOOK_URL_HERE":
        print("⚠️  DISCORD_WEBHOOK is not set. Printing embed to stdout instead:\n")
        print(json.dumps({"embeds": [embed]}, indent=2))
        return

    payload = json.dumps({"embeds": [embed]}).encode("utf-8")
    req = urllib.request.Request(
        config.DISCORD_WEBHOOK,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "k8s-monitor/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status not in (200, 204):
                print(f"⚠️  Discord returned HTTP {resp.status}", file=sys.stderr)
    except Exception as e:
        print(f"⚠️  Failed to send Discord message: {e}", file=sys.stderr)
