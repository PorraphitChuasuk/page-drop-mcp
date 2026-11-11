"""The local HTTP server behind the tunnel: /<token>/ serves a page, nothing else is listed."""
from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import files
from pages import PageStore

_TOKEN = re.compile(r"^/([A-Za-z0-9_-]{16,})/?$")

HEADERS = {
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


class PageServer:
    """Serves store's pages on 127.0.0.1 at a free port, in a background thread."""

    def __init__(self, store: PageStore):
        self.store = store
        self._httpd: ThreadingHTTPServer | None = None

    @property
    def port(self) -> int | None:
        return self._httpd.server_address[1] if self._httpd else None

    def start(self) -> int:
        if self._httpd is None:
            handler = type("Handler", (_Handler,), {"store": self.store})
            self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            self._httpd.daemon_threads = True
            threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self.port

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()


class _Handler(BaseHTTPRequestHandler):
    store: PageStore
    server_version = "page-drop"
    sys_version = ""

    def do_GET(self):
        self._answer(send_body=True)

    def do_HEAD(self):
        self._answer(send_body=False)

    def _answer(self, send_body: bool) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            return self._send(200, "ok", "text/plain", send_body)
        m = _TOKEN.match(path)
        page = self.store.by_token(m.group(1)) if m else None
        if page is None:
            return self._send(404, "Not found.", "text/plain", send_body)
        try:
            body = self.store.body(page)
        except files.FileRefused as e:
            return self._send(404, "This page's file can't be shown any more (%s)." % e, "text/plain", send_body)
        self._send(200, body, "text/html", send_body)

    def _send(self, status: int, body: str, ctype: str, send_body: bool) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        if send_body:
            self.wfile.write(data)

    def log_message(self, format, *args):  # stdout belongs to MCP; stay quiet
        pass
