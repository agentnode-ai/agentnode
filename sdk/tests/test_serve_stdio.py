"""Task #5b: ``agentnode serve`` works on the MCP SDK a fresh install gets.

The server process is started for real and spoken to over stdio with plain
newline-delimited JSON-RPC, so the test does not depend on either mcp major
version's client API. It runs under whatever ``mcp`` the interpreter has: the
project venv (mcp 1.x) and a separate venv with mcp 2.x both have to pass.
A synthetic skill; no network.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tests.test_mcp_server import _create_skill

SDK_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(json.dumps({"version": "0.1"}))
    lock = tmp_path / "agentnode.lock"
    lock.write_text(json.dumps({"lockfile_version": "0.1", "packages": {}}))
    values = {
        "AGENTNODE_CONFIG": str(cfg_dir / "config.json"),
        "AGENTNODE_LOCKFILE": str(lock),
        "AGENTNODE_CONFIG_DIR": str(cfg_dir),
    }
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    _create_skill(tmp_path)
    return dict(os.environ, PYTHONPATH=str(SDK_ROOT), **values)


class _Serve:
    def __init__(self, env):
        self.proc = subprocess.Popen(
            [sys.executable, "-c",
             "from agentnode_sdk.cli.serve import cmd_serve; raise SystemExit(cmd_serve())"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, text=True, encoding="utf-8",
        )
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.next_id = 0

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.put(line)

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.next_id += 1
            msg["id"] = self.next_id
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        if notify:
            return None
        while True:
            try:
                line = self.lines.get(timeout=20)
            except queue.Empty:
                err = self.proc.stderr.read() if self.proc.poll() is not None else ""
                raise AssertionError(f"no answer to {method}; server stderr: {err[-2000:]}")
            reply = json.loads(line)
            if reply.get("id") == self.next_id:
                return reply

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


def test_serve_lists_and_renders_skills_over_stdio(env):
    s = _Serve(env)
    try:
        init = s.send("initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "smoke-client", "version": "1"},
        })
        assert "result" in init, init
        s.send("notifications/initialized", notify=True)

        prompts = s.send("prompts/list", {})["result"]["prompts"]
        assert "test-skill/main-prompt" in [p["name"] for p in prompts], prompts

        got = s.send("prompts/get", {"name": "test-skill/main-prompt",
                                     "arguments": {"topic": "release notes"}})
        text = got["result"]["messages"][0]["content"]["text"]
        assert "release notes" in text, got

        bad = s.send("prompts/get", {"name": "test-skill/does-not-exist"})
        assert "error" in bad or bad.get("result", {}).get("isError"), bad

        resources = s.send("resources/list", {})["result"]["resources"]
        uris = [r["uri"] for r in resources]
        assert "agentnode://skills/test-skill/assets/example-doc" in uris, uris

        read = s.send("resources/read", {"uri": "agentnode://skills/test-skill/assets/example-doc"})
        assert "Sample content." in read["result"]["contents"][0]["text"], read
    finally:
        s.close()
