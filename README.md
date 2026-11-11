# page-drop

An MCP server that lets a Claude Code session show its output as a web page you can
open on your phone: plain text, Markdown rendered like GitHub, or HTML to copy into
Gmail. Each page gets a long random link through a Cloudflare quick tunnel, and the
link is sent to Google Chat when the page is first published. Design:
[docs/superpowers/specs/2026-11-11-page-drop-design.md](docs/superpowers/specs/2026-11-11-page-drop-design.md).

## Tools

| Tool | Does |
|---|---|
| `publish_page(title, kind, content \| file_path, hours=8)` | Publishes a page, returns `page_id` and `url`, sends the link to Chat. |
| `update_page(page_id, content \| file_path, title, kind, hours=0)` | Same link, new content. `link_changed` is true only if the tunnel restarted. |
| `list_pages()` | This session's pages. |
| `remove_page(page_id)` | Takes a page down now. |

- `kind`: `text`, `markdown` or `html` (default: from the file's extension, else markdown).
  An `html` page is served exactly as given, so Ctrl+A / Ctrl+C copies only the content.
- `file_path`: the page follows the file, so editing it updates the page. Only regular
  files under `/tmp` or `~/Documents`; dotfiles, files in dot-folders, `.env` files,
  keys, certificates and `~/.config/secrets`, `~/.aws`, `~/.ssh` are refused (after
  resolving symlinks, at publish and on every read).

## Lifetime

A page closes after `hours` (default 8, at most 24) or when its session ends,
whichever is first:

- the session exits: the server's stdin closes and it cleans up (also on SIGTERM);
- `/clear`: the `SessionEnd` hook runs `server.py end-session`, which marks this
  session's page-drop folder; the server takes its pages down within 2 seconds;
- archived on claude.ai/code: Remote Control ends that session's process (seen on
  this server: archived sessions have no process left), so the server exits too.
  If it ever only disconnects, the lifetime is the backstop.

The tunnel and local server run only while the session has a page. If `cloudflared`
exits, it's restarted and every live page's new link goes to Chat in one message. A
crash or reboot loses pages; that's accepted.

## Install

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
claude mcp add page-drop -s user -e PAGE_DROP_NOTIFY=/home/poraphit/.local/bin/notify \
  -- /home/poraphit/Documents/page-drop-mcp/.venv/bin/python /home/poraphit/Documents/page-drop-mcp/server.py
```

And the `SessionEnd` hook in `~/.claude/settings.json`:

```json
{"hooks": {"SessionEnd": [{"hooks": [{"type": "command",
  "command": "/home/poraphit/Documents/page-drop-mcp/.venv/bin/python /home/poraphit/Documents/page-drop-mcp/server.py end-session"}]}]}}
```

New sessions pick both up; running sessions need a restart.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests            # unit tests, no network
PAGE_DROP_E2E=1 .venv/bin/python -m unittest tests.test_e2e  # real MCP + tunnel + hook (no Chat message)
```
