"""The contract, rendered for each kind of client. Generated, never written twice.

An OpenAPI document, a set of MCP tool definitions and a generic function-calling schema all say
the same thing in three vocabularies. Maintaining them by hand would mean three chances to
disagree, and the disagreement would not show up as an error -- it would show up as a client that
can ask for something one door allows and another refuses, which is the shape of a security
difference rather than a documentation one.

So they are produced from `contract.py` and nothing else. A transport cannot offer an operation
that is not declared, because no generator has anywhere to get one from; and a transport cannot
accept a parameter that is not declared, because the schema it publishes does not contain one.

`MANAGED-ACCESS-DECISION-0001` named that as the reason to choose this shape, and named the risk
that goes with it: a declaration layer is itself security-critical, so completeness has to be
mechanically checked rather than assumed. The tests that do that are the point of this module
existing separately from the transports.
"""
from __future__ import annotations

from agentnode_sdk.access import contract

#: JSON Schema words for the kinds a declaration uses. `bytes` has no JSON type: it travels
#: base64-encoded, and saying so here is better than every transport deciding for itself.
AS_JSON = {
    "string": {"type": "string"},
    "integer": {"type": "integer"},
    "boolean": {"type": "boolean"},
    "object": {"type": "object"},
    "array": {"type": "array"},
    "bytes": {"type": "string", "contentEncoding": "base64"},
}


def _properties(fields) -> dict:
    out = {}
    for f in fields:
        shape = dict(AS_JSON[f.kind])
        shape["description"] = f.describes
        if f.one_of:
            shape["enum"] = list(f.one_of)
        out[f.name] = shape
    return out


def _object(fields) -> dict:
    """A closed object. `additionalProperties: false` is the load-bearing part: a parameter
    nobody declared is refused rather than ignored, and ignored input is input somebody believes
    was used."""
    return {
        "type": "object",
        "properties": _properties(fields),
        "required": [f.name for f in fields if f.required],
        "additionalProperties": False,
    }


def openapi_document(title: str = "AgentNode Sandbox", server: str = "") -> dict:
    """The REST rendering. One path per operation, POST for anything that changes.

    Operations are addressed by name rather than by a REST-shaped noun hierarchy. That is a
    deliberate trade: a hierarchy reads better to somebody browsing, and it also means the REST
    surface stops being a direct rendering of the contract, which is the property being kept.
    """
    paths = {}
    for op in contract.OPERATIONS:
        method = "post" if op.changes or op.params else "get"
        entry = {
            "operationId": op.name,
            "summary": op.summary,
            "x-agentnode-since": op.since,
            "x-agentnode-needs": op.needs,
            "responses": {
                "200": {
                    "description": "the operation was carried out",
                    "content": {"application/json": {"schema": _object(op.returns)}},
                },
                "400": {"description": "refused; the body names which refusal"},
            },
        }
        if op.params:
            entry["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": _object(op.params)}},
            }
        paths["/v1/" + op.name.replace(".", "/")] = {method: entry}

    document = {
        "openapi": "3.1.0",
        "info": {
            "title": title,
            "version": contract.PROTOCOL_VERSION,
            "description": (
                "Every operation this sandbox has. The same contract is served over REST, over "
                "MCP, and to the CLI and SDKs; nothing is available through one and not another, "
                "and no client decides anything the server decides."
            ),
        },
        "paths": paths,
        "components": {
            "schemas": {
                "Refusal": {
                    "type": "object",
                    "properties": {
                        "refused": {"type": "string", "enum": list(contract.REFUSALS),
                                    "description": "which refusal this is, by name"},
                        "because": {"type": "string",
                                    "description": "what happened, in words a person can act on"},
                        "what_to_do": {"type": "string",
                                       "description": "one thing that would resolve it"},
                    },
                    "required": ["refused", "because"],
                    "additionalProperties": False,
                }
            }
        },
    }
    if server:
        document["servers"] = [{"url": server}]
    return document


def mcp_tools() -> list:
    """The MCP rendering. One tool per operation, named so they group under one service."""
    return [
        {
            "name": "agentnode_" + op.name.replace(".", "_"),
            "description": op.summary,
            "inputSchema": _object(op.params),
            "_meta": {"agentnode/since": op.since, "agentnode/needs": op.needs,
                      "agentnode/changes": op.changes},
        }
        for op in contract.OPERATIONS
    ]


def tool_calling_schema() -> list:
    """The generic rendering, for anything that takes a list of callable functions.

    Same objects, different envelope. Systems that speak neither MCP nor our REST directly can
    still be handed this and call the API.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "agentnode_" + op.name.replace(".", "_"),
                "description": op.summary,
                "parameters": _object(op.params),
            },
        }
        for op in contract.OPERATIONS
    ]


def every_rendering() -> dict:
    """All of them, for a test that wants to ask the same question of each."""
    return {
        "openapi": openapi_document(),
        "mcp": mcp_tools(),
        "tool_calling": tool_calling_schema(),
    }


def operations_named_by(rendering, which: str) -> set:
    """Which operations a rendering actually offers, in that rendering's own terms."""
    if which == "openapi":
        return {entry[method]["operationId"]
                for entry in rendering["paths"].values()
                for method in entry}
    if which == "mcp":
        return {tool["name"].replace("agentnode_", "", 1).replace("_", ".", 1)
                if tool["name"].count("_") > 1 else tool["name"].replace("agentnode_", "", 1)
                for tool in rendering}
    if which == "tool_calling":
        return {tool["function"]["name"].replace("agentnode_", "", 1) for tool in rendering}
    raise ValueError(which)
