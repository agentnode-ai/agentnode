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

from dataclasses import dataclass

#: The protocol version this build speaks. Clients are told it, and are also told the `since` of
#: every operation, so a client older than an operation can tell rather than discover by failing.
PROTOCOL_VERSION = "2"

#: What changed in 2, so a reader is not left to diff two builds:
#:
#: * `cancel` answers at once and the run reports `stopping` until the sandbox is confirmed
#:   gone. Its answer gained `accepted`, `attempts` and `problem`.
#: * `device_revoked` was REMOVED from the refusals. `MANAGED-REVOCATION-0001` settled it:
#:   withdrawing a device deletes the only record that could tell it from a credential that
#:   never existed, so the contract promised a distinction the gateway does not make. A
#:   withdrawn device, an ended session and an unknown credential are all `not_authenticated`.
#: * `devices.rotate` and the session operations were declared, and the older `/v1/jobs*` and
#:   `/v1/token/rotate` addresses were withdrawn in favour of them.
INTRODUCED_IN_2 = ("cancel.accepted", "cancel.attempts", "cancel.problem", "devices.rotate",
                   "sessions.list", "sessions.end", "connections.enrol", "connections.check")

# ------------------------------------------------------------------ what a caller may hold

#: Capabilities a device can be granted. What a caller may do is the intersection of what it holds
#: and what the operator's policy allows -- never one of those alone.
RUN = "run"                       # submit work and ask about it
READ = "read"                     # ask about work and usage, change nothing
MANAGE_DEVICES = "manage_devices"  # list and revoke this account's devices

CAPABILITIES = (RUN, READ, MANAGE_DEVICES)

# ------------------------------------------------------------------ what an operation IS

#: Who an operation is FOR. This is a declaration, never something worked out from the name.
#:
#: An earlier version excluded operations from the tool schemas by matching fragments of their
#: names -- "rotate", "revoke", "invite". That is fail-open, and obviously so once written down:
#: an operation called `account.limits` or `credentials.refresh` matches nothing on the list and
#: ships to every model on the next release. The list was a description of the operations that
#: happened to exist when it was written, doing duty as a rule.
TOOL = "tool"            # may be handed to a model as a callable tool
PERSON = "person"        # a person's own client. Never rendered as a tool.
OPERATOR = "operator"    # whoever runs the sandbox. Never rendered as a tool.
AUDIENCES = (TOOL, PERSON, OPERATOR)

#: What an operation can do, in the only terms that matter for deciding who may be offered it.
READS = "reads"                      # changes nothing
AFFECTS_A_RUN = "affects_a_run"      # starts work, or stops it
CHANGES_ACCESS = "changes_access"    # credentials, devices, sessions, invitations
CHANGES_POLICY = "changes_policy"    # the operator's policy, ceilings, kill switch, billing
RISK_CLASSES = (READS, AFFECTS_A_RUN, CHANGES_ACCESS, CHANGES_POLICY)

#: Risk classes a model is never offered, whatever anybody declares. The rule that makes
#: `audience` unable to be wrong in the dangerous direction.
NEVER_FOR_A_MODEL = (CHANGES_ACCESS, CHANGES_POLICY)

#: Not a value. The ABSENCE of one.
#:
#: An earlier version defaulted a missing `audience` to `person`, which kept an unclassified
#: operation out of the tool schemas and was described as making classification mandatory. It
#: did not. It made classification OPTIONAL with a safe fallback, which is a different and
#: weaker claim, and it left the operation reachable over REST -- classified by nobody, refused
#: by nothing. A default that stands in for a decision is a decision nobody made.
#:
#: There is no fallback now. Every operation states who it is for, what it can do, which
#: permission it needs and whether a person has to have agreed. An operation that does not is
#: not a quieter operation; it is a contract that does not describe itself, and nothing is
#: generated, started or served from it.
UNCLASSIFIED = None

# ------------------------------------------------------------------ refusals, by name

