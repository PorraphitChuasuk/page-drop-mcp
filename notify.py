"""Send links to the user's Google Chat through the notifier named in PAGE_DROP_NOTIFY."""
from __future__ import annotations

import os
import subprocess

NOTIFY = os.environ.get("PAGE_DROP_NOTIFY", "")


def send(text: str, links: list[tuple[str, str]], notifier: str | None = None) -> tuple[bool, str]:
    """Run `notifier "<text>" [<label> <url>]...`. Returns (sent, why not).

    The notifier exits 0 when it sent, and non-zero when it could only print."""
    cmd = NOTIFY if notifier is None else notifier
    if not cmd:
        return False, "PAGE_DROP_NOTIFY is not set"
    args = [cmd, text]
    for label, url in links:
        args += [label, url]
    try:
        # Output is captured: stdout here is MCP's, and a failing notifier prints the links.
        r = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    if r.returncode == 0:
        return True, ""
    return False, (r.stderr.strip().splitlines() or ["%s exited %d" % (cmd, r.returncode)])[-1]
