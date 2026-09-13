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

#: Where the contract lives. Kept beside the renderer so the document and the router agree.
NAMESPACE = "/v1/op/"

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
        paths[NAMESPACE + op.name.replace(".", "/")] = {method: entry}

    document = {
        "openapi": "3.1.0",
        "info": {
            "title": title,
            "version": contract.PROTOCOL_VERSION,
            "description": (
                "Every operation this sandbox has. The same contract is served over REST, "
                "over MCP, and to the CLI and SDKs; nothing is available through one and not "
                "another, and no client decides anything the server decides."
                + chr(10) + chr(10) + "What this does not establish:" + chr(10)
                + chr(10).join("  - " + line for line in contract.WHAT_THIS_IS_NOT)
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
    """The MCP rendering. One tool per operation a model may be offered.

    Operations not declared `audience=tool` are left out HERE, in the renderer, not only in the
    per-device list. A schema is an offer: a tool that appears in it has been offered, whoever is
    afterwards told they may not call it. Rotating a credential, withdrawing a device, making an
    invitation, changing the operator's policy or working the kill switch are a person's
    business, and a model handed a device token has every incentive to do them and no way to be
    asked whether it should.

    They remain reachable through the dispatcher, which is what stops this being a second place
    decisions are made -- they are simply not put in front of a model.
    """
    return [
        {
            "name": tool_name_for(op.name),
            "description": op.summary,
            "inputSchema": _object(op.params),
            "_meta": {"agentnode/since": op.since, "agentnode/needs": op.needs,
                      "agentnode/changes": op.changes},
        }
        for op in contract.for_a_model()
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
                "name": tool_name_for(op.name),
                "description": op.summary,
                "parameters": _object(op.params),
            },
        }
        # The same exclusion as the MCP rendering, for the same reason: this is the other thing
        # a model is handed, and a schema that lists a tool has offered it.
        for op in contract.for_a_model()
    ]


def every_rendering() -> dict:
    """All of them, for a test that wants to ask the same question of each."""
    return {
        "openapi": openapi_document(),
        "mcp": mcp_tools(),
        "tool_calling": tool_calling_schema(),
    }


def tool_name_for(operation_name: str) -> str:
    """How an operation is spelled where dots are not allowed. One place, so the renderings and
    anything reading them cannot disagree about it."""
    return "agentnode_" + operation_name.replace(".", "_")


def operations_named_by(rendering, which: str) -> set:
    """Which declared operations a rendering actually offers.

    Matched by mangling the DECLARED names and looking for them, rather than by un-mangling what
    the rendering contains. Reversing the mangling needs a rule about where a dot used to be, and
    the first version of that quietly got `devices.list` wrong in one of the three renderings --
    which is exactly the kind of disagreement this module exists to make impossible.
    """
    if which == "openapi":
        offered = {entry[method]["operationId"]
                   for entry in rendering["paths"].values()
                   for method in entry}
        return {op.name for op in contract.OPERATIONS if op.name in offered}
    if which == "mcp":
        offered = {tool["name"] for tool in rendering}
    elif which == "tool_calling":
        offered = {tool["function"]["name"] for tool in rendering}
    else:
        raise ValueError(which)
    return {op.name for op in contract.OPERATIONS if tool_name_for(op.name) in offered}
