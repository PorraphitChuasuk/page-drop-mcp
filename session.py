"""Tie pages to the Claude Code session that made them.

Claude Code starts each MCP server (and each hook) as a child of the session's own
process. Every page-drop process keeps a folder named "<session pid>-<own pid>" under
the runtime dir. The SessionEnd hook (fired on /clear, exit and logout) finds its
nearest Claude ancestor and drops an "end" marker in that session's folders; the
page-drop process sees it within seconds and takes its pages down. Folders whose
page-drop process is gone (crash, OOM kill) are swept at the next start.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

END_MARKER = "end"
_FOLDER = re.compile(r"^(\d+)-(\d+)$")


def runtime_dir() -> Path:
    base = os.environ.get("PAGE_DROP_RUNTIME") or os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return Path(base) / "page-drop"


def parent_of(pid: int) -> int | None:
    try:
        stat = Path("/proc/%d/stat" % pid).read_text()
    except OSError:
        return None
    # "pid (comm) state ppid ...": comm may hold spaces or parentheses.
    return int(stat.rsplit(")", 1)[1].split()[1])


def is_claude(pid: int) -> bool:
    """Claude Code's native binary: ~/.local/share/claude/versions/<version>, or named claude."""
    try:
        exe = os.readlink("/proc/%d/exe" % pid)
    except OSError:
        exe = ""
    try:
        comm = Path("/proc/%d/comm" % pid).read_text().strip()
    except OSError:
        comm = ""
    return "/claude/versions/" in exe or comm == "claude"


def session_pid(start: int | None = None, max_depth: int = 12) -> int | None:
    """The nearest Claude Code process above start (default: this process's parent)."""
    pid = os.getppid() if start is None else start
    for _ in range(max_depth):
        if not pid or pid <= 1:
            return None
        if is_claude(pid):
            return pid
        pid = parent_of(pid)
    return None


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Owner:
    """This page-drop process's folder, named for its session."""

    def __init__(self, base: Path | None = None, session: int | None = None, pid: int | None = None):
        self.base = base or runtime_dir()
        self.session = session if session is not None else (session_pid() or os.getppid())
        self.pid = pid or os.getpid()
        self.folder = self.base / ("%d-%d" % (self.session, self.pid))

    def create(self) -> None:
        self.base.mkdir(mode=0o700, parents=True, exist_ok=True)
        sweep(self.base)
        self.folder.mkdir(mode=0o700, exist_ok=True)

    def take_end_marker(self) -> bool:
        """True once per SessionEnd: the marker is removed when seen."""
        try:
            (self.folder / END_MARKER).unlink()
            return True
        except FileNotFoundError:
            return False

    def remove(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)


def sweep(base: Path) -> list[Path]:
    """Remove folders whose page-drop process is gone."""
    gone = []
    for d in base.iterdir() if base.is_dir() else ():
        m = _FOLDER.match(d.name)
        if m and not alive(int(m.group(2))):
            shutil.rmtree(d, ignore_errors=True)
            gone.append(d)
    return gone


def end_session(base: Path | None = None, start: int | None = None) -> list[Path]:
    """Run by the SessionEnd hook: mark every page-drop folder of this hook's session."""
    base = base or runtime_dir()
    session = session_pid(start)
    if session is None or not base.is_dir():
        return []
    marked = []
    for d in base.glob("%d-*" % session):
        if _FOLDER.match(d.name) and d.is_dir():
            (d / END_MARKER).touch()
            marked.append(d)
    return marked
