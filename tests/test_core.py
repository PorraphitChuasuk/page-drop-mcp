"""Rendering, the file rules and the page store (stdlib unittest, no network).

    .venv/bin/python -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import files  # noqa: E402
from pages import MAX_HOURS, PageError, PageStore  # noqa: E402
from render import normalise_markdown, render  # noqa: E402


class Render(unittest.TestCase):
    def test_text_is_escaped_and_kept_as_written(self):
        out = render("text", "a <b>bold</b>\n  indented", "T")
        self.assertIn("a &lt;b&gt;bold&lt;/b&gt;\n  indented", out)
        self.assertNotIn("<b>bold</b>", out)

    def test_markdown_github_lists(self):
        md = "Intro line:\n- one\n- two\n\n1. **First**\n   - nested a\n   - nested b\n2. Second\n"
        out = render("markdown", md, "T")
        self.assertEqual(out.count("<li>"), 6)
        self.assertIn("<ol>", out)
        self.assertRegex(out, r"<li><strong>First</strong>\s*<ul>")

    def test_list_after_indented_paragraph_starts_a_new_item(self):
        md = "1. Item one\n   - sub\n\n   More text\n   still more\n2. Item two\n3. Item three\n"
        out = render("markdown", md, "T")
        self.assertEqual(out.count("<ol>"), 1)
        self.assertEqual(out.count("<li>"), 4)

    def test_fenced_code_is_not_touched(self):
        md = "```\n   - not a list\ntext\n- also code\n```\n"
        self.assertEqual(normalise_markdown(md), md.rstrip("\n"))
        self.assertIn("   - not a list", render("markdown", md, "T"))

    def test_html_fragment_gets_no_visible_chrome(self):
        out = render("html", "<p>Hi <b>there</b></p>", "Reply")
        self.assertIn("<body><p>Hi <b>there</b></p></body>", out)
        self.assertNotIn("<h1", out)
        self.assertNotIn("Updated", out)

    def test_full_html_document_is_served_as_is(self):
        doc = "<!DOCTYPE html><html><body><p>x</p></body></html>"
        self.assertEqual(render("html", doc, "T"), doc)

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            render("pdf", "x", "T")


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.roots = (self.root,)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text="x"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def test_plain_file_is_allowed(self):
        p = self.write("drafts/review.md")
        self.assertEqual(files.check(p, self.roots), p)

    def test_refusals(self):
        refused = [self.write(".hidden.md"), self.write(".git/config"), self.write("a/.env"),
                   self.write("a/.env.local"), self.write("a/prod.env"), self.write("a/server.pem"),
                   self.write("a/id.key")]
        for p in refused:
            with self.subTest(p=p), self.assertRaises(files.FileRefused):
                files.check(p, self.roots)

    def test_outside_roots_and_relative_and_missing(self):
        with tempfile.NamedTemporaryFile(dir=os.path.expanduser("~"), delete=True) as f:
            with self.assertRaises(files.FileRefused):
                files.check(f.name, self.roots)
        with self.assertRaises(files.FileRefused):
            files.check("drafts/review.md", self.roots)
        with self.assertRaises(files.FileRefused):
            files.check(self.root / "nope.md", self.roots)
        with self.assertRaises(files.FileRefused):
            files.check(self.root, self.roots)

    def test_symlink_out_of_roots_is_refused(self):
        with tempfile.NamedTemporaryFile(dir=os.path.expanduser("~"), suffix=".md") as outside:
            link = self.root / "link.md"
            link.symlink_to(outside.name)
            with self.assertRaises(files.FileRefused):
                files.check(link, self.roots)

    def test_symlink_to_secret_is_refused_even_inside_roots(self):
        secret = self.write("vault/.env", "KEY=1")
        link = self.root / "notes.md"
        link.symlink_to(secret)
        with self.assertRaises(files.FileRefused):
            files.check(link, self.roots)

    def test_too_big(self):
        p = self.write("big.txt", "x" * (files.MAX_BYTES + 1))
        with self.assertRaises(files.FileRefused):
            files.check(p, self.roots)


class Store(unittest.TestCase):
    def setUp(self):
        self.now = 1_000_000.0
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.store = PageStore(clock=lambda: self.now, roots=(self.root,))

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_lifetime_is_8_hours_and_cap_is_1_day(self):
        p = self.store.publish("A", content="x")
        self.assertEqual(p.expires - self.now, 8 * 3600)
        q = self.store.publish("B", content="x", hours=72)
        self.assertEqual(q.expires - self.now, MAX_HOURS * 3600)

    def test_bad_requests(self):
        for kwargs in ({}, {"content": "x", "file_path": "/tmp/x"}, {"content": "x", "hours": 0},
                       {"content": "x", "kind": "pdf"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(PageError):
                self.store.publish("T", **kwargs)

    def test_ids_and_tokens_are_unique_and_long(self):
        a, b = self.store.publish("A", content="x"), self.store.publish("B", content="x")
        self.assertNotEqual(a.id, b.id)
        self.assertNotEqual(a.token, b.token)
        self.assertGreaterEqual(len(a.token), 22)  # 16 random bytes
        self.assertIs(self.store.by_token(a.token), a)
        self.assertIsNone(self.store.by_token("nope"))

    def test_update_keeps_link_and_close_time_unless_hours_given(self):
        p = self.store.publish("A", content="one")
        expires, token = p.expires, p.token
        self.now += 3600
        self.store.update(p.id, content="two")
        self.assertEqual((p.content, p.token, p.expires), ("two", token, expires))
        self.store.update(p.id, hours=2)
        self.assertEqual(p.expires, self.now + 2 * 3600)
        with self.assertRaises(PageError):
            self.store.update("p-unknown", content="x")

    def test_file_page_follows_the_file_and_infers_kind(self):
        f = self.root / "draft.md"
        f.write_text("# One")
        p = self.store.publish("", file_path=str(f))
        self.assertEqual((p.kind, p.title), ("markdown", "draft.md"))
        self.assertIn("<h1>One</h1>", self.store.body(p))
        f.write_text("# Two")
        self.assertIn("<h1>Two</h1>", self.store.body(p))

    def test_expire_and_remove(self):
        a = self.store.publish("A", content="x", hours=1)
        b = self.store.publish("B", content="x")
        self.now += 3600
        self.assertEqual([p.id for p in self.store.expire()], [a.id])
        self.store.remove(b.id)
        self.assertEqual(self.store.pages(), [])


if __name__ == "__main__":
    unittest.main()
