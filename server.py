#!/usr/bin/env python3
"""page-drop MCP server.

Shows Claude's output as a web page the user can open on their phone: plain text,
Markdown rendered like GitHub, or HTML to copy into Gmail. Each page gets a long
random link through a Cloudflare quick tunnel. Pages close after 8 hours by default
(at most 24), or when the session ends: exit, /clear, or archived on claude.ai.

    server.py               run as an MCP server (stdio)
    server.py end-session   the SessionEnd hook: take down this session's pages
"""
from __future__ import annotations

import asyncio
import atexit
import os
import signal
import sys

from mcp.server.mcpserver import MCPServer

import session

mcp = MCPServer("page-drop")
_drop = None


def drop():
    global _drop
    if _drop is None:
        from service import PageDrop
        _drop = PageDrop()
        atexit.register(_drop.close)
    return _drop


@mcp.tool()
async def publish_page(title: str = "", kind: str = "", content: str = "", file_path: str = "",
                       hours: float = 8) -> dict:
    """Show something to the user as a web page they can open on their phone, and get its link.

    Use it for output that's too long to read in the chat (drafts, reports, review comments) or
    that the user will copy somewhere else (an email reply). Always put the returned `url` in your
    reply, in full, on its own line. The first time, the link is also sent to the user's Google
    Chat (`sent_to_chat`).

    title: shown on the page and in the Chat message.
    kind: "text" (plain text, kept as written), "markdown" (rendered like GitHub), or "html"
      (served as given with nothing added, so Ctrl+A / Ctrl+C copies exactly the content). For an
      email reply, write simple HTML with inline styles: the user pastes it into Gmail on the web.
      Default: from the file's extension, else markdown.
    content: the page's content, or instead
    file_path: an absolute path under /tmp or ~/Documents. The page follows the file: editing the
      file updates the page with no further call. Dotfiles, .env files, keys and secrets are
      refused. Never publish secrets: anyone holding the link can open the page.
    hours: how long the page stays up; default 8, at most 24. It also closes when this session
      ends (exit, /clear, or archived on claude.ai)."""
    return await asyncio.to_thread(drop().publish, title, kind, content, file_path, hours)


@mcp.tool()
async def update_page(page_id: str, content: str = "", file_path: str = "", title: str = "",
                      kind: str = "", hours: float = 0) -> dict:
    """Change a page: new content (or follow a file instead), title, kind, or lifetime.

    The link stays the same. If the tunnel restarted since the link was sent, the address changed:
    then `link_changed` is true, the new link goes to Google Chat, and you give the user the new
    `url`. hours: 0 keeps the close time; anything else closes the page that many hours from now
    (at most 24). A page that follows a file needs no call to show the file's latest version. If
    you lost the page_id (e.g. the conversation was summarised), list_pages has it."""
    return await asyncio.to_thread(drop().update, page_id, content, file_path, title, kind, hours)


@mcp.tool()
def list_pages() -> list:
    """This session's pages: page_id, title, kind, url, closes_at, and source (a file or inline)."""
    return drop().list()


@mcp.tool()
async def remove_page(page_id: str) -> dict:
    """Take a page down now; its link stops working."""
    return await asyncio.to_thread(drop().remove, page_id)


def _end_session() -> None:
    """SessionEnd hook: Claude Code sends JSON on stdin, which isn't needed here."""
    try:
        sys.stdin.read()
    except OSError:
        pass
    try:
        session.end_session()
    except Exception as e:  # a hook must never get in the way of ending the session
        print("page-drop end-session: %s" % e, file=sys.stderr)


def _on_sigterm(signum, frame) -> None:
    """Claude Code (or Remote Control, when a session is archived) may stop us with SIGTERM,
    which skips atexit: clean up here, then leave at once."""
    try:
        if _drop is not None:
            _drop.close()
    finally:
        os._exit(0)


if __name__ == "__main__":
    if sys.argv[1:] == ["end-session"]:
        _end_session()
    elif len(sys.argv) > 1:
        sys.exit(__doc__)
    else:
        signal.signal(signal.SIGTERM, _on_sigterm)
        mcp.run()
