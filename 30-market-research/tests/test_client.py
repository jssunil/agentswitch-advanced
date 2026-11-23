"""The seat 30 client is read-only: no write, draft or send tool can be called.

Nothing is sent anywhere: the transport is replaced, and a refused tool must not reach it.
"""
import json
import unittest

from brief.client import Client, WriteBlocked, is_read_tool

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
                     "Deal.list_and_update", "endpoint.make.thread", "email.send", "Deal.listx"):
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


if __name__ == "__main__":
    unittest.main()
