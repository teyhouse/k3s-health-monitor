import json
import urllib.error
import urllib.request

from k8s_monitor import config

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = (
    "You are a Kubernetes SRE assistant. Analyze the provided cluster "
    "state and produce a concise health report. Be specific about "
    "namespaces, pod names, and job names. Do NOT suggest fixes unless "
    "explicitly asked — only report issues. Ignore everything related "
    "to Recent warnings for DNS config forming in kube-system namespace. "
    "Use bullet points. Keep it under 1500 characters so it fits in a "
    "Discord message."
)


def ask_groq(prompt: str) -> str:
    """Send a prompt to Groq's free API and return the response text."""
    if not config.GROQ_API_KEY or config.GROQ_API_KEY == "YOUR_GROQ_API_KEY_HERE":
        return "⚠️  GROQ_API_KEY is not set. Cannot contact LLM."

    payload = json.dumps(
        {
            "model": config.GROQ_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 512,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        GROQ_API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {config.GROQ_API_KEY}",
            "Content-Type": "application/json",
            # Needed to pass Cloudflare bot protection on Groq's API.
            # Python's default urllib User-Agent triggers a 403/1010 error.
            "User-Agent": "k8s-monitor/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
            return body["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        return f"⚠️  Groq API error {e.code}: {e.read().decode()}"
    except Exception as e:
        return f"⚠️  Groq request failed: {e}"
