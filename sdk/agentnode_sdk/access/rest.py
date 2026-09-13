"""The REST door. A translation from HTTP to `dispatch` and back, and nothing else.

Every route here does the same four things: read the token from the header, turn it into a
principal, call `dispatch`, and render what comes back. There is no route that decides anything,
no route that reaches past the dispatcher into the gateway, and no route for an operation that is
not declared -- the routes are BUILT from the contract, so there is nowhere for an extra one to
come from.

That last part is the point of generating rather than writing them. A hand-written route table is
a second list of what the service does, and a second list is a list that can disagree.

## Where the token goes

In a header, never in the path or the query. A URL ends up in logs, in browser history, in a
proxy's access log and in whatever somebody pastes into an issue; a header does not. The same
reason the audit log carries no token.
"""
from __future__ import annotations

import json

from agentnode_sdk.access import contract, dispatch, schemas

#: Where a caller puts its device token.
TOKEN_HEADER = "X-AgentNode-Token"

#: Where a caller says which protocol it speaks, so the server can refuse an operation that
#: arrived after that client did rather than letting it fail somewhere less obvious.
SPEAKS_HEADER = "X-AgentNode-Protocol"

#: The contract lives under its own prefix. That is not decoration: the gateway also serves older
#: routes directly under /v1/, and sharing the namespace would mean an unknown operation and an
#: unknown legacy path are the same string. The contract could then not answer "no such
#: operation" for anything, because it could never be sure the path was meant for it.
NAMESPACE = "/v1/op/"

#: Paths that are part of the contract. Built from it, so this cannot drift.
PATHS = {NAMESPACE + op.name.replace(".", "/"): op for op in contract.OPERATIONS}

#: Read-only and unauthenticated, deliberately: a client cannot write the request that gets it a
#: token without first knowing the shape of one.
SCHEMA_PATH = "/v1/openapi.json"

#: The MCP door. Same authentication, same dispatcher, different vocabulary.
MCP_PATH = "/v1/mcp"


def ours(path: str) -> bool:
    """Whether this path is the contract's to answer for, declared or not."""
    bare = path.split("?", 1)[0]
    return bare.startswith(NAMESPACE) or bare in (SCHEMA_PATH, MCP_PATH)


def route_for(path: str):
    """Which operation a path is, or None. One table, derived from the declarations."""
    bare = path.split("?", 1)[0]
    return PATHS.get(bare.rstrip("/") or bare)


def how_it_should_answer(refusal: str) -> int:
    """Which HTTP status a refusal becomes.

    The mapping lives here because it is an HTTP fact, not a product one -- the dispatcher does
    not know what a 403 is, and should not. What it must not do is lose information: the body
    always carries the refusal by name, and a client that wants to branch reads that rather than
    the status, because several refusals share a status.
    """
    return {
        "not_authenticated": 401,
        "not_permitted": 403,
        "unknown_operation": 404,
        "no_such_run": 404,
        "malformed": 400,
        "not_finished": 409,
        "over_a_ceiling": 429,
        "refused_by_policy": 403,
        "gateway_stopped": 503,
        "sandbox_unavailable": 503,
    }.get(refusal, 400)


def handle(service, path: str, method: str, headers, body: bytes):
    """One request. Returns (status, body-as-dict).

    This is the whole adapter. It is deliberately a function rather than a framework: what it
    does is small enough to read in one sitting, and anything larger would be somewhere for a
    decision to hide.
    """
    bare = path.split("?", 1)[0]
    if bare == SCHEMA_PATH:
        return 200, schemas.openapi_document()

    if bare == MCP_PATH:
        from agentnode_sdk.access import mcp

        try:
            message = json.loads(body.decode("utf-8")) if body else {}
        except (ValueError, UnicodeDecodeError):
            dispatch.record_a_refusal(
                service, MCP_PATH, dispatch.identify(service, _header(headers, TOKEN_HEADER)),
                "bad_request")
            return 400, {"jsonrpc": "2.0", "id": None,
                         "error": {"code": -32700, "message": "that is not JSON"}}
        reply = mcp.handle(service, message,
                           dispatch.identify(service, _header(headers, TOKEN_HEADER)))
        # A notification gets no reply. 202 rather than 200 with an empty body, because "accepted,
        # nothing to say" and "here is nothing" are different things.
        return (202, {}) if reply is None else (200, reply)

    # Who is asking is established first, so a refusal at the door is recorded against somebody
    # rather than against nobody. A probe that never reaches an operation is exactly the traffic
    # an operator most wants to be able to see afterwards.
    token = _header(headers, TOKEN_HEADER)
    who = dispatch.identify(service, token)

    op = route_for(path)
    if op is None:
        dispatch.record_a_refusal(service, path, who, "not_a_route")
        return 404, dispatch.Refused(
            "unknown_operation",
            "This sandbox has nothing at that address.",
            "Fetch %s to see what it does have." % SCHEMA_PATH).as_answer()

    wanted = "POST" if (op.changes or op.params) else "GET"
    if method.upper() != wanted:
        dispatch.record_a_refusal(service, op.name, who, "wrong_method")
        return 405, dispatch.Refused(
            "malformed",
            "%s is asked for with %s, not %s." % (op.name, wanted, method.upper()),
            "Send it as %s." % wanted).as_answer()

    params = {}
    if body:
        try:
            params = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            dispatch.record_a_refusal(service, op.name, who, "bad_request")
            return 400, dispatch.Refused(
                "malformed", "The body of that request is not JSON.",
                "Send a JSON object with the operation's parameters.").as_answer()
        if not isinstance(params, dict):
            dispatch.record_a_refusal(service, op.name, who, "bad_request")
            return 400, dispatch.Refused(
                "malformed", "The body of that request is not a JSON object.",
                "Send an object, not a list or a bare value.").as_answer()

    speaks = _header(headers, SPEAKS_HEADER) or contract.PROTOCOL_VERSION
    try:
        answer = dispatch.dispatch(op.name, params, who, service=service, speaks=speaks)
    except dispatch.Refused as refusal:
        return how_it_should_answer(refusal.refusal), refusal.as_answer()
    return 200, answer


def _header(headers, name: str) -> str:
    """Case-insensitively, because HTTP header names are."""
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    if getter is not None:
        found = getter(name) or getter(name.lower()) or ""
        if found:
            return str(found)
    for key, value in dict(headers).items():
        if str(key).lower() == name.lower():
            return str(value)
    return ""
