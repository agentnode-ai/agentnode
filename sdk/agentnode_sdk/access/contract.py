"""What the AgentNode Sandbox service can be asked to do, declared once.

`MANAGED-ACCESS-DECISION-0001` chose this shape: the operations are DATA, there is one
server-side way to reach them, and every transport is a rendering of this declaration rather than
a second opinion about it. REST, remote MCP, the stdio bridge, the CLI, the SDKs and the web
client all end in the same place; what differs between them is spelling.

The reason is not tidiness. When there are six ways in and each carries its own idea of the order
in which things are checked, the order is no longer a property of the service -- it is a property
of whichever door somebody knocked on. A declaration that transports are GENERATED from cannot
grow a seventh door by accident, and a transport cannot accept a parameter nothing declared.

## What a declaration carries, and why each part is here

    name            what the operation is called, in every transport
    since           the protocol version that introduced it, so a client can negotiate per
                    operation rather than being told one number for the whole service
    needs           the capability a caller must hold. Not a role: capabilities are what the
                    server grants a device, and roles are a way of forgetting which ones
    params          every accepted parameter. Anything not named here is refused rather than
                    ignored, because ignored input is input somebody believes was used
    returns         what comes back, so a client can be written against this and not against a
                    recorded response
    errors          the refusals this operation can produce, by name. A client that must parse
                    prose to tell "over quota" from "malformed" will get it wrong
    changes         whether it changes anything, which decides the HTTP method, whether it may
                    be retried, and whether it needs a run id to be idempotent

## What this file is NOT

It is not the enforcement. Nothing here checks a token, a quota or a policy; `dispatch.py` does
that, once, and this only says what may be asked. Keeping them apart is deliberate: a declaration
that could also decide would be a second place where security lives.

It is also not a wire format. How a parameter is encoded is each transport's business.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: The protocol version this build speaks. Clients are told it, and are also told the `since` of
#: every operation, so a client older than an operation can tell rather than discover by failing.
PROTOCOL_VERSION = "1"

# ------------------------------------------------------------------ what a caller may hold

#: Capabilities a device can be granted. What a caller may do is the intersection of what it holds
#: and what the operator's policy allows -- never one of those alone.
RUN = "run"                       # submit work and ask about it
READ = "read"                     # ask about work and usage, change nothing
MANAGE_DEVICES = "manage_devices"  # list and revoke this account's devices

CAPABILITIES = (RUN, READ, MANAGE_DEVICES)

# ------------------------------------------------------------------ refusals, by name

REFUSALS = (
    "not_authenticated",       # no usable credential was presented
    "device_revoked",          # the device was withdrawn
    "not_permitted",           # authenticated, but lacks the capability
    "unknown_operation",       # nothing declared under that name
    "malformed",               # a parameter is missing, unknown, or the wrong shape
    "over_a_ceiling",          # a quota or rate limit refused it, and says when it lifts
    "refused_by_policy",       # the operator's policy refused this job
    "gateway_stopped",         # the kill switch is on; the operator's own words come with it
    "no_such_run",             # asked about something this caller did not submit
    "not_finished",            # a result asked for before there is one
    "sandbox_unavailable",     # nothing could run it, and this is not the caller's fault
)


@dataclass(frozen=True)
class Field:
    """One parameter or one returned value."""

    name: str
    kind: str                      # "string" | "integer" | "boolean" | "object" | "array" | "bytes"
    describes: str
    required: bool = True
    #: Present only for enumerated values, so a transport can offer a choice rather than free text.
    one_of: tuple = ()


@dataclass(frozen=True)
class Operation:
    name: str
    since: str
    needs: str
    summary: str
    params: tuple = ()
    returns: tuple = ()
    errors: tuple = ()
    changes: bool = False

    def __post_init__(self) -> None:
        if self.needs not in CAPABILITIES:
            raise ValueError("%s needs %r, which is not a capability" % (self.name, self.needs))
        for refusal in self.errors:
            if refusal not in REFUSALS:
                raise ValueError("%s can refuse with %r, which is not a declared refusal"
                                 % (self.name, refusal))
        seen = [f.name for f in self.params]
        if len(seen) != len(set(seen)):
            raise ValueError("%s declares a parameter twice" % self.name)

    def accepts(self, name: str) -> bool:
        return any(f.name == name for f in self.params)


#: Every refusal, for operations that can hit the common ones. Spelled out per operation rather
#: than inherited, so reading one declaration tells a client what it must handle.
COMMON = ("not_authenticated", "device_revoked", "not_permitted", "malformed", "gateway_stopped")


#: What every reader-facing surface must carry. Said in one place so the three surfaces cannot
#: drift, and said at all because a service that describes where code runs without describing
#: what that does not protect against is one somebody will read as a guarantee.
WHAT_THIS_IS_NOT = (
    "Older routes on this gateway are not part of this contract and are not covered by it.",
    "Cancelling a run is synchronous and can hold the caller for up to the gateway's settle "
    "window while it confirms the sandbox is gone.",
    "None of this is authorisation to expose this sandbox publicly, to deploy it, or to charge "
    "for it. It is a closed test service.",
)


OPERATIONS = (
    Operation(
        name="capabilities",
        since="1",
        needs=READ,
        summary="What this sandbox can do, what it will enforce, and which operations it has.",
        params=(),
        returns=(
            Field("protocol", "string", "the protocol version this gateway speaks"),
            Field("operations", "array", "each operation, with the version that introduced it"),
            Field("capabilities", "array", "what this device has been granted"),
            Field("enforces", "object", "what the sandbox was measured to actually enforce"),
            Field("what_this_does_not_establish", "array",
                  "the limits of this arrangement, which every client is told before it starts"),
        ),
        errors=COMMON,
    ),
    Operation(
        name="prepare",
        since="1",
        needs=RUN,
        summary="What would happen if this job were run: where, what leaves the machine, what it "
                "may reach, what it may use, and what it is expected to cost.",
        params=(
            Field("command", "array", "the command, as a list of arguments"),
            Field("artifact_sha256", "string", "digest of the code that would be sent"),
            Field("artifact_bytes", "integer", "how much would be transferred"),
            Field("network", "string", "the access being asked for", required=False,
                  one_of=("none", "allowlist")),
            Field("allowed_domains", "array", "where it may connect, if any", required=False),
            Field("wall_clock_s", "integer", "how long it may run", required=False),
        ),
        returns=(
            Field("runs_at", "string", "the machine and topology it would run on"),
            Field("transfers", "object", "what would leave this machine, and how much"),
            Field("network", "object", "what it would be allowed to reach, and what it would not"),
            Field("limits", "object", "cpu, memory, wall clock and output ceilings in force"),
            Field("expected_use", "object", "what this would count against, before it is run"),
            Field("what_this_does_not_establish", "string",
                  "the arrangement's stated limits, carried with the disclosure"),
            Field("accepted_disclosure", "string",
                  "what to send with the submission to show this is what was agreed to"),
        ),
        errors=COMMON + ("refused_by_policy", "over_a_ceiling"),
    ),
    Operation(
        name="submit",
        since="1",
        needs=RUN,
        summary="Run this code in the sandbox.",
        params=(
            Field("run_id", "string", "chosen by the caller, so a retry is not a second run"),
            Field("artifact", "bytes", "the code to run"),
            Field("command", "array", "the command, as a list of arguments"),
            Field("network", "string", "the access asked for", required=False,
                  one_of=("none", "allowlist")),
            Field("allowed_domains", "array", "where it may connect, if any", required=False),
            Field("wall_clock_s", "integer", "how long it may run", required=False),
            Field("accepted_disclosure", "string",
                  "the disclosure this was started against, as prepare() returned it"),
        ),
        returns=(
            Field("run_id", "string", "how to ask about it"),
            Field("state", "string", "where it is now"),
            Field("admitted_under", "object", "the ceilings and policy it was admitted against"),
        ),
        errors=COMMON + ("refused_by_policy", "over_a_ceiling", "sandbox_unavailable"),
        changes=True,
    ),
    Operation(
        name="status",
        since="1",
        needs=READ,
        summary="Where a run has got to.",
        params=(Field("run_id", "string", "the run to ask about"),),
        returns=(
            Field("run_id", "string", "the run asked about"),
            Field("state", "string", "where it is"),
            Field("started_at", "integer", "when it began", required=False),
            Field("finished_at", "integer", "when it ended", required=False),
        ),
        errors=COMMON + ("no_such_run",),
    ),
    Operation(
        name="result",
        since="1",
        needs=READ,
        summary="What a finished run produced.",
        params=(Field("run_id", "string", "the run to collect"),),
        returns=(
            Field("run_id", "string", "the run collected"),
            Field("state", "string", "how it ended"),
            Field("exit_code", "integer", "what the command returned", required=False),
            Field("stdout", "string", "what it wrote out", required=False),
            Field("stderr", "string", "what it wrote to standard error", required=False),
            Field("cleanup_verified", "boolean",
                  "whether the sandbox was confirmed gone; absent means nobody could ask",
                  required=False),
        ),
        errors=COMMON + ("no_such_run", "not_finished"),
    ),
    Operation(
        name="cancel",
        since="1",
        needs=RUN,
        summary="Stop a run that has not finished, and say whether what it left is gone.",
        params=(Field("run_id", "string", "the run to stop"),),
        returns=(
            Field("run_id", "string", "the run stopped"),
            Field("state", "string", "what it ended as"),
            Field("cleanup_verified", "boolean",
                  "whether the sandbox was confirmed gone; absent means nobody could ask",
                  required=False),
        ),
        errors=COMMON + ("no_such_run",),
        changes=True,
    ),
    Operation(
        name="usage",
        since="1",
        needs=READ,
        summary="What has been used against the ceilings, and when the window clears.",
        params=(Field("window", "string", "which window to report", required=False,
                      one_of=("current", "all")),),
        returns=(
            Field("runs", "integer", "how many have been started in the window"),
            Field("seconds", "integer", "how much wall clock has been used in it"),
            Field("ceilings", "object", "what the operator has set"),
            Field("clears_at", "integer", "when the oldest run stops counting", required=False),
        ),
        errors=COMMON,
    ),
    Operation(
        name="devices.list",
        since="1",
        needs=MANAGE_DEVICES,
        summary="Which devices can reach this sandbox as you, and when each was last used.",
        params=(),
        returns=(Field("devices", "array", "each device, with its name and when it was last used"),),
        errors=COMMON,
    ),
    Operation(
        name="devices.revoke",
        since="1",
        needs=MANAGE_DEVICES,
        summary="Withdraw a device, so nothing it holds works any more.",
        params=(Field("device_id", "string", "the device to withdraw"),),
        returns=(
            Field("device_id", "string", "the device withdrawn"),
            Field("withdrawn", "boolean", "whether there was one to withdraw"),
        ),
        errors=COMMON,
        changes=True,
    ),
)


BY_NAME = {op.name: op for op in OPERATIONS}


def find(name: str):
    """The declaration, or None. `dispatch` turns None into `unknown_operation`; there is no
    fall-through to "try it anyway"."""
    return BY_NAME.get(name)


def for_capabilities(held) -> tuple:
    """What a caller holding these capabilities may ask for. Capability discovery is this."""
    held = set(held or ())
    return tuple(op for op in OPERATIONS if op.needs in held)


def describe() -> dict:
    """The contract as data, for `capabilities` to return and for the generators to render."""
    return {
        "protocol": PROTOCOL_VERSION,
        "operations": [
            {
                "name": op.name,
                "since": op.since,
                "needs": op.needs,
                "summary": op.summary,
                "changes": op.changes,
                "params": [
                    {"name": f.name, "kind": f.kind, "required": f.required,
                     "describes": f.describes,
                     **({"one_of": list(f.one_of)} if f.one_of else {})}
                    for f in op.params
                ],
                "returns": [
                    {"name": f.name, "kind": f.kind, "required": f.required,
                     "describes": f.describes}
                    for f in op.returns
                ],
                "errors": list(op.errors),
            }
            for op in OPERATIONS
        ],
        "refusals": list(REFUSALS),
        "capabilities": list(CAPABILITIES),
    }
