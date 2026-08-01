import subprocess


def run_cmd(binary: str, args: list[str], timeout: int = 30) -> tuple[str, str]:
    """Run a CLI command and return (stdout, stderr) with friendly error messages."""
    try:
        result = subprocess.run([binary] + args, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return "", f"{binary} not found. Is it installed and in PATH?"
    except subprocess.TimeoutExpired:
        return "", f"{binary} {' '.join(args)} timed out after {timeout}s"
