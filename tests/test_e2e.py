"""End to end: server.py as a real MCP server, a real Cloudflare quick tunnel, and the
SessionEnd hook command. Needs network and cloudflared, so it only runs when asked:

    PAGE_DROP_E2E=1 .venv/bin/python -m unittest tests.test_e2e

No Google Chat message is sent (PAGE_DROP_NOTIFY is cleared for the server).
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def _pages(res):
    if res.structured_content is not None:
        return res.structured_content.get("result", res.structured_content)
    return [json.loads(c.text) for c in res.content]


def get(url):
    with urllib.request.urlopen(url, timeout=15) as r:
        return r.status, r.read().decode()


@unittest.skipUnless(os.environ.get("PAGE_DROP_E2E"), "set PAGE_DROP_E2E=1 to run (needs network)")
class EndToEnd(unittest.TestCase):
    def test_publish_update_and_session_end(self):
        asyncio.run(self._run())

    async def _run(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            runtime = Path(tmp) / "run"
            env = dict(os.environ, PAGE_DROP_NOTIFY="", PAGE_DROP_RUNTIME=str(runtime))
            params = StdioServerParameters(command=PY, args=[str(ROOT / "server.py")], env=env)
            async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
                await s.initialize()
                names = sorted(t.name for t in (await s.list_tools()).tools)
                self.assertEqual(names, ["list_pages", "publish_page", "remove_page", "update_page"])

                res = await s.call_tool("publish_page", {"title": "e2e", "kind": "markdown", "content": "# One"})
                info = json.loads(res.content[0].text)
                self.assertFalse(info["sent_to_chat"])
                url = info["url"]
                self.assertRegex(url, r"^https://[a-z0-9-]+\.trycloudflare\.com/[A-Za-z0-9_-]{22}/$")
                status, body = await asyncio.to_thread(get, url)
                self.assertEqual(status, 200)
                self.assertIn("<h1>One</h1>", body)

                res = await s.call_tool("update_page", {"page_id": info["page_id"], "content": "# Two"})
                self.assertEqual(json.loads(res.content[0].text)["url"], url)
                self.assertIn("<h1>Two</h1>", (await asyncio.to_thread(get, url))[1])

                # The hook command, run from this test's process tree, ends this session's pages.
                hook = subprocess.run([PY, str(ROOT / "server.py"), "end-session"], input="{}",
                                      text=True, capture_output=True, env=env, timeout=30)
                self.assertEqual(hook.returncode, 0, hook.stderr)
                pages = None
                for _ in range(20):
                    pages = _pages(await s.call_tool("list_pages", {}))
                    if not pages:
                        break
                    await asyncio.sleep(0.5)
                self.assertEqual(pages, [])
            # Server exited with the client: its own folder is gone (the shared base stays).
            time.sleep(0.5)
            base = runtime / "page-drop"
            self.assertEqual(list(base.iterdir()) if base.exists() else [], [])


    def test_sigterm_stops_the_tunnel_and_removes_the_folder(self):
        asyncio.run(self._sigterm())

    async def _sigterm(self):
        def tunnels():
            out = subprocess.run(["pgrep", "-f", "cloudflared tunnel"], capture_output=True, text=True)
            return set(out.stdout.split())

        before = tunnels()
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            runtime = Path(tmp) / "run"
            env = dict(os.environ, PAGE_DROP_NOTIFY="", PAGE_DROP_RUNTIME=str(runtime))
            params = StdioServerParameters(command=PY, args=[str(ROOT / "server.py")], env=env)
            try:
                async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
                    await s.initialize()
                    await s.call_tool("publish_page", {"title": "t", "kind": "text", "content": "x"})
                    self.assertTrue(tunnels() - before)
                    (folder,) = (runtime / "page-drop").iterdir()
                    os.kill(int(folder.name.split("-")[1]), 15)
                    for _ in range(40):
                        if not folder.exists() and not (tunnels() - before):
                            break
                        await asyncio.sleep(0.25)
            except BaseException:
                pass  # the client may complain that its server went away
            self.assertFalse(folder.exists())
            self.assertEqual(tunnels() - before, set())


if __name__ == "__main__":
    unittest.main()
