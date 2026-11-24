"""The seat 30 client is read-only: no write, draft or send tool can be called.

Nothing is sent anywhere: the transport is replaced, and a refused tool must not reach it.
"""
import http.server
import json
import threading
import unittest

from brief.client import Client, McpError, WriteBlocked, is_read_tool

TOKEN = "tok-SECRET-123"


class Recording(Client):
    def __init__(self):
        super().__init__("http://x", TOKEN)
        self.sent = []

    def _post(self, body):
        self.sent.append(json.loads(body))
        result = {"structuredContent": {"data": [], "total": 0}}
        return 200, "application/json", json.dumps({"jsonrpc": "2.0", "id": self._id, "result": result})


class ReadOnly(unittest.TestCase):
    def test_reads_are_allowed(self):
        for name in ("Deal.list", "Deal.get", "Pipeline.list", "Party.get", "Activity.list", "Note.list",
                     "tools.list", "tools.search", "tools.describe"):
            self.assertTrue(is_read_tool(name), name)

    def test_writes_are_refused_before_sending(self):
        c = Recording()
        for name in ("Deal.update", "Deal.create", "Deal.delete", "Activity.create", "Note.create",
                     "Deal.list_and_update", "endpoint.make.thread", "email.send", "Deal.listx",
                     # write tools the seat 30 login advertises (review, 24 Nov)
                     "Party.create", "Party.update", "PartyRelationship.create", "PartyRelationship.update",
                     "KBNote.create", "KBNote.update", "KBTag.update", "KBVault.create"):
            with self.assertRaises(WriteBlocked, msg=name):
                c.call(name, {"id": "d1"})
        self.assertEqual(c.sent, [])

    def test_list_all_pages_through_a_read(self):
        c = Recording()
        self.assertEqual(c.list_all("Deal"), [])
        self.assertEqual(c.sent[0]["params"]["name"], "Deal.list")

    def test_errors_never_show_the_token(self):
        bad = "tok-SECRET\x01-123"
        with self.assertRaises(Exception) as e:
            Client("http://x", bad)
        self.assertNotIn("SECRET", str(e.exception))



class Redirects(unittest.TestCase):
    """Bug hunt BH-03: a redirect to another host must not receive the token."""

    def test_a_redirect_is_refused_and_the_token_stays_here(self):
        seen = []

        class Other(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("Authorization"))
                self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
            do_POST = do_GET

            def log_message(self, *a):
                pass

        other = http.server.HTTPServer(("127.0.0.1", 0), Other)
        servers = [other]
        try:
            threading.Thread(target=other.serve_forever, daemon=True).start()
            for code in (301, 302, 303, 307, 308):
                class Platform(http.server.BaseHTTPRequestHandler):
                    def do_POST(self, code=code):
                        self.rfile.read(int(self.headers.get("Content-Length", 0)))
                        self.send_response(code)
                        self.send_header("Location", f"http://localhost:{other.server_port}/x")
                        self.send_header("Content-Length", "0")
                        self.end_headers()

                    def log_message(self, *a):
                        pass

                platform = http.server.HTTPServer(("127.0.0.1", 0), Platform)
                servers.append(platform)
                threading.Thread(target=platform.serve_forever, daemon=True).start()
                with self.assertRaises(McpError) as e:
                    Client(f"http://127.0.0.1:{platform.server_port}", TOKEN).tool_names()
                self.assertIn("redirect", str(e.exception))
                self.assertNotIn(TOKEN, str(e.exception))
        finally:
            for s in servers:
                s.shutdown(); s.server_close()
        self.assertEqual(seen, [])

if __name__ == "__main__":
    unittest.main()
