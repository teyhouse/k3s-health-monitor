import os

from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "YOUR_GROQ_API_KEY_HERE")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "YOUR_DISCORD_WEBHOOK_URL_HERE")

# Groq free tier — fast and free for small models.
# Alternatives: "llama-3.1-8b-instant" (faster), "mixtral-8x7b-32768" (smarter)
GROQ_MODEL = "llama-3.3-70b-versatile"

# Discord embed colors (decimal values, 0xRRGGBB).
DISCORD_COLORS = {
    "green": 0x2ECC71,
    "red": 0xE74C3C,
    "amber": 0xF1C40F,
}

# Benign: host has >3 nameservers (systemd-resolved, dual-stack).
# kubelet truncates to 3 and warns; no functional impact.
# See kubernetes/kubernetes#126585 (no upstream suppress flag yet).
WARNING_FILTER = {"DNSConfigForming"}

# Velero backup check — enabled by default, disable with VELERO_CHECK_ENABLED=false.
VELERO_CHECK_ENABLED = os.environ.get("VELERO_CHECK_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
# Only inspect backups created within this window (hours).
VELERO_LOOKBACK_HOURS = 24
