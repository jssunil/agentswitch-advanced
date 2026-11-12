"""Read-only MCP client for AgentSwitch.

Only tools on the read allowlist can be called. Anything else raises
WriteBlocked before a request is sent, so the report can never change a record.
"""
import hashlib
import json
import os
import urllib.request

READ_SUFFIXES = (".list", ".get")
READ_TOOLS = {"tools.list", "tools.search", "tools.describe", "endpoint.make.thread"}
PAGE_SIZE = 500
MAX_PAGES = 1000


class WriteBlocked(Exception):
    pass


class McpError(Exception):
    pass


class PaginationError(McpError):
    """A list response cannot establish complete, consistent coverage."""


def is_read_tool(name):
    return name in READ_TOOLS or name.endswith(READ_SUFFIXES)


class Client:
    def __init__(self, base_url=None, token=None, timeout=60):
        self.base_url = (base_url or os.environ["AGENTSWITCH_BASE_URL"]).rstrip("/")
        self.token = token or os.environ["AGENTSWITCH_TOKEN"]
        self.timeout = timeout
        self._id = 0

    def _rpc(self, method, params):
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        req = urllib.request.Request(
            self.base_url + "/api/mcp",
            body.encode(),
            {
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read().decode()
        # The server may answer as an SSE stream; keep the last data line.
        for line in raw.splitlines():
            if line.startswith("data:"):
                raw = line[5:]
        msg = json.loads(raw)
        if "error" in msg:
            raise McpError(json.dumps(msg["error"]))
        return msg["result"]

    def tool_names(self):
        return {t["name"] for t in self._rpc("tools/list", {}).get("tools", [])}

    def call(self, name, args=None):
        if not is_read_tool(name):
            raise WriteBlocked(f"{name} is not a read tool; the report is read-only")
        result = self._rpc("tools/call", {"name": name, "arguments": args or {}})
        if result.get("isError"):
            raise McpError(result["content"][0]["text"])
        return result.get("structuredContent") or json.loads(result["content"][0]["text"])

    def list_all(self, entity, filters=None, max_pages=MAX_PAGES):
        """Return rows only after all pages satisfy the advertised integer total.

        Every page must carry data and a consistent nonnegative total. Missing
        metadata, repeated records/pages and early exhaustion are errors, never
        a partial success. A short page alone does not establish completion.
        """
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages <= 0:
            raise ValueError("max_pages must be a positive integer")
        rows, offset = [], 0
        expected_total = None
        seen_ids, seen_pages = set(), set()

        def invalid(reason):
            # Report protocol facts only; never include customer rows or tokens.
            raise PaginationError(f"{entity}.list pagination incomplete: {reason}")

        for _ in range(max_pages):
            page = self.call(f"{entity}.list", {**(filters or {}), "limit": PAGE_SIZE, "offset": offset})
            if not isinstance(page, dict):
                invalid("response is not an object")
            data, total = page.get("data"), page.get("total")
            if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
                invalid("data must be a list of record objects")
            if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                invalid("total must be a nonnegative integer on every page")
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                invalid("total changed during pagination")
            if offset + len(data) > total:
                invalid("returned row count exceeds total")
            if not data and offset < total:
                invalid("empty page before total was reached")
            if data:
                fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).digest()
                if fingerprint in seen_pages:
                    invalid("repeated page")
                seen_pages.add(fingerprint)
                for row in data:
                    if row.get("id") is not None:
                        record_id = json.dumps(row["id"], sort_keys=True)
                        if record_id in seen_ids:
                            invalid("duplicate record id")
                        seen_ids.add(record_id)
            rows.extend(data)
            offset += len(data)
            if offset == total:
                return rows
        invalid("page limit reached before total was reached")
