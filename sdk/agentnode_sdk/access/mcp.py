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
    """The tools this device may actually call -- not everything the service has.

    Two filters, and they answer different questions. What this device was GRANTED decides what
    it may do at all. What is a tool at all decides what a model is offered: some operations are
    a person's business rather than a job's -- replacing a credential, managing sessions,
    enrolling a connection -- and an AI handed a device token should not be able to mint its own
    successor as one ordinary tool call. Those still go through the dispatcher, which is what
    stops them being a second place decisions are made; they are simply not offered here.
    """
    for_a_model = {op.name for op in contract.for_a_model()}
    allowed = {op.name for op in contract.for_capabilities(principal.capabilities)
               if op.name in for_a_model}
    return [tool for tool in schemas.mcp_tools()
            if _operation_of(tool["name"]) in allowed]


def _operation_of(tool_name: str) -> str:
    """The operation a tool name refers to -- and only ever one a model may call.

    The audience is checked HERE, in the one place a name becomes an operation, rather than only
    where the list is built. It was only in the list: `tools_for` correctly left out replacing a
    credential, ending a session and withdrawing a device, and `tools/call` then resolved any
    declared operation by name and carried it out. The names are predictable, so a model holding
    a device token could withdraw devices and end sessions by asking for a tool it had never been
    offered. Filtering what is advertised is not a boundary; this is.

    A person-only operation therefore does not exist from a model's side: the same "there is no
    tool called that" as a name that was never declared, because from where the model stands
    those are the same fact.
    """
    for op in contract.OPERATIONS:
        if schemas.tool_name_for(op.name) == tool_name:
            return op.name if op.audience == contract.TOOL else ""
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
            # An AI reads this before it reads anything else, and whatever it reads here is what
            # it will tell the person about what this sandbox is.
            "instructions": (
                "Runs code in a sandbox on somebody else's machine, under their policy."
                + chr(10) + chr(10) + "What this does not establish:" + chr(10)
                + chr(10).join("  - " + line for line in contract.WHAT_THIS_IS_NOT)),
        })

    if method == "tools/list":
        return _ok(call_id, {"tools": tools_for(principal)})

    if method == "tools/call":
        params = message.get("params") or {}
        name = str(params.get("name") or "")
        operation = _operation_of(name)
        if not operation:
            # Recorded like any other refusal. An unknown tool name is exactly what probing
            # looks like, and it never reaches the dispatcher, so the door records it.
            dispatch.record_a_refusal(service, name, principal, "unknown_operation")
            return _error(call_id, METHOD_NOT_FOUND, "There is no tool called that.")
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

    dispatch.record_a_refusal(service, method, principal, "not_a_route")
    return _error(call_id, METHOD_NOT_FOUND, "This server does not do that.")


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
