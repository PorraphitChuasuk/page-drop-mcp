"""Which files a page may follow.

A page is open to anyone holding its link for up to a day, so only plain files under
/tmp (where the Claude scratchpads are) or ~/Documents can be published, and never
dotfiles, files in dot-folders, .env files, keys or certificates. Symlinks are
resolved first, so a link can't point a page somewhere else. Checked when a page is
published and again on every read.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024

HOME = Path.home()
ALLOWED_ROOTS = (Path("/tmp"), HOME / "Documents")
DENIED_DIRS = (HOME / ".config" / "secrets", HOME / ".aws", HOME / ".ssh")
DENIED_NAMES = (".env", ".env.*", "*.env", "*.pem", "*.key")


class FileRefused(ValueError):
    """The file can't be published (or read for a page); the message says why."""


def check(path: str | Path, roots: tuple[Path, ...] = ALLOWED_ROOTS) -> Path:
    """Return the resolved path if it may be published, else raise FileRefused."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise FileRefused("file_path must be absolute: %s" % path)
    try:
        real = p.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError):
        raise FileRefused("no such file: %s" % path) from None
    except (OSError, RuntimeError) as e:
        raise FileRefused("can't read %s: %s" % (path, e)) from None
    if not real.is_file():
        raise FileRefused("not a regular file: %s" % path)
    for denied in DENIED_DIRS:
        if real.is_relative_to(denied.resolve()):
            raise FileRefused("secrets can't be published: %s" % path)
    root = next((r for r in roots if real.is_relative_to(r.resolve())), None)
    if root is None:
        raise FileRefused("only files under %s can be published: %s"
                          % (" or ".join(str(r) for r in roots), path))
    if any(part.startswith(".") for part in real.relative_to(root.resolve()).parts):
        raise FileRefused("dotfiles and files in dot-folders can't be published: %s" % path)
    if any(fnmatch.fnmatch(real.name.lower(), pat) for pat in DENIED_NAMES):
        raise FileRefused(".env files, keys and certificates can't be published: %s" % path)
    if real.stat().st_size > MAX_BYTES:
        raise FileRefused("file is over %d MB: %s" % (MAX_BYTES // 1024 // 1024, path))
    return real


def read(path: str | Path, roots: tuple[Path, ...] = ALLOWED_ROOTS) -> str:
    """Check again (the file may have changed into something else) and read it as UTF-8."""
    return check(path, roots).read_text(encoding="utf-8", errors="replace")
