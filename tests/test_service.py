"""The page server, session wiring and tunnel lifecycle, with a fake tunnel and notifier.

    .venv/bin/python -m unittest discover -s tests
"""
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import session  # noqa: E402
from pages import PageStore  # noqa: E402
from service import PageDrop  # noqa: E402


class FakeTunnel:
    def __init__(self):
        self.url, self.running, self.hung, self.starts, self.stops = None, False, False, 0, 0

    def start(self, port):
        if not self.running:
            self.starts += 1
            self.running, self.hung = True, False
            self.url = "https://gen%d.example" % self.starts
        return self.url

    def alive(self):
        return self.running

    def answers(self):
        return self.running and not self.hung

    def stop(self):
        if self.running:
            self.stops += 1
        self.running, self.url = False, None

    def die(self):  # cloudflared exits on its own; the URL attribute stays like the real one
        self.running = False


class Notifier:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def __call__(self, text, links):
        self.calls.append((text, links))
        return (True, "") if self.ok else (False, "no webhook")


def fetch(port, path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=5) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.read().decode(), dict(e.headers)


class Service(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name).resolve()
        self.now = 2_000_000.0
        self.tunnel, self.notifier = FakeTunnel(), Notifier()
        self.owner = session.Owner(base=self.dir / "run", session=4242)
        self.drop = PageDrop(store=PageStore(clock=lambda: self.now, roots=(self.dir,)),
                             tunnel=self.tunnel, owner=self.owner, notifier=self.notifier, tick=3600)

    def tearDown(self):
        self.drop.close()
        self.tmp.cleanup()

    def token(self, info):
        return info["url"].rstrip("/").rsplit("/", 1)[1]

    def test_publish_serves_the_page_and_sends_the_link_once(self):
        info = self.drop.publish("Review draft", "markdown", "# Hello")
        self.assertTrue(info["url"].startswith("https://gen1.example/"))
        self.assertTrue(info["sent_to_chat"])
        self.assertEqual(self.notifier.calls[0][1], [("Open the page", info["url"])])
        port = self.drop.server.port
        status, body, headers = fetch(port, "/%s/" % self.token(info))
        self.assertEqual(status, 200)
        self.assertIn("<h1>Hello</h1>", body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(fetch(port, "/")[0], 404)
        self.assertEqual(fetch(port, "/" + "A" * 22 + "/")[0], 404)
        self.assertEqual(fetch(port, "/healthz")[0], 200)

    def test_html_page_is_exactly_the_content(self):
        doc = "<!doctype html><html><body><p style='color:red'>Hi</p></body></html>"
        info = self.drop.publish("Reply", "html", doc)
        self.assertEqual(fetch(self.drop.server.port, "/%s/" % self.token(info))[1], doc)

    def test_update_keeps_the_link_and_sends_nothing(self):
        info = self.drop.publish("A", "text", "one")
        out = self.drop.update(info["page_id"], content="two")
        self.assertEqual((out["url"], out["link_changed"], out["sent_to_chat"]), (info["url"], False, False))
        self.assertEqual(len(self.notifier.calls), 1)
        self.assertIn("two", fetch(self.drop.server.port, "/%s/" % self.token(info))[1])

    def test_dead_tunnel_is_restarted_and_all_new_links_sent_in_one_message(self):
        a = self.drop.publish("A", "text", "a")
        b = self.drop.publish("B", "text", "b")
        self.tunnel.die()
        self.drop.sweep()
        self.assertEqual(self.tunnel.starts, 2)
        text, links = self.notifier.calls[-1]
        self.assertIn("restarted", text)
        self.assertEqual([l[1] for l in links], [a["url"].replace("gen1", "gen2"), b["url"].replace("gen1", "gen2")])
        self.assertFalse(self.drop.update(a["page_id"], content="a2")["link_changed"])

    def test_hung_tunnel_found_on_update_gives_a_new_link(self):
        info = self.drop.publish("A", "text", "a")
        self.tunnel.hung = True
        out = self.drop.update(info["page_id"], content="b")
        self.assertTrue(out["link_changed"])
        self.assertTrue(out["url"].startswith("https://gen2.example/"))
        self.assertIn("new link", self.notifier.calls[-1][0])

    def test_failed_chat_send_is_reported_and_retried_on_update(self):
        self.notifier.ok = False
        info = self.drop.publish("A", "text", "a")
        self.assertEqual((info["sent_to_chat"], info["not_sent_because"]), (False, "no webhook"))
        self.notifier.ok = True
        self.assertTrue(self.drop.update(info["page_id"], content="b")["sent_to_chat"])

    def test_session_end_marker_takes_everything_down(self):
        self.drop.publish("A", "text", "a")
        with mock.patch.object(session, "session_pid", return_value=4242):
            marked = session.end_session(base=self.dir / "run")
        self.assertEqual(marked, [self.owner.folder])
        self.drop.sweep()
        self.assertEqual(self.drop.list(), [])
        self.assertFalse(self.tunnel.running)
        self.assertIsNone(self.drop.server.port)

    def test_other_sessions_are_not_touched(self):
        self.drop.publish("A", "text", "a")
        with mock.patch.object(session, "session_pid", return_value=9999):
            self.assertEqual(session.end_session(base=self.dir / "run"), [])
        self.drop.sweep()
        self.assertEqual(len(self.drop.list()), 1)

    def test_expiry_and_last_removal_go_offline(self):
        self.drop.publish("A", "text", "a", hours=1)
        b = self.drop.publish("B", "text", "b")
        self.now += 3600
        self.drop.sweep()
        self.assertEqual([p["page_id"] for p in self.drop.list()], [b["page_id"]])
        self.assertTrue(self.tunnel.running)
        self.drop.remove(b["page_id"])
        self.assertFalse(self.tunnel.running)

    def test_file_page_turned_into_a_symlink_to_a_secret_stops_serving(self):
        f = self.dir / "draft.md"
        f.write_text("# Draft")
        info = self.drop.publish("", file_path=str(f))
        secret = self.dir / "vault" / ".env"
        secret.parent.mkdir()
        secret.write_text("KEY=1")
        f.unlink()
        f.symlink_to(secret)
        status, body, _ = fetch(self.drop.server.port, "/%s/" % self.token(info))
        self.assertEqual(status, 404)
        self.assertNotIn("KEY=1", body)

    def test_close_removes_the_folder(self):
        self.drop.close()
        self.assertFalse(self.owner.folder.exists())


class Session(unittest.TestCase):
    def test_sweep_removes_folders_of_dead_processes_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            dead = subprocess.Popen(["true"])
            dead.wait()
            (base / ("1-%d" % dead.pid)).mkdir()
            (base / ("1-%d" % os.getpid())).mkdir()
            (base / "unrelated").mkdir()
            session.sweep(base)
            self.assertEqual(sorted(p.name for p in base.iterdir()), sorted(["1-%d" % os.getpid(), "unrelated"]))

    def test_parent_of_and_session_pid(self):
        self.assertEqual(session.parent_of(os.getpid()), os.getppid())
        found = session.session_pid()
        if found is not None:  # only when the tests run under Claude Code
            self.assertTrue(session.is_claude(found))


if __name__ == "__main__":
    unittest.main()
