from datetime import UTC, datetime

from k8s_monitor import config, discord_client, groq, kube, reporting


def run() -> None:
    """Main orchestration: gather cluster state, decide health, send Discord report."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    print(f"[{now}] Starting Kubernetes health check...")

    state = kube.gather_cluster_state()

    # If kubectl itself failed, we can't trust the "healthy" signal — alert instead.
    if not state["kubectl_ok"]:
        print("❌  kubectl failed. Sending error report to Discord...")
        discord_client.send_discord_embed(
            {
                "title": "⚠️ K8s Health Check — Could Not Run",
                "description": (
                    "**The health check could not collect cluster state.**\n\n"
                    f"```\n{state['nodes']}\n```"
                ),
                "color": config.DISCORD_COLORS["amber"],
                "footer": {"text": reporting.footer_text(now, state)},
            }
        )
        print("Done.")
        return

    # No issues found — post a nice green "all good" report instead of skipping.
    if not reporting.has_issues(state):
        print("✅  No issues detected. Sending healthy report to Discord...")
        discord_client.send_discord_embed(
            {
                "title": "✅ K8s Health Check",
                "description": (
                    "**Status: All Systems Nominal**\n"
                    "🟢 No cluster health issues detected.\n"
                    "Health check completed successfully."
                ),
                "color": config.DISCORD_COLORS["green"],
                "fields": reporting.build_summary_fields(state),
                "footer": {"text": reporting.footer_text(now, state)},
            }
        )
        print("Done.")
        return

    # Build the prompt — keep it concise to save tokens
    prompt = reporting.build_prompt(state, now)

    print("Sending state to LLM for analysis...")
    analysis = groq.ask_groq(prompt)

    print("Sending report to Discord...")
    discord_client.send_discord_embed(
        {
            "title": "🔴 K8s Health Report — Issues Found",
            "description": (
                f"**Cluster issues detected** as of {now}\n"
                f"Model: `{config.GROQ_MODEL}`\n\n{analysis}"
            ),
            "color": config.DISCORD_COLORS["red"],
            "fields": reporting.build_summary_fields(state),
            "footer": {"text": reporting.footer_text(now, state)},
        }
    )
    print("Done.")
