# Security Policy

## Supported versions

This project is a small personal monitoring tool; only the latest commit on
`main` is supported. Please make sure you are running an up-to-date checkout
before reporting an issue.

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, report them privately via [GitHub Security Advisories]
("Report a vulnerability" under the repository's **Security** tab). You can
also contact the maintainer directly through their GitHub profile.

Please include as much of the following as you can:

- The type of issue and its impact
- Steps to reproduce or a proof of concept
- The affected commit/revision
- Any suggested fixes

You can expect an initial response within a few days. Once a fix is released,
credit will be given if desired.

## Scope notes

- This tool reads cluster state with `kubectl`/`velero` and sends it to
  Groq's cloud LLM API and your Discord webhook when issues are detected.
  Treat your `GROQ_API_KEY` and `DISCORD_WEBHOOK` values as secrets — the
  webhook URL in particular grants posting access to anyone who has it.
- `.env` is git-ignored and must never be committed. If you accidentally leak
  a key or webhook, rotate it immediately (Discord webhooks can be deleted
  and recreated from channel settings).
