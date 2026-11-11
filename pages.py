"""This session's pages: what each holds, its link token, and when it closes.

Nothing is written to disk: an inline page lives in memory, and a file page reads its
file on every request. A server restart may lose pages, which is accepted.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import files
from render import KINDS, render

BKK = ZoneInfo("Asia/Bangkok")
DEFAULT_HOURS = 8.0
MAX_HOURS = 24.0
MIN_HOURS = 1 / 60
MAX_CONTENT = files.MAX_BYTES

_EXT_KIND = {".md": "markdown", ".markdown": "markdown", ".html": "html", ".htm": "html"}


class PageError(ValueError):
    """A request the tools refuse; the message says why."""


def bkk(ts: float) -> str:
    return datetime.fromtimestamp(ts, BKK).strftime("%d %b %H:%M BKK")


@dataclass
class Page:
    id: str
    token: str
    kind: str
    title: str
    content: str | None
    file: Path | None
    created: float
    updated: float
    expires: float
    sent_base: str | None = None  # the tunnel address whose link was last sent to Google Chat


class PageStore:
    def __init__(self, clock=time.time, roots: tuple[Path, ...] = files.ALLOWED_ROOTS):
        self._clock = clock
        self._roots = roots
        self._lock = threading.Lock()
        self._pages: dict[str, Page] = {}

    def publish(self, title: str, kind: str = "", content: str = "", file_path: str = "",
                hours: float = DEFAULT_HOURS) -> Page:
        if bool(content) == bool(file_path):
            raise PageError("give exactly one of content or file_path")
        file = self._file(file_path) if file_path else None
        now = self._clock()
        page = Page(id="p-" + secrets.token_hex(4), token=secrets.token_urlsafe(16),
                    kind=self._kind(kind, file), title=self._title(title, file),
                    content=self._content(content) if content else None, file=file,
                    created=now, updated=now, expires=now + self._hours(hours) * 3600)
        with self._lock:
            self._pages[page.id] = page
        return page

    def update(self, page_id: str, content: str = "", file_path: str = "", title: str = "",
               kind: str = "", hours: float = 0) -> Page:
        """Replace what's given; keep the rest. hours=0 keeps the close time, anything
        else closes the page that many hours from now (at most a day)."""
        if content and file_path:
            raise PageError("give content or file_path, not both")
        file = self._file(file_path) if file_path else None
        new_content = self._content(content) if content else None
        new_hours = self._hours(hours) if hours else None
        if kind and kind not in KINDS:
            raise PageError("kind must be one of %s" % ", ".join(KINDS))
        with self._lock:
            page = self._get(page_id)
            if new_content is not None:
                page.content, page.file = new_content, None
            if file is not None:
                page.content, page.file = None, file
                if not kind:
                    page.kind = self._kind("", file)
            if kind:
                page.kind = kind
            if title.strip():
                page.title = title.strip()
            now = self._clock()
            if new_hours is not None:
                page.expires = now + new_hours * 3600
            page.updated = now
            return page

    def get(self, page_id: str) -> Page:
        with self._lock:
            return self._get(page_id)

    def by_token(self, token: str) -> Page | None:
        with self._lock:
            for page in self._pages.values():
                if secrets.compare_digest(page.token, token):
                    return page
        return None

    def pages(self) -> list[Page]:
        with self._lock:
            return sorted(self._pages.values(), key=lambda p: p.created)

    def remove(self, page_id: str) -> Page:
        with self._lock:
            return self._pages.pop(self._get(page_id).id)

    def remove_all(self) -> list[Page]:
        with self._lock:
            gone, self._pages = list(self._pages.values()), {}
            return gone

    def expire(self) -> list[Page]:
        now = self._clock()
        with self._lock:
            gone = [p for p in self._pages.values() if p.expires <= now]
            for p in gone:
                del self._pages[p.id]
            return gone

    def body(self, page: Page) -> str:
        """The page as the browser gets it. A file page reads (and re-checks) its file now."""
        content = page.content if page.file is None else files.read(page.file, self._roots)
        stamp = None if page.file is not None else datetime.fromtimestamp(page.updated, BKK)
        return render(page.kind, content or "", page.title, stamp)

    def _get(self, page_id: str) -> Page:
        page = self._pages.get(page_id)
        if page is None:
            raise PageError("no page %r in this session (list_pages shows them)" % page_id)
        return page

    def _file(self, file_path: str) -> Path:
        try:
            return files.check(file_path, self._roots)
        except files.FileRefused as e:
            raise PageError(str(e)) from None

    @staticmethod
    def _content(content: str) -> str:
        if len(content.encode("utf-8")) > MAX_CONTENT:
            raise PageError("content is over %d MB" % (MAX_CONTENT // 1024 // 1024))
        return content

    @staticmethod
    def _kind(kind: str, file: Path | None) -> str:
        if kind:
            if kind not in KINDS:
                raise PageError("kind must be one of %s" % ", ".join(KINDS))
            return kind
        if file is not None:
            return _EXT_KIND.get(file.suffix.lower(), "text")
        return "markdown"

    @staticmethod
    def _title(title: str, file: Path | None) -> str:
        return title.strip() or (file.name if file else "Page from Claude")

    @staticmethod
    def _hours(hours: float) -> float:
        try:
            hours = float(hours)
        except (TypeError, ValueError):
            raise PageError("hours must be a number") from None
        if hours <= 0:
            raise PageError("hours must be more than 0")
        return min(max(hours, MIN_HOURS), MAX_HOURS)
