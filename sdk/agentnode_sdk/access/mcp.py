"""The MCP door. JSON-RPC in, `dispatch` out, and no rules of its own.

An AI that speaks MCP asks this for a list of tools and then calls them. Both come from the
contract: the list is generated from the declarations, and a call goes to the same dispatcher the
REST door uses. Nothing here decides whether something is allowed.

## What is deliberately NOT a tool

Making an invitation is not offered. It is the operation that brings a NEW device into the
sandbox, and offering it as a tool would mean any AI a person had connected could mint access for
something else -- quietly, as one tool call among many, in a transcript nobody reads closely.
Pairing stays where a person does it deliberately, and it is not in the contract at all, so there
is no list for it to leak into.

That is also why the tool list is filtered by what the calling DEVICE holds rather than by what
the service can do. An AI is handed exactly the operations that device may use, so a tool it
cannot call never appears and never has to be refused.
"""
from __future__ import annotations

from agentnode_sdk.access import contract, dispatch, schemas

#: The MCP protocol revision this speaks. Separate from AgentNode's own contract version: they
#: move for different reasons and conflating them would make one of them lie.
MCP_PROTOCOL = "2025-06-18"

#: JSON-RPC error codes. `-32602` is "invalid params" and `-32601` is "method not found"; the rest
#: of what can go wrong is an AgentNode refusal and travels as one, in the tool result, because a
#: transport-level error tells an AI only that something broke.
INVALID_PARAMS = -32602
METHOD_NOT_FOUND = -32601


def tools_for(principal) -> list:
    """The tools this device may actually call -- not everything the service has."""
    allowed = {op.name for op in contract.for_capabilities(principal.capabilities)}
    return [tool for tool in schemas.mcp_tools()
            if _operation_of(tool["name"]) in allowed]


def _operation_of(tool_name: str) -> str:
    for op in contract.OPERATIONS:
        if schemas.tool_name_for(op.name) == tool_name:
            return op.name
    return ""


def handle(service, message: dict, principal) -> dict | None:
    """One JSON-RPC message. Returns the reply, or None for a notification."""
    method = str(message.get("method") or "")
    call_id = message.get("id")
    if call_id is None and method.startswith("notifications/"):
        return None

    if method == "initialize":
        return _ok(call_id, {
            "protocolVersion": MCP_PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "agentnode-sandbox",
                           "version": contract.PROTOCOL_VERSION},
        })

    if method == "tools/list":
        return _ok(call_id, {"tools": tools_for(principal)})

    if method == "tools/call":
        params = message.get("params") or {}
        name = str(params.get("name") or "")
        operation = _operation_of(name)
        if not operation:
            return _error(call_id, METHOD_NOT_FOUND, "There is no tool called %r." % name)
        try:
            answer = dispatch.dispatch(operation, params.get("arguments") or {}, principal,
                                       service=service)
        except dispatch.Refused as refusal:
            # A refusal is a RESULT, not a transport error. An AI that is told "the call failed"
            # will retry it; one that is told "you are over a ceiling, it clears in four minutes"
            # can say so to the person waiting.
            return _ok(call_id, {
                "isError": True,
                "content": [{"type": "text", "text": _in_words(refusal)}],
                "structuredContent": refusal.as_answer(),
            })
        return _ok(call_id, {
            "content": [{"type": "text", "text": _readably(operation, answer)}],
            "structuredContent": answer,
        })

    return _error(call_id, METHOD_NOT_FOUND, "This server does not do %r." % method)


def _in_words(refusal) -> str:
    said = refusal.because
    if refusal.what_to_do:
        said += "\n\nWhat would help: " + refusal.what_to_do
    return said


def _readably(operation: str, answer: dict) -> str:
    """Something an AI can put in front of a person without them reading JSON."""
    if operation == "prepare":
        return (
            "Before this runs:\n"
            "  where       %s\n"
            "  sends       %s bytes, and nothing else leaves this machine\n"
            "  network     %s\n"
            "  limits      %s seconds of wall clock\n"
            "  so far      %s runs, %s seconds used\n"
            "  note        %s"
            % (answer.get("runs_at", "?"),
               answer.get("transfers", {}).get("bytes", "?"),
               answer.get("network", {}).get("asked_for", "?"),
               answer.get("limits", {}).get("wall_clock_s", "?"),
               answer.get("expected_use", {}).get("runs_so_far", "?"),
               answer.get("expected_use", {}).get("seconds_so_far", "?"),
               answer.get("what_this_does_not_establish", ""))
        )
    if operation == "submit":
        return "Started run %s. It is %s." % (answer.get("run_id"), answer.get("state"))
    if operation == "result":
        return "Run %s %s.\n\n%s" % (answer.get("run_id"), answer.get("state"),
                                     answer.get("stdout") or "(it printed nothing)")
    import json as _json

    return _json.dumps(answer, indent=2, sort_keys=True)


def _ok(call_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": call_id, "result": result}


def _error(call_id, code, message) -> dict:
    return {"jsonrpc": "2.0", "id": call_id, "error": {"code": code, "message": message}}