REFUSALS = (
    # One refusal covers an unknown credential, an expired one, a withdrawn device and an
    # ended session. `MANAGED-REVOCATION-0001` chose this deliberately: revoking deletes the
    # token record, so nothing remains to tell those apart, and keeping a tombstone to create
    # the distinction would retain a record of credentials that no longer exist and would tell
    # a caller holding a revoked token that it was once real. The cost is accepted and stated:
    # a client cannot tell "withdrawn" from "wrong", and both lead to the same action.
    "not_authenticated",       # no usable credential was presented, for any reason
    "not_permitted",           # authenticated, but lacks the capability
    "unknown_operation",       # nothing declared under that name
    "malformed",               # a parameter is missing, unknown, or the wrong shape
    "over_a_ceiling",          # a quota or rate limit refused it, and says when it lifts
    "refused_by_policy",       # the operator's policy refused this job
    "gateway_stopped",         # the kill switch is on; the operator's own words come with it
    "no_such_run",             # asked about something this caller did not submit
    "not_finished",            # a result asked for before there is one
    "disclosure_required",     # nothing was disclosed to a person, so nothing runs
    "upgrade_required",        # this client cannot express what this operation now requires
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
    #: The protocol version that introduced this field, when it is not the operation's own.
    #: Descriptive rather than enforced -- an older client simply does not read it -- but a
    #: reader of the schema can see what is new without comparing two builds.
    since: str = ""


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
    #: Who this is for. NO DEFAULT -- see `UNCLASSIFIED`. `audience=PERSON` is a perfectly
    #: ordinary thing to declare; what is refused is declaring nothing.
    audience: str | None = UNCLASSIFIED
    #: What it can do. Checked against `audience` below, so a declaration cannot be both
    #: "a model may call this" and "this changes who can get in".
    risk: str | None = UNCLASSIFIED
    #: Whether carrying it out requires proof that a person agreed to this specific thing.
    #: `submit` has it, and the disclosure gate is what enforces it. False is a real answer;
    #: not saying is not.
    confirms_with_a_person: bool | None = UNCLASSIFIED

    def __post_init__(self) -> None:
        # Refused at the declaration, so a classification that is missing, unknown or
        # self-contradictory cannot be constructed at all. The generators check again, because
        # something built another way -- a loader, a plugin, a test -- must not slip past.
        for what, value in (("audience", self.audience), ("risk", self.risk),
                            ("required permission", self.needs),
                            ("human confirmation", self.confirms_with_a_person)):
            if value is UNCLASSIFIED:
                raise ValueError(
                    "%s does not declare its %s. Every operation states who it is for, what it "
                    "can do, which permission it needs and whether a person has to have agreed. "
                    "There is no default: a default standing in for that decision is a decision "
                    "nobody made." % (self.name, what))
        if self.audience not in AUDIENCES:
            raise ValueError("%s declares audience %r, which is not one of %s"
                             % (self.name, self.audience, ", ".join(AUDIENCES)))
        if self.risk not in RISK_CLASSES:
            raise ValueError("%s declares risk %r, which is not one of %s"
                             % (self.name, self.risk, ", ".join(RISK_CLASSES)))
        if self.audience == TOOL and self.risk in NEVER_FOR_A_MODEL:
            raise ValueError(
                "%s is declared as a tool and as %s. A model calling it cannot be asked whether "
                "it should, and it has every reason to say yes." % (self.name, self.risk))
        if self.risk in NEVER_FOR_A_MODEL and self.needs != MANAGE_DEVICES:
            raise ValueError(
                "%s is %s but asks only for %s. Something that changes who can get in, or what "
                "they may do, needs the capability a person's own client holds."
                % (self.name, self.risk, self.needs))
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
COMMON = ("not_authenticated", "not_permitted", "malformed", "gateway_stopped")


#: What every reader-facing surface must carry. Said in one place so the three surfaces cannot
#: drift, and said at all because a service that describes where code runs without describing
#: what that does not protect against is one somebody will read as a guarantee.
WHAT_THIS_IS_NOT = (
    "A withdrawn device, an ended session and a credential this sandbox never issued are all "
    "reported the same way. This gateway keeps no record of a credential once it is withdrawn, "
    "so it cannot tell them apart and does not pretend to.",
    "Cancelling asks for a stop and comes back at once. The run is not finished at that "
    "point -- it reports stopping until the sandbox has been confirmed gone, and a stop "
    "that fails does not become a cancellation that worked.",
    "This gateway runs on one host for development. It is not multi-tenant, not "
    "production-safe and not escape-proof, and nothing here should be described as any "
    "of those.",
    "None of this is authorisation to expose this sandbox publicly, to deploy it, or to charge "
    "for it. It is a closed test service.",
)


#: Every state a caller can be shown, so a client can be written against a closed list
#: rather than against whatever it has happened to see.
#:
#: `stopping` is the one worth explaining. Cancelling is not instant -- the sandbox has to
#: be torn down and confirmed gone, which is what makes a terminal state trustworthy -- so
#: a cancel is ACCEPTED immediately and the run sits in `stopping` until that confirmation
#: arrives. Holding the caller until then would mean a person watching a spinner for the
#: length of the settle window.
RUNNING_STATES = ("accepted", "running", "stopping")
FINISHED_STATES = ("finished", "refused", "cancelled", "unverified", "interrupted")
STATES = RUNNING_STATES + FINISHED_STATES

OPERATIONS = (
    Operation(
        name="capabilities",
        audience=TOOL,
        risk=READS,
        confirms_with_a_person=False,
        since="1",
        needs=READ,
        summary="What this sandbox can do, what it will enforce, and which operations it has.",
        params=(),
        returns=(
            Field("protocol", "string", "the protocol version this gateway speaks"),
            Field("operations", "array", "each operation, with the version that introduced it"),
            Field("capabilities", "array", "what this device has been granted"),
            Field("enforces", "object", "what the sandbox was measured to actually enforce"),
            # Additive in protocol 1. A customer area has to be able to show that the operator
            # has stopped the sandbox WITHOUT first trying to run something and reading the
            # refusal -- "why can I not start anything" should be answerable before you try.
            Field("accepting_work", "boolean",
                  "whether the operator currently has this sandbox taking new work"),
            Field("not_accepting_because", "string",
                  "the operator's reason, if it is not taking work", required=False),
            Field("what_this_does_not_establish", "array",
                  "the limits of this arrangement, which every client is told before it starts"),
        ),
        errors=COMMON,
    ),
    Operation(
        name="prepare",
        audience=TOOL,
        risk=READS,
        confirms_with_a_person=False,
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
            Field("decided_by", "object",
                  "the account and the way in this was disclosed to; a disclosure shown to one "
                  "device, over one door, is not usable from another", since="2"),
            Field("requested_policy_sha256", "string",
                  "digest of the policy being asked for, composed by this gateway", since="2"),
            Field("operator_policy_sha256", "string",
                  "digest of the operator policy in force when this was shown", since="2"),
            Field("secrets", "object",
                  "which named secrets would be released to the job -- names only, never "
                  "values", since="2"),
            Field("not_modelled", "array",
                  "things a disclosure like this is often expected to cover that this sandbox "
                  "does not model, said plainly rather than left to be assumed", since="2"),
            Field("accepted_disclosure", "string",
                  "what to send with the submission to show this is what was agreed to"),
        ),
        errors=COMMON + ("refused_by_policy", "over_a_ceiling"),
    ),
    Operation(
        name="submit",
        audience=TOOL,
        risk=AFFECTS_A_RUN,
        confirms_with_a_person=True,
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
            # Declared optional ON PURPOSE, and refused by `submit` rather than by the shape
            # check. A client from before the gate existed sends nothing here, and what it needs
            # back is not "a field is missing" but which call to make, in what order, and why --
            # so the refusal is `disclosure_required`, which carries that. Optional in the shape,
            # mandatory in fact.
            Field("accepted_disclosure", "string",
                  "the disclosure this was started against, as prepare() returned it",
                  required=False),
            # Everything below was expressible in the older signed request and was not
            # expressible here. Declaring it is what made translating those requests onto this
            # operation possible WITHOUT narrowing them: a field the contract cannot carry is a
            # requirement that would have been dropped rather than refused, which is the one
            # outcome worse than not migrating at all.
            Field("job_id", "string",
                  "the job this run belongs to, when it is not the run itself",
                  required=False, since="2"),
            Field("required_properties", "array",
                  "what the sandbox must actually enforce, or the job is refused rather than "
                  "run with less", required=False, since="2"),
            Field("mandatory", "array",
                  "policy fields that must survive composition; narrowing one is refused",
                  required=False, since="2"),
            Field("optional", "array",
                  "policy fields the job would like; narrowing one is reported as a delta "
                  "rather than silently applied", required=False, since="2"),
            Field("nonce", "string",
                  "chosen by the caller so a replayed request is seen as one",
                  required=False, since="2"),
        ),
        returns=(
            Field("run_id", "string", "how to ask about it"),
            Field("state", "string", "where it is now"),
            Field("admitted_under", "object", "the ceilings and policy it was admitted against"),
            Field("request_policy_sha256", "string",
                  "digest of the policy that was ASKED for, composed by this gateway rather "
                  "than sent by the caller", required=False, since="2"),
            Field("effective_policy_sha256", "string",
                  "digest of the policy actually granted", required=False, since="2"),
            Field("answer_binding", "object",
                  "the gateway's identity, protocol, binding and signature over this "
                  "answer -- present only when the request proved it holds the token's "
                  "secret, because nobody else could check it", required=False,
                  since="2"),
        ),
        errors=COMMON + ("refused_by_policy", "over_a_ceiling", "sandbox_unavailable",
                         "disclosure_required", "upgrade_required"),
        changes=True,
    ),
    Operation(
        name="status",
        audience=TOOL,
        risk=READS,
        confirms_with_a_person=False,
        since="1",
        needs=READ,
        summary="Where a run has got to.",
        params=(Field("run_id", "string", "the run to ask about"),),
        returns=(
            Field("run_id", "string", "the run asked about"),
            Field("state", "string", "where it is", one_of=STATES),
            Field("started_at", "integer", "when it began", required=False),
            Field("finished_at", "integer", "when it ended", required=False),
            Field("answer_binding", "object",
                  "the gateway's identity, protocol, binding and signature over this "
                  "answer -- present only when the request proved it holds the token's "
                  "secret, because nobody else could check it", required=False,
                  since="2"),
        ),
        errors=COMMON + ("no_such_run",),
    ),
    Operation(
        name="result",
        audience=TOOL,
        risk=READS,
        confirms_with_a_person=False,
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
            Field("answer_binding", "object",
                  "the gateway's identity, protocol, binding and signature over this "
                  "answer -- present only when the request proved it holds the token's "
                  "secret, because nobody else could check it", required=False,
                  since="2"),
        ),
        errors=COMMON + ("no_such_run", "not_finished"),
    ),
    Operation(
        name="cancel",
        audience=TOOL,
        risk=AFFECTS_A_RUN,
        confirms_with_a_person=False,
        since="1",
        needs=RUN,
        summary="Ask for a run to be stopped. Comes back at once; the run reports "
                "stopping until the sandbox has been confirmed gone.",
        params=(Field("run_id", "string", "the run to stop"),),
        returns=(
            Field("run_id", "string", "the run being stopped"),
            Field("state", "string", "where it is now -- stopping, or already finished",
                  one_of=STATES),
            Field("accepted", "boolean",
                  "whether this call is what started the stopping", since="2"),
            Field("attempts", "integer",
                  "how many times this gateway has tried to stop this run", since="2"),
            Field("cleanup_verified", "boolean",
                  "whether the sandbox was confirmed gone; absent means not yet known",
                  required=False),
            Field("problem", "string",
                  "why the last attempt did not confirm the sandbox gone, if it did not",
                  required=False, since="2"),
            Field("answer_binding", "object",
                  "the gateway's identity, protocol, binding and signature over this "
                  "answer -- present only when the request proved it holds the token's "
                  "secret, because nobody else could check it", required=False,
                  since="2"),
        ),
        # Cancelling is bounded like everything else. A device that asks for many stops in a
        # short time is refused with `over_a_ceiling`; asking again about a stop already in
        # flight is free, because that is how a client watches its own cancellation.
        errors=COMMON + ("no_such_run",),
        changes=True,
    ),
    Operation(
        name="usage",
        audience=TOOL,
        risk=READS,
        confirms_with_a_person=False,
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
        audience=TOOL,
        risk=READS,
        confirms_with_a_person=False,
        since="1",
        needs=MANAGE_DEVICES,
        summary="Which devices can reach this sandbox as you, and when each was last used.",
        params=(),
        returns=(Field("devices", "array", "each device, with its name and when it was last used"),),
        errors=COMMON,
    ),
    Operation(
        name="devices.rotate",
        audience=PERSON,
        risk=CHANGES_ACCESS,
        confirms_with_a_person=True,
        since="2",
        needs=MANAGE_DEVICES,
        summary="Replace this device's credential, keeping the identity behind it.",
        params=(),
        returns=(
            Field("token", "string", "the replacement credential"),
            Field("device_id", "string", "the identity it still belongs to"),
        ),
        # Deliberately behind MANAGE_DEVICES rather than RUN. Credential management is not
        # sandbox use, and an AI holding a device's token should not be able to mint its
        # successor as one ordinary tool call.
        errors=COMMON,
        changes=True,
    ),
    Operation(
        name="devices.revoke",
        audience=PERSON,
        risk=CHANGES_ACCESS,
        confirms_with_a_person=True,
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


#: A TRIPWIRE, not the mechanism. What decides tool exposure is the declared `audience`; this
#: exists so that an operation whose own name says it does one of these, and which was declared
#: a tool anyway, is caught by a test rather than shipped. Deriving the class from the name is
#: what this replaced, and a tripwire is not a derivation: nothing reads it at runtime.
NAMES_THAT_SHOULD_NEVER_BE_TOOLS = (
    ("rotate a credential", ("rotate", "renew_token", "reissue")),
    ("withdraw a device or a session", ("revoke", "withdraw", "sign_out", "end_session")),
    ("make an invitation", ("invite", "invitation", "pair", "pairing", "enrol", "enroll")),
    ("change the operator's policy", ("set_policy", "policy_set", "allowance_set", "ceiling_set")),
    ("work the kill switch", ("kill", "halt", "stop_gateway", "shutdown", "resume_gateway")),
    ("change billing or account limits", ("billing", "invoice", "quota_set", "plan")),
)


def for_a_model() -> tuple:
    """The operations that may be rendered as tools. The only place that decides it."""
    check_classifications()
    return tuple(op for op in OPERATIONS if op.audience == TOOL)


class NotClassified(ValueError):
    """The contract does not describe itself, so nothing may be generated, started or served."""


def check_classifications() -> None:
    """Refuse the WHOLE contract if any operation's classification is unusable.

    `Operation.__post_init__` already refuses to build one, so reaching here means something was
    constructed another way -- a loader, a plugin, a test. Every generator, the gateway's start
    and the dispatcher call this.

    It raises rather than skipping, and that distinction is the point. A generator that quietly
    left out what it could not classify would publish a shorter schema and exit zero, and a
    shorter schema that looks like a successful build is how an operation ends up reachable over
    one transport and invisible on another -- with nobody having decided anything.
    """
    for op in OPERATIONS:
        for what, value, allowed in (("audience", op.audience, AUDIENCES),
                                     ("risk", op.risk, RISK_CLASSES),
                                     ("required permission", op.needs, CAPABILITIES)):
            if value is UNCLASSIFIED:
                raise NotClassified(
                    "%s does not declare its %s, so this contract does not describe itself and "
                    "nothing can be generated, started or served from it." % (op.name, what))
            if value not in allowed:
                raise NotClassified(
                    "%s declares %s %r, which is not one of %s."
                    % (op.name, what, value, ", ".join(allowed)))
        if op.confirms_with_a_person is UNCLASSIFIED:
            raise NotClassified(
                "%s does not say whether a person has to have agreed to it." % op.name)
        if op.audience == TOOL and op.risk in NEVER_FOR_A_MODEL:
            raise NotClassified(
                "%s is declared as a tool and as %s. A model calling it cannot be asked whether "
                "it should." % (op.name, op.risk))
        if op.risk in NEVER_FOR_A_MODEL and op.needs != MANAGE_DEVICES:
            raise NotClassified(
                "%s is %s but asks only for %s." % (op.name, op.risk, op.needs))


def usable(op) -> bool:
    """Whether one operation is classified well enough to be carried out at all."""
    return bool(op is not None
                and op.audience in AUDIENCES and op.risk in RISK_CLASSES
                and op.needs in CAPABILITIES and op.confirms_with_a_person is not UNCLASSIFIED
                and not (op.audience == TOOL and op.risk in NEVER_FOR_A_MODEL))


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
    """The contract as data, for `capabilities` to return and for the generators to render.

    Validates first. This is the one function every rendering and every `capabilities` answer
    goes through, so a contract that does not describe itself cannot be described.
    """
    check_classifications()
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
