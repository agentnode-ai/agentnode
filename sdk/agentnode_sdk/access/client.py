"""How something on the USER's machine reaches the sandbox: over the authenticated API.

`MANAGED-ACCESS-DECISION-0001` settled this and it is the part worth not forgetting. The CLI, the
stdio MCP bridge and the SDKs all run where the user is. Anything they enforce is advice: the
person they are protecting the sandbox from is the person who can edit them. So they get no
in-process shortcut, no direct `dispatch()` call, and no access to `GatewayState`, the worker or
the runtime -- they speak HTTP to the gateway exactly as a stranger would, and the answer they get
is the answer anybody would get.

What lives here is therefore a transport and nothing else: take an operation and some parameters,
present the device's token, and hand back what came out. Every decision was made on the other end.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from agentnode_sdk.access import contract, rest


class TheSandboxRefused(Exception):
    """A refusal, carried back with its name intact so a caller can branch on it."""

    def __init__(self, refusal: str, because: str, what_to_do: str = "") -> None:
        super().__init__(because)
        self.refusal = refusal
        self.because = because
        self.what_to_do = what_to_do

    def in_words(self) -> str:
        said = self.because
        if self.what_to_do:
            said += "\n" + self.what_to_do
        return said


class Sandbox:
    """A sandbox somewhere, reached the way anybody reaches it."""

    def __init__(self, base: str, token: str, opener=None, timeout: float = 60.0) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout
        #: Supplied by a caller that pins a certificate. Absent here rather than reimplemented:
        #: pinning already exists in `gateway/pinning.py` and a second version of it would be a
        #: second thing to get wrong.
        self.opener = opener

    def ask(self, operation: str, **params):
        """Carry out an operation, or raise the refusal it came back with."""
        op = contract.find(operation)
        path = self.base + rest.NAMESPACE + operation.replace(".", "/")
        method = "POST" if (op is None or op.changes or op.params) else "GET"
        body = json.dumps(params).encode("utf-8") if method == "POST" else None
        request = urllib.request.Request(path, data=body, method=method)
        request.add_header("Content-Type", "application/json")
        request.add_header(rest.SPEAKS_HEADER, contract.PROTOCOL_VERSION)
        if self.token:
            request.add_header(rest.TOKEN_HEADER, self.token)
        opener = self.opener.open if self.opener is not None else urllib.request.urlopen
        try:
            with opener(request, timeout=self.timeout) as answer:
                return json.loads(answer.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as refused:
            raise _as_refusal(refused) from None
        except urllib.error.URLError as unreachable:
            raise TheSandboxRefused(
                "sandbox_unavailable",
                "Could not reach the sandbox at %s: %s" % (self.base, unreachable.reason),
                "Check that it is running and that this machine can reach it.") from None

    def speak_mcp(self, message: dict) -> dict | None:
        """Send one JSON-RPC message to this sandbox's MCP endpoint and return its answer.

        Raw, not translated. The gateway already speaks MCP; a second translation on this side
        would be a second thing to keep in step, and it was one: the bridge used to rebuild MCP
        out of REST calls, so the gateway recorded the channel as `rest` while the caller was an
        MCP client. An approval given for MCP could then never be spent, which is how the most
        prominent option in the console -- Claude Code or Codex on my machine -- could not
        complete a job at all.
        """
        request = urllib.request.Request(self.base + rest.MCP_PATH,
                                         data=json.dumps(message).encode("utf-8"), method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header(rest.SPEAKS_HEADER, contract.PROTOCOL_VERSION)
        if self.token:
            request.add_header(rest.TOKEN_HEADER, self.token)
        opener = self.opener.open if self.opener is not None else urllib.request.urlopen
        try:
            with opener(request, timeout=self.timeout) as answer:
                body = answer.read().decode("utf-8")
        except urllib.error.HTTPError as refused:
            raise _as_refusal(refused) from None
        except urllib.error.URLError as unreachable:
            raise TheSandboxRefused(
                "sandbox_unavailable",
                "Could not reach the sandbox at %s: %s" % (self.base, unreachable.reason),
                "Check that it is running and that this machine can reach it.") from None
        return json.loads(body) if body.strip() else None

    # -- the journey, named the way a person would say it ------------------------------------

    def capabilities(self):
        return self.ask("capabilities")

    def prepare(self, **what):
        return self.ask("prepare", **what)

    def submit(self, **what):
        return self.ask("submit", **what)

    def status(self, run_id: str):
        return self.ask("status", run_id=run_id)

    def result(self, run_id: str):
        return self.ask("result", run_id=run_id)

    def cancel(self, run_id: str):
        return self.ask("cancel", run_id=run_id)

    def usage(self):
        return self.ask("usage")

    def devices(self):
        return self.ask("devices.list")

    def revoke(self, device_id: str):
        return self.ask("devices.revoke", device_id=device_id)


def _as_refusal(refused) -> TheSandboxRefused:
    try:
        answer = json.loads(refused.read().decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        answer = {}
    return TheSandboxRefused(
        str(answer.get("refused") or "sandbox_unavailable"),
        str(answer.get("because") or ("the sandbox answered %s" % refused.code)),
        str(answer.get("what_to_do") or ""))


# ---------------------------------------------------------------- the local MCP bridge


def bridge(sandbox: Sandbox, incoming, outgoing) -> None:
    """Speak MCP on a pipe, and forward every call over the authenticated API.

    For programs that will only talk to a local MCP server. It is a relay and holds no authority
    of its own: every message is forwarded to the sandbox's own MCP endpoint, so the tool list,
    the refusals and the wording are the sandbox's, not this process's. A tool the device may not
    use never appears, and calling one anyway is refused at the far end.

    FORWARDED, not translated. This used to rebuild MCP out of REST operations, which meant the
    gateway saw `rest` while the caller was an MCP client -- so an approval given for MCP could
    never be spent, and the console's most prominent option could not finish a job. A relay that
    changes what channel you appear to be on is not a relay.
    """
    for line in incoming:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            _write(outgoing, {"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32700, "message": "that is not JSON"}})
            continue
        reply = _forward(sandbox, message)
        if reply is not None:
            _write(outgoing, reply)


def _forward(sandbox: Sandbox, message: dict):
    """Hand one message to the sandbox and give back what it said."""
    method = str(message.get("method") or "")
    if message.get("id") is None and method.startswith("notifications/"):
        return None
    try:
        return sandbox.speak_mcp(message)
    except TheSandboxRefused as refusal:
        # Reaching the sandbox failed, which is not the same as the sandbox refusing a call. It
        # is reported as a transport error so a client retries rather than telling a person their
        # job was rejected.
        return {"jsonrpc": "2.0", "id": message.get("id"),
                "error": {"code": -32000, "message": refusal.in_words()}}


def _write(outgoing, message) -> None:
    outgoing.write(json.dumps(message) + "\n")
    outgoing.flush()
