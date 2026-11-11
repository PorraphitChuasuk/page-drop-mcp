# page-drop: show Claude's output as a web page you can open on your phone

Design, 2026-11-11. Owner: Poraphit. Status: approved 2026-11-11.

## Why

Poraphit drives this headless server from a phone through Remote Control. Long
output (review drafts, email replies) is hard to read in the chat, and an HTML
email can't be copied out of it with its formatting. On 11 Nov a session
rendered three PR review drafts by hand (python-markdown, `http.server`,
`cloudflared` quick tunnel, `notify`) so he could read them in full. page-drop
makes that a tool any Claude session on the server can call.

It is deliberately small. It is not Claude Artifacts: no comments, no
versions, no editing on the page, no sign-in.

## What it does

An MCP server (stdio, user scope, like `secret-drop`) with these tools:

| Tool | Does |
|---|---|
| `publish_page` | Publishes content as a page and returns its `page_id` and URL. |
| `update_page` | Replaces a page's content (or title, or lifetime). Same URL. |
| `list_pages` | This session's pages: id, title, kind, URL, when it closes. |
| `remove_page` | Takes a page down now. |

Content is either passed inline (`content`) or as a file (`file_path`). A
page made from a file **follows the file**: every request reads the file
again, so when the session edits the file the page updates with no tool call.
Inline pages update only through `update_page`.

Which files can be published (my call, on the safe side, since a page is open
to anyone with its link for up to a day): only regular files under `/tmp`
(which holds the Claude scratchpads) or `~/Documents`, after resolving
symlinks, and never a dotfile or a file in a dot-folder, an `.env` file
(`.env`, `.env.*`, `*.env`), a key or certificate (`*.pem`, `*.key`), or
anything under `~/.config/secrets`, `~/.aws` or `~/.ssh`. The check runs at
publish time and again on every read. Inline content can't be checked this
way; the tool description tells Claude never to publish secrets.

### The three kinds of page (Poraphit's)

1. **`text`**: plain text, kept as written (line breaks and spacing), in a
   readable font that wraps on a phone. HTML in it is shown, not run.
2. **`markdown`**: rendered to HTML on the server, styled like the 11 Nov
   review-drafts page (`scratchpad/render_reviews.py` in that session):
   readable width, headings, lists, code, tables, light and dark mode. It
   must accept GitHub-style Markdown: nested lists indented 3 spaces, and a
   list that starts straight under a paragraph line. python-markdown needs
   both normalised (the 11 Nov renderer did this; that code is the starting
   point).
3. **`html`**: served as it is, with nothing added around it, so Ctrl+A then
   Ctrl+C copies exactly the content (e.g. an email reply to paste into
   Gmail). A fragment with no `<html>` gets only a charset and viewport
   wrapper, no visible header, footer or buttons. Poraphit copies these in a
   **desktop browser into Gmail on the web**, so no copy button is needed;
   the tool description tells Claude to write email HTML that survives that
   paste (inline styles, simple elements).

### Links

- The URL is `https://<random>.trycloudflare.com/<token>/`, where `<token>` is
  at least 128 random bits per page. No sign-in (Poraphit: an unguessable
  link is good enough). There is no index: `/` and unknown paths return 404.
- The domain is a Cloudflare quick tunnel, so it changes whenever the tunnel
  restarts. Poraphit accepts that.
- Every tool result that creates or updates a page includes the current URL,
  and the tool descriptions tell Claude to put the URL in its reply
  (Poraphit: "show the url directly in reply").
- When a page is first published, its URL also goes to Google Chat through
  `notify` (Poraphit). Updates don't send a message, except when the domain
  changed since the URL was last sent: then the old link is dead, so the new
  one goes to Chat again (my call).

### Lifetime and cleanup (Poraphit's rules)

- Every page has a lifetime: **8 hours by default**, so Claude doesn't have to
  pick one each time, and **at most 1 day** (Poraphit). Claude can set a
  different lifetime when publishing or updating; anything over 1 day is
  capped at 1 day, and the result says when the page closes.
- A page closes at the end of its lifetime or when its session ends,
  whichever comes first. Nothing outlives its session. "Its session ends"
  means any of (Poraphit):
  - the Claude Code session exits;
  - Poraphit runs `/clear` in it;
  - Poraphit archives the session on claude.ai/code.
- `/clear` doesn't restart MCP servers, so page-drop can't see it by itself.
  Claude Code fires a `SessionEnd` hook on `/clear` (reason `clear`; also on
  exit and logout). page-drop ships a small command, registered once as a
  user-scope `SessionEnd` hook, that takes down every page of the session the
  hook fired in. The hook and that session's page-drop process are both
  children of the same Claude Code process, so the hook can find it (e.g. the
  server records its parent PID and the hook signals the page-drop process
  with the same parent); the exact mechanism is the builder's. Pages made
  after the `/clear` belong to the fresh conversation and live on.
