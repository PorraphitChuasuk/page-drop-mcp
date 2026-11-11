"""Turns a page's content into the HTML a browser gets.

    text      plain text, kept as written, in a wrapping monospace block
    markdown  rendered on the server, GitHub-style lists accepted
    html      served as given; a fragment only gets charset + viewport, nothing visible
"""
from __future__ import annotations

import html
import re
from datetime import datetime

import markdown

KINDS = ("text", "markdown", "html")

_FENCE = re.compile(r"^\s*(```|~~~)")
_FULL_DOC = re.compile(r"^\s*(<!doctype\b|<html[\s>])", re.I)


def render(kind: str, content: str, title: str, updated: datetime | None = None) -> str:
    if kind == "html":
        return _html_page(content, title)
    if kind == "text":
        body = '<pre class="text">%s</pre>' % html.escape(content)
    elif kind == "markdown":
        body = '<article>%s</article>' % markdown.markdown(
            normalise_markdown(content), extensions=["sane_lists", "tables", "fenced_code"])
    else:
        raise ValueError("kind must be one of %s" % ", ".join(KINDS))
    stamp = '<div class="sub">Updated %s</div>' % updated.strftime("%d %b %H:%M BKK") if updated else ""
    return _PAGE % {"title": html.escape(title), "stamp": stamp, "body": body}


def _html_page(content: str, title: str) -> str:
    # Nothing visible may be added: Ctrl+A / Ctrl+C has to copy exactly the content.
    if _FULL_DOC.search(content):
        return content
    return ('<!doctype html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>%s</title></head><body>%s</body></html>' % (html.escape(title), content))


def _is_list_line(line: str) -> bool:
    s = line.lstrip(" ")
    return s.startswith(("- ", "* ", "+ ")) or bool(re.match(r"\d{1,3}[.)] ", s))


def normalise_markdown(text: str) -> str:
    """Accept what GitHub accepts but python-markdown doesn't, outside fenced code:
    nested lists indented 3 spaces (it wants 4), and a list starting straight under a
    non-list line (it wants a blank line first)."""
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if indent and indent % 3 == 0 and indent % 4:
            line = " " * (indent // 3 * 4) + stripped
        if out and _is_list_line(line) and out[-1].strip() and not _is_list_line(out[-1]):
            out.append("")
        out.append(line)
    return "\n".join(out)


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>%(title)s</title>
<style>
:root { --bg: #fbfaf8; --fg: #1f2328; --muted: #656d76; --card: #ffffff; --line: #e3e1dc;
        --code-bg: #f1efea; --link: #0b5cad; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #16171a; --fg: #e6e6e3; --muted: #9a9ea6; --card: #1e2024; --line: #33363c;
          --code-bg: #2a2d33; --link: #79b8ff; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
       font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 20px 16px 64px; }
h1.title { font-size: 1.3rem; margin: 0 0 2px; }
.sub { color: var(--muted); font-size: .85rem; margin-bottom: 16px; }
article, pre.text { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; }
pre.text { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0;
           font: 14px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace; }
a { color: var(--link); }
article h1, article h2, article h3 { line-height: 1.3; margin: 20px 0 8px; }
article ol, article ul { padding-left: 1.3em; }
article li { margin: 6px 0; }
article table { border-collapse: collapse; display: block; overflow-x: auto; }
article th, article td { border: 1px solid var(--line); padding: 6px 10px; text-align: left; }
code { font: .86em/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--code-bg);
       padding: 1px 5px; border-radius: 5px; overflow-wrap: anywhere; }
pre code { display: block; padding: 10px 12px; overflow-x: auto; }
blockquote { margin: 0; padding-left: 12px; border-left: 3px solid var(--line); color: var(--muted); }
</style>
</head>
<body>
<main>
<h1 class="title">%(title)s</h1>
%(stamp)s
%(body)s
</main>
</body>
</html>
"""
