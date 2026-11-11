"""page-drop for one Claude Code session: pages, the local server, the tunnel, and cleanup.

The server and tunnel run only while the session has a page. A background thread
takes pages down when they expire or when the session's SessionEnd hook fires, stops
the tunnel when the last page is gone, and restarts it (sending the new links to
Google Chat) if cloudflared exits while pages are live.
"""
from __future__ import annotations

import sys
import threading
import time
import traceback

import notify
from pages import Page, PageStore, bkk
from session import Owner
from tunnel import Tunnel
from web import PageServer

RESTART_BACKOFF = 60.0


class PageDrop:
    def __init__(self, store: PageStore | None = None, server: PageServer | None = None,
                 tunnel: Tunnel | None = None, owner: Owner | None = None,
                 notifier=notify.send, tick: float = 2.0):
        self.store = store or PageStore()
        self.server = server or PageServer(self.store)
        self.tunnel = tunnel or Tunnel()
        self.owner = owner or Owner()
        self.notifier = notifier
        self.tick = tick
        self._lock = threading.RLock()
        self._online = False
        self._next_restart = 0.0
        self._stop = threading.Event()
        self.owner.create()
        self._janitor = threading.Thread(target=self._run, name="page-drop-janitor", daemon=True)
        self._janitor.start()

    # --- tools -----------------------------------------------------------------

    def publish(self, title: str = "", kind: str = "", content: str = "", file_path: str = "",
                hours: float = 8.0) -> dict:
        page = self.store.publish(title, kind, content, file_path, hours)
        try:
            base = self._ensure_online()
        except Exception:
            self.store.remove(page.id)
            self._offline_if_empty()
            raise
        info = self._info(page, base)
        sent, why = self.notifier("📄 %s\nOpen until %s." % (page.title, bkk(page.expires)),
                                  [("Open the page", info["url"])])
        if sent:
            page.sent_base = base
        return self._with_sent(info, sent, why)

    def update(self, page_id: str, content: str = "", file_path: str = "", title: str = "",
               kind: str = "", hours: float = 0) -> dict:
        page = self.store.update(page_id, content, file_path, title, kind, hours)
        base = self._ensure_online()
        info = self._info(page, base)
        info["link_changed"] = page.sent_base != base
        if not info["link_changed"]:
            info["sent_to_chat"] = False
            return info
        sent, why = self.notifier("📄 %s: new link (the old one stopped working)." % page.title,
                                  [("Open the page", info["url"])])
        if sent:
            page.sent_base = base
        return self._with_sent(info, sent, why)

    def list(self) -> list[dict]:
        base = self.tunnel.url if self._online and self.tunnel.alive() else None
        return [self._info(p, base) for p in self.store.pages()]

    def remove(self, page_id: str) -> dict:
        page = self.store.remove(page_id)
        self._offline_if_empty()
        return {"removed": page.id, "title": page.title}

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            self.store.remove_all()
            self._go_offline()
        self.owner.remove()

    # --- internals -------------------------------------------------------------

    def _ensure_online(self) -> str:
        with self._lock:
            port = self.server.start()
            if not self.tunnel.answers():
                self.tunnel.stop()
                self.tunnel.start(port)
            self._online = True
            return self.tunnel.url

    def _offline_if_empty(self) -> None:
        with self._lock:
            if not self.store.pages():
                self._go_offline()

    def _go_offline(self) -> None:
        if self._online:
            self.tunnel.stop()
            self.server.stop()
            self._online = False

    def _run(self) -> None:
        while not self._stop.wait(self.tick):
            try:
                self.sweep()
            except Exception:
                traceback.print_exc(file=sys.stderr)

    def sweep(self) -> None:
        """One janitor pass (also called directly by tests)."""
        if self.owner.take_end_marker():
            self.store.remove_all()
        self.store.expire()
        with self._lock:
            pages = self.store.pages()
            if not pages:
                self._go_offline()
                return
            if not self._online or self.tunnel.alive() or time.monotonic() < self._next_restart:
                return
            try:
                base = self.tunnel.start(self.server.start())
            except Exception:
                self._next_restart = time.monotonic() + RESTART_BACKOFF
                raise
        links = [(p.title[:60], self._url(base, p)) for p in pages]
        sent, _ = self.notifier("📄 page-drop's tunnel restarted, so the links changed:", links)
        if sent:
            for p in pages:
                p.sent_base = base

    @staticmethod
    def _url(base: str, page: Page) -> str:
        return "%s/%s/" % (base, page.token)

    def _info(self, page: Page, base: str | None) -> dict:
        return {"page_id": page.id, "title": page.title, "kind": page.kind,
                "url": self._url(base, page) if base else None,
                "closes_at": bkk(page.expires),
                "source": str(page.file) if page.file else "inline"}

    @staticmethod
    def _with_sent(info: dict, sent: bool, why: str) -> dict:
        info["sent_to_chat"] = sent
        if not sent:
            info["not_sent_because"] = why
        return info