- Archiving on claude.ai/code: what it does to the local process on this
  server is not known yet. **The first build step checks it** with a
  throwaway session: if archiving ends the process or fires `SessionEnd`,
  the rules above cover it; if it only disconnects, the lifetime (8 h default,
  1 day max) is the backstop, and the README says so.
- Each Claude session runs its own copy of the MCP server, so "the session
  closes" means that process exits. It then removes its pages and stops its
  tunnel.
- If the process dies uncleanly (crash, OOM kill), its tunnel must still stop
  (secret-drop's watchdog shell loop does this) and its files are removed the
  next time any page-drop process starts (sweep folders whose owner PID is
  gone).
- A server reboot or crash may lose pages. Poraphit accepts that, so pages can
  live in a temporary folder and nothing needs to survive a restart.
- A resumed session (`claude --resume`) starts with no pages.

### One tunnel per session (my call)

Each MCP process starts its own HTTP server (127.0.0.1, random port) and
quick tunnel when it publishes its first page, and stops both when its last
page is gone. This changes the earlier idea of one shared server for all
sessions: per-session gives cleanup-on-close for free and has no locking
between sessions. Cost: up to one `cloudflared` per session that has a live
page, which is small next to the 14 GB box. Different sessions get different
domains; that's fine.

If `cloudflared` exits while pages are live, page-drop restarts it on its own
(it's a child process, so the exit is seen with no polling) and sends every
live page's new link to Google Chat in one message, since a page that follows
a file may never get another tool call (my call). A tunnel that hangs while
still running is not watched for; the next tool call checks the tunnel
answers and restarts it if not.

## Goals

- One call from any session gives Poraphit a link he can open on his phone.
- The same link shows updated content after a later prompt.
- HTML pages copy cleanly into Gmail.
- Nothing keeps running once a session has no pages or has closed.

## Non-goals

- Sign-in or access control beyond the unguessable token.
- A fixed domain, or links that survive a tunnel restart or a reboot.
- Comments, versions, history, or editing in the browser.
- Live auto-refresh: Poraphit reloads the page (text and Markdown pages may
  show "updated HH:MM BKK"; HTML pages show nothing extra).
- Pages shared between sessions, or a session updating another's page.
- Images, attachments or multi-file sites.

## Anti-goals (fails even if it works)

- A tunnel or server left running after its session ends.
- Anything added around an HTML page that ends up in the copied email.
- A page reachable by listing or guessing, or a `file_path` page that can
  serve any file other than the one it was published from (no path
  traversal, no directory listing).
- A stale page after an update because something cached it: responses send
  `Cache-Control: no-store`, `X-Robots-Tag: noindex` and
  `Referrer-Policy: no-referrer`.
- Looking up the new tunnel hostname before it's published: resolvers cache
  the "no such name" for minutes here and on the phone. Reuse secret-drop's
  `tunnel.py` approach (wait for "Registered tunnel connection", then ask
  trycloudflare.com's nameservers directly, then check the page answers).

## Constraints

- Shared server: 14 GB RAM, up to 5 Claude sessions. Keep it light (no
  Docker, no build step).
- Same stack as secret-drop (my call): Python 3.14 in a `.venv`, MCP 2.x
  (`MCPServer`), `cloudflared` from Cloudflare's apt repo (already
  installed), `dnspython`, `markdown`.
- Notifier path comes from an env var set at registration
  (`PAGE_DROP_NOTIFY=/home/poraphit/.local/bin/notify`), the same pattern as
  `SECRET_DROP_NOTIFY`. Without it, the tool only returns the URL.
- Registered once at user scope (`claude mcp add page-drop -s user …`). New
  sessions pick it up; running sessions only after a restart.
- Lives in `~/Documents/page-drop-mcp` (this repo). Local git only for now;
  pushing it to GitHub is Poraphit's call later (my call).

## Left to the builder

- Tool parameter names and result shape (keep them few and obvious).
- Whether `publish_page` and `update_page` share code or one tool takes an
  optional `page_id`.
- Size limit per page (something like a few MB) and the error text.
- How the lifetime is given (hours or minutes from now is fine) and how
  expiry is scheduled. Whether an update restarts the lifetime clock or keeps
  the original close time.
- Where the temporary folder is (`$XDG_RUNTIME_DIR` or `/tmp`, per process).
- Markdown extensions beyond lists, tables and fenced code.
- Tests: rendering of the three kinds, the GitHub-list normalisation, token
  and path handling, the file allow/deny rules, expiry, the `SessionEnd`
  hook, cleanup on exit and the stale-folder sweep; the tunnel can be faked
  in tests.
- Registering the `SessionEnd` hook in `~/.claude/settings.json` (keep any
  hooks already there).
- README with registration steps.
