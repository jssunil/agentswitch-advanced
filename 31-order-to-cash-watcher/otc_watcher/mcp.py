"""Minimal read-only MCP client. Credentials come from environment variables, never from the repo.

For system SURYODAYA or KEYSTONE set: <SYS>_URL, <SYS>_MCP_URL, <SYS>_USERNAME, <SYS>_PASSWORD.
`load_env_file` can read the same KEY=value lines from a local file kept outside the repo.
"""
import json
import os
import time

import requests


def load_env_file(path):
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key, value)


def _retry(fn, attempts=5):
    # Suryodaya /api/mcp times out intermittently; back off and retry connection errors only.
    for i in range(attempts):
        try:
            return fn()
        except (requests.ConnectTimeout, requests.ConnectionError):
            if i == attempts - 1:
                raise
            time.sleep(2 ** i)


class MCPClient:
    def __init__(self, system, post=requests.post):
        sys_ = system.upper()
        env = lambda k: os.environ[f"{sys_}_{k}"]
        self._post, self._n, self.system = post, 0, system
        self.mcp_url = env("MCP_URL")
        r = _retry(lambda: post(env("URL") + "/api/auth/login",
                                json={"email": env("USERNAME"), "password": env("PASSWORD")},
                                timeout=(15, 30)))
        r.raise_for_status()
        self._headers = {"Authorization": "Bearer " + r.json()["token"],
                         "Content-Type": "application/json",
                         "Accept": "application/json, text/event-stream"}

    def call(self, tool, **arguments):
        self._n += 1
        body = {"jsonrpc": "2.0", "id": self._n, "method": "tools/call",
                "params": {"name": tool, "arguments": arguments}}
        r = _retry(lambda: self._post(self.mcp_url, headers=self._headers, json=body, timeout=(15, 60)))
        if "event-stream" in r.headers.get("content-type", ""):
            msg = next(json.loads(l[5:]) for l in r.text.splitlines() if l.startswith("data:"))
        else:
            msg = r.json()
        if "error" in msg:
            raise RuntimeError(msg["error"])
        result = msg["result"]
        text = "".join(c.get("text", "") for c in result.get("content", []))
        data = json.loads(text) if text else None
        if result.get("isError"):
            raise RuntimeError(data)
        return data

    def list_all(self, tool, page=1000, **filters):
        rows, offset = [], 0
        while True:
            d = self.call(tool, limit=page, offset=offset, **filters)
            rows += d["data"]
            offset += len(d["data"])
            if not d["data"] or offset >= d["total"]:
                return rows
