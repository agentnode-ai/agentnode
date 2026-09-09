"""Recording an external run so the record can be checked rather than believed.

`EM3C-EXTERNAL-0017` blocked on the evidence, not on the code, and the findings were specific: a
transcript naming one run while the record beside it was about another; exit statuses read from the
end of a pipe, which is always zero and therefore always looked like success; summarised output that
could not be told apart from a check that never ran.

`EM3C-E2-CLASSIFY-0001` then found the contract itself broken. The verifier read `expect_output` and
`expect_cleanup`; the `Step` could carry neither. Omitting them silently disabled both rules and
passing them raised, so no recorder could satisfy the contract by any route. The module's own suite
passed throughout, because its verifier tests built dictionaries by hand containing fields the
recorder could not produce -- a double that does not produce what the real thing produces tests the
double.

So this module now has ONE closed schema. The dataclass below is the whole vocabulary. What the
recorder writes, what the file holds, what a reader may load and what the verifier may read are the
same names with the same types, and every entry point refuses anything else.

## Two kinds of field, kept apart

`Step` is divided deliberately. The expectation fields are declared before a step runs and say what
it was supposed to establish. Everything else is what happened. Nothing here derives one from the
other: an expectation that quietly became an observation is exactly the "synthetic success" that
made two external runs worthless, and `expected_exit` sitting beside `exit_code` in one record is
only safe while nothing writes the second from the first.

## Three outcomes, not two

A rule can fail, and a rule can be unable to see its input. Those are different, and collapsing them
is how "the container is gone" came to mean "the command that would have told us failed". Findings
carry `FAIL` or `EVIDENCE_ERROR`, and neither is a pass.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass, fields
from typing import Any

SCHEMA = 2

#: A rule that was not satisfied.
FAIL = "fail"
#: A rule that could not be evaluated, because what it needed was missing, unreadable or never
#: obtained. Never a pass, and never reported as a failure of the thing under test.
EVIDENCE_ERROR = "evidence_error"

#: Redaction by argument NAME, for values this code has never seen. Value-based redaction covers
#: the ones it has been told about; neither alone is enough.
_SECRET_FLAGS = ("--code", "--token", "--secret", "--password", "--key")
REDACTED = "[redacted]"


class _Unset:
    """A value that means nobody said, distinct from every value anyone could say."""

    __slots__ = ()

    def __repr__(self) -> str:                                    # pragma: no cover - debugging
        return "UNSET"


UNSET = _Unset()



class EvidenceError(Exception):
    """The evidence does not establish what it claims, or cannot be read at all."""


@dataclass
class Finding:
    """One reason a record does not establish what it claims."""

    step: str
    kind: str
    message: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.step}: {self.message}"


# --------------------------------------------------------------------------- the schema


@dataclass
class Step:
    """One recorded action. This dataclass IS the schema; nothing else defines it."""

    # -- what identifies the step ------------------------------------------------------------
    name: str
    role: str
    argv: list
    started_at: float
    ended_at: float
    #: The command's OWN status. None when it never produced one -- a missing executable, a
    #: timeout -- which is unknown, not zero.
    exit_code: int | None

    # -- what was observed --------------------------------------------------------------------
    stdout: str = ""
    stderr: str = ""
    error_class: str = ""
    run_id: str = ""
    job_id: str = ""
    client_id: str = ""
    request_policy_sha256: str = ""
    effective_policy_sha256: str = ""
    policy_deltas: list | None = None
    gateway_record: dict | None = None
    container: str = ""
    container_query: dict | None = None
    cleanup_verified: Any = None
    machine: dict | None = None
    sentinel: dict | None = None
    binding: dict | None = None
    #: What the client observed about the answer beside it. Never part of the answer.
    answer: dict | None = None

    # -- what was expected, declared before the step ran ---------------------------------------
    #: `UNSET` rather than a value. `EM3C-EVIDENCE-0002`: with ordinary defaults, a caller who
    #: never thought about the question produced a record identical to one that had considered it
    #: and said no. The recorder refuses a step that left any of these unset, so the difference
    #: between "not required" and "nobody said" survives all the way to the file.
    expected_exit: Any = UNSET
    expected_refusal: Any = UNSET
    expect_output: Any = UNSET
    expect_cleanup: Any = UNSET
    expect_container_gone: Any = UNSET

    def as_dict(self) -> dict:
        return asdict(self)


#: Declared before the step ran. Nothing may write these from what happened.
EXPECTATIONS = ("expected_exit", "expected_refusal", "expect_output", "expect_cleanup",
                "expect_container_gone")

#: Written by the recorder from what happened. A caller may not supply these to `run()`.
OBSERVED_BY_RUNNING = ("exit_code", "stdout", "stderr", "error_class")

#: Without these a step says nothing, so their absence is refused rather than defaulted.
#: The expectations are here too: the recorder always writes them, so a record that omits one did
#: not come from a recorder, and defaulting it to false would make "the check was not required"
#: and "nobody said" the same thing. `EM3C-EVIDENCE-0001` named that ambiguity.
MANDATORY = ("name", "role", "argv", "started_at", "ended_at", "exit_code",
             "expected_exit", "expected_refusal", "expect_output", "expect_cleanup",
             "expect_container_gone")

#: Fields the verifier reads. `EM3C-EVIDENCE-0009`: this used to be a subset, with the remainder
#: named in a `RECORDED_ONLY` list and defended as a decision rather than an oversight. But an
#: unread field is a field nothing can contradict, and the schema is closed in BOTH directions
#: now: this tuple is the whole of `FIELD_NAMES`, and a test holds the two against each other.
#: `notes` was the one field with no rule that could be written for it, so it is gone; what the
#: driver kept there is in the sentinel, where `_sentinel_findings` reads it.
READ_BY_RULES = (
    "name", "role", "argv", "started_at", "ended_at", "exit_code", "stdout", "stderr",
    "error_class", "run_id", "gateway_record", "container", "container_query", "machine",
    "sentinel", "binding", "answer", "request_policy_sha256", "effective_policy_sha256",
    "expected_exit", "expected_refusal", "expect_output", "expect_cleanup",
    "expect_container_gone",
    "job_id", "client_id", "policy_deltas", "cleanup_verified",
)

#: The shapes of the nested structures. `dict` alone says nothing about what is inside, and the
#: rules below read specific keys out of these.
_NESTED: dict[str, dict] = {
    "machine": {"required": ("role", "host_sha256", "filesystem_sha256", "os", "commands"),
                "optional": (),
                "types": {"role": (str,), "host_sha256": (str,), "filesystem_sha256": (str,),
                          "os": (str,), "commands": (list,)},
                # `commands` was closed as a list and open in its elements, so a reader accepted
                # anything inside it while the rules read four particular keys out of every
                # entry (`EM3C-EVIDENCE-0005`). Each element is a record of one command, and the
                # reader checks that rather than leaving the rule to second-guess it.
                "elements": {"commands": {
                    "required": ("command", "exit_code", "stdout", "stderr"),
                    "optional": (),
                    "types": {"command": (str,), "exit_code": (int, type(None)),
                              "stdout": (str,), "stderr": (str,)}}}},
    "sentinel": {"required": ("generated_on", "carried_over", "confirmed_over", "value_sha256",
                              "in_request", "in_response", "in_other_channel", "matched",
                              "request_text"),
                 "optional": (),
                 "types": {"generated_on": (str,), "carried_over": (str,),
                           "confirmed_over": (str,), "value_sha256": (str,),
                           "in_request": (bool,), "in_response": (bool,),
                           "in_other_channel": (bool,), "matched": (bool,),
                           # What the client actually sent. `in_request` decides the origin, and
                           # until now it was a boolean the recorder asserted. With the request
                           # itself in the record the rule can check it, so the direction a
                           # value travelled is read out of the evidence rather than believed.
                           "request_text": (str,)}},
    "container_query": {"required": ("ran", "exit_code", "stdout", "stderr", "error_class",
                                     "parsed", "command"),
                        "optional": ("names", "ids", "sought_id", "complete"),
                        "types": {"ran": (bool,), "exit_code": (int, type(None)),
                                  "stdout": (str,), "stderr": (str,), "error_class": (str,),
                                  "parsed": (bool,), "command": (str,), "names": (list,),
                                  "ids": (list,), "sought_id": (str,), "complete": (bool,)}},
    "binding": {"required": ("mode", "generation", "policy_digest", "configured_digest",
                             "digests_agree", "required_properties", "allowlist", "runtime",
                             "backend", "conformance_digest"),
                "optional": (),
                "types": {"mode": (str,), "generation": (str,), "policy_digest": (str,),
                          "configured_digest": (str,), "digests_agree": (str,),
                          "required_properties": (str,), "allowlist": (list,),
                          "runtime": (str,), "backend": (str,), "conformance_digest": (str,)}},
}

# --------------------------------------------------------- what the gateway actually answers
#
# NOTHING BELOW DESCRIBES THE GATEWAY'S ANSWER. It asks the code that produces one.
#
# `EM3C-E3-CLASSIFY-0001`: the one authorised external run died here. This module carried its
# own list of fields, taken from `RunRecord.public()` -- an object that exists INSIDE the
# gateway. What a client receives is that object inside an envelope the gateway stamps and
# signs, so every real answer carried five fields this reader had never heard of and every real
# answer was refused. A test was supposed to hold the list against the code; it held it against
# the inner object, which is the wrong end of the same mistake.
#
# So there is no list. `_production()` calls the gateway's own functions and uses what they
# return. A field added on the gateway side arrives here without anyone editing this file.

_PRODUCTION: dict = {}


def _production() -> dict:
    """The shapes the gateway defines, from the gateway.

    Imported lazily and once: this module is also the thing that reads a record back on a
    machine that may not be a gateway, and paying for the gateway's imports at import time
    would make that worse for no gain.
    """
    if not _PRODUCTION:
        from agentnode_sdk.gateway.policy_paths import policy_shape
        from agentnode_sdk.gateway.protocol import (
            ERROR_FIELDS, PROTOCOL_VERSION, SIGNATURE_FIELDS, STAMP_FIELDS, binding_fields,
            refusal, response_binding,
        )
        from agentnode_sdk.gateway.server import RunRecord

        import typing

        from agentnode_sdk.gateway.identity import GatewayIdentity
        from agentnode_sdk.gateway.protocol import canonical_bytes, digest, stamp_fields

        hints = typing.get_type_hints(RunRecord)
        inner = tuple(RunRecord(run_id="", job_id="").public())
        # `policy_deltas` is the only public key whose attribute is named differently.
        attribute = {"policy_deltas": "deltas"}
        types = {key: _runtime_types(hints[attribute.get(key, key)]) for key in inner}

        # The envelope's types come from a real stamp over a real identity, so they are the
        # types the gateway really puts there rather than the ones this file expects.
        stamped = stamp_fields(GatewayIdentity(gateway_id="0" * 32, version="0"))
        types.update({key: (type(value),) for key, value in stamped.items()})
        types["binding"] = (dict,)
        types["signature"] = (str,)
        # From a real refusal, so its type is the one the gateway really writes.
        types.update({k: (type(v),) for k, v in refusal("").items()})

        _PRODUCTION.update({
            "inner": inner,
            "stamp": tuple(STAMP_FIELDS),
            "signature": tuple(SIGNATURE_FIELDS),
            "binding": tuple(binding_fields()),
            "policy": tuple(policy_shape(None)),
            "protocol": PROTOCOL_VERSION,
            "error": tuple(ERROR_FIELDS),
            "response_binding": response_binding,
            "types": types,
            "seal": lambda answer: digest(canonical_bytes(answer)),
        })
    return _PRODUCTION


def answer_fields() -> tuple[str, ...]:
    """Every key a client-visible answer may carry, from the code that puts them there.

    Four groups, four production sources: the run record, the stamp, the signature, and what an
    answer carries instead of a run when there is no run to describe. `EM3C-EVIDENCE-0014`: the
    last of those was named here, which made this a second declaration of the protocol however
    carefully it was held against a real answer.
    """
    p = _production()
    return tuple(p["inner"]) + tuple(p["stamp"]) + tuple(p["signature"]) + tuple(p["error"])


def _runtime_types(hint) -> tuple:
    """The runtime types an annotation admits.

    `float` also admits `int`, because JSON has one number type and a whole number comes back as
    an int. Nothing else is widened: this is the gateway's declaration, read rather than guessed.
    """
    import types as _types
    import typing

    if typing.get_origin(hint) in (typing.Union, getattr(_types, "UnionType", None)):
        found: tuple = ()
        for part in typing.get_args(hint):
            found += _runtime_types(part)
        return found
    if hint is float:
        return (int, float)
    return (hint,) if isinstance(hint, type) else (object,)


def answer_types() -> dict:
    """The type of every key an answer may carry, from the code that declares them.

    `EM3C-EVIDENCE-0013`: the names were derived and the types were not, so a key nobody had
    thought about was accepted whatever it held. Nothing is open now, and a key that cannot be
    resolved to a declared type makes this refuse rather than wave it through.
    """
    types = dict(_production()["types"])
    missing = [k for k in answer_fields() if k not in types]
    if missing:
        raise EvidenceError(
            "the gateway declares fields whose type this reader cannot resolve: "
            + ", ".join(sorted(missing)) + ". Nothing is read until it can.")
    return types

#: A delta says which policy field changed and what it held on each side. The two values are
#: whatever that field's type is, so they are deliberately not typed -- but the entry around
#: them is, so this is an arbitrary VALUE rather than an arbitrary record.
_DELTA = {"required": ("path", "requested", "effective"), "optional": (), "types": {"path": (str,)}}

#: What the CLIENT observed about an answer, which is not part of the answer. Kept apart on
#: purpose: an answer is the gateway's, and whether the client's own verification accepted it is
#: the client's. Mixing the two is how a self-made answer gets recorded as a received one.
_ANSWER_OBSERVED = {
    "required": ("http_status", "verified", "refusal", "asked_for", "verified_sha256"),
    "optional": (),
    "types": {"http_status": (int, type(None)), "verified": (bool,), "refusal": (str,),
              "asked_for": (str,), "verified_sha256": (str,)},
}


def _policy_shape_for_reading() -> dict:
    """The policy map a job ran under, with the keys the gateway's own canonical shape pins."""
    return {
        # The two the containment rule reads. Everything else the gateway pins is allowed.
        "required": ("network.enabled", "network.allowed_destinations"),
        "optional": tuple(k for k in _production()["policy"]
                          if k not in ("network.enabled", "network.allowed_destinations")),
        "types": {"network.enabled": (bool,),
                  "network.allowed_destinations": (list, type(None)),
                  "limits.cpu": (int, float, type(None)),
                  "limits.memory_mb": (int, float, type(None)),
                  "limits.processes": (int, type(None)),
                  "limits.wall_clock_s": (int, float, type(None))},
    }




_TYPES: dict[str, tuple] = {
    "name": (str,), "role": (str,), "argv": (list,),
    "started_at": (int, float), "ended_at": (int, float),
    "exit_code": (int, type(None)),
    "stdout": (str,), "stderr": (str,), "error_class": (str,),
    "run_id": (str,), "job_id": (str,), "client_id": (str,),
    "request_policy_sha256": (str,), "effective_policy_sha256": (str,),
    "policy_deltas": (list, type(None)),
    "gateway_record": (dict, type(None)),
    "container": (str,),
    "container_query": (dict, type(None)),
    "cleanup_verified": (bool, str, type(None)),
    "machine": (dict, type(None)),
    "sentinel": (dict, type(None)),
    "binding": (dict, type(None)),
    "answer": (dict, type(None)),
    "expected_exit": (int, type(None)),
    "expected_refusal": (str,),
    "expect_output": (bool,), "expect_cleanup": (bool,), "expect_container_gone": (bool,),
}

FIELD_NAMES = tuple(f.name for f in fields(Step))

if set(FIELD_NAMES) != set(_TYPES):                               # pragma: no cover - a guard
    raise RuntimeError("the schema and its declared types have drifted apart")


#: The digest of nothing at all. A recorder that hashed an absent value produced exactly this,
#: and it is indistinguishable from the digest of a real one -- so a presence check passes and an
#: identity nobody established becomes an identity (`EM3C-EVIDENCE-0005`). Refused by value,
#: wherever it appears, because nothing worth recording has this digest.
DIGEST_OF_NOTHING = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _closed(where: str, value, shape: dict) -> None:
    """One object against one declared shape: no unknown key, no missing one, no wrong type."""
    if not isinstance(value, dict):
        raise EvidenceError(f"{where} is {type(value).__name__} and the schema says a record.")
    known = set(shape["required"]) | set(shape.get("optional", ()))
    strange = sorted(set(value) - known)
    if strange:
        raise EvidenceError(
            f"{where} carries " + ", ".join(repr(x) for x in strange) +
            ", which its shape does not describe.")
    absent = [k for k in shape["required"] if k not in value]
    if absent:
        raise EvidenceError(f"{where} has no " + ", ".join(absent) + ", so it cannot be read.")
    for key, item in value.items():
        allowed = shape["types"].get(key)
        if allowed is None:
            continue
        if isinstance(item, bool) and bool not in allowed:
            raise EvidenceError(f"{where}.{key} is a boolean and its shape says otherwise.")
        if not isinstance(item, allowed):
            raise EvidenceError(
                f"{where}.{key} is {type(item).__name__} and its shape says "
                + " or ".join(t.__name__ for t in allowed) + ".")


#: The flat view the older rules use. A function, because the types come from the gateway.
def _gateway_record_types() -> dict:
    return answer_types()


def _no_duplicates(pairs):
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise EvidenceError(
                f"the key {key!r} appears more than once in one record. One value would have been "
                "kept and the other dropped, so neither is trusted.")
        seen[key] = value
    return seen


def parse_step(text_or_mapping) -> dict:
    """Read one recorded step, refusing anything the schema does not describe.

    Unknown, missing, duplicated and wrongly typed all fail closed here. A reader that quietly
    filled in a default would make a record that never carried a field indistinguishable from one
    that carried it false -- the difference between "cleanup was not required" and "cleanup was
    required and nobody looked".
    """
    if isinstance(text_or_mapping, str):
        try:
            document = json.loads(text_or_mapping, object_pairs_hook=_no_duplicates)
        except EvidenceError:
            raise
        except ValueError as exc:
            raise EvidenceError(f"this is not readable as JSON: {exc}") from None
    else:
        document = dict(text_or_mapping)

    if not isinstance(document, dict):
        raise EvidenceError("a step has to be an object.")

    schema = document.pop("schema", None)
    if schema is not None and schema != SCHEMA:
        raise EvidenceError(
            f"this record is schema {schema!r} and this build reads {SCHEMA}. It is not read "
            "approximately.")

    unknown = sorted(set(document) - set(FIELD_NAMES))
    if unknown:
        raise EvidenceError(
            "this record carries " + ", ".join(repr(u) for u in unknown) +
            ", which the schema does not describe. An unrecognised field reads as recorded and is "
            "never checked, so it is refused rather than ignored.")

    missing = [name for name in MANDATORY if name not in document]
    if missing:
        raise EvidenceError(
            "this record has no " + ", ".join(missing) + ", so nothing about it is established.")

    for name, value in document.items():
        allowed = _TYPES[name]
        if isinstance(value, bool) and bool not in allowed:
            raise EvidenceError(
                f"{name} is a boolean and the schema says "
                + " or ".join(t.__name__ for t in allowed) + ".")
        if not isinstance(value, allowed):
            raise EvidenceError(
                f"{name} is {type(value).__name__} and the schema says "
                + " or ".join(t.__name__ for t in allowed) + ".")

    for name, shape in _NESTED.items():
        nested = document.get(name)
        # (`_closed` below is the same check, so a nested list and a nested object are read by
        # one rule rather than by two that can drift apart.)
        if nested is None:
            continue
        known = set(shape["required"]) | set(shape["optional"])
        strange = sorted(set(nested) - known)
        if strange:
            raise EvidenceError(
                f"{name} carries " + ", ".join(repr(x) for x in strange) +
                ", which its shape does not describe.")
        absent = [k for k in shape["required"] if k not in nested]
        if absent:
            raise EvidenceError(
                f"{name} has no " + ", ".join(absent) + ", so it cannot be read.")
        for key, value in nested.items():
            allowed = shape["types"].get(key)
            if allowed is None:
                continue
            if isinstance(value, bool) and bool not in allowed:
                raise EvidenceError(f"{name}.{key} is a boolean and its shape says otherwise.")
            if not isinstance(value, allowed):
                raise EvidenceError(
                    f"{name}.{key} is {type(value).__name__} and its shape says "
                    + " or ".join(t.__name__ for t in allowed) + ".")

        for key, inner in shape.get("elements", {}).items():
            for index, entry in enumerate(nested.get(key) or ()):
                _closed(f"{name}.{key}[{index}]", entry, inner)

    # The gateway's own record, and the two policy maps inside it that the containment rule
    # reads. Closed like everything else: a rule reads them, so what they may contain is this
    # module's business whoever wrote them (`EM3C-EVIDENCE-0006`).
    record = document.get("gateway_record")
    if isinstance(record, dict):
        _closed("gateway_record", record, {"required": (), "optional": answer_fields(),
                                           "types": answer_types(),
                                           "elements": {"policy_deltas": _DELTA}})
        for key in ("requested_policy", "effective_policy"):
            if isinstance(record.get(key), dict):
                _closed(f"gateway_record.{key}", record[key], _policy_shape_for_reading())
    observed = document.get("answer")
    if isinstance(observed, dict):
        _closed("answer", observed, _ANSWER_OBSERVED)
    return document


# --------------------------------------------------------------------------- redaction


def redact_argv(argv) -> list[str]:
    """A command line safe to write down, with the values of secret-bearing flags removed."""
    out: list[str] = []
    skip = False
    for item in argv:
        text = str(item)
        if skip:
            out.append(REDACTED)
            skip = False
            continue
        if text in _SECRET_FLAGS:
            out.append(text)
            skip = True
            continue
        if "=" in text and text.split("=", 1)[0] in _SECRET_FLAGS:
            out.append(text.split("=", 1)[0] + "=" + REDACTED)
            continue
        out.append(text)
    return out


def redact_text(text: str, secrets) -> str:
    for secret in secrets or ():
        if secret and len(str(secret)) >= 8:
            text = text.replace(str(secret), REDACTED)
    return text


#: Key names whose VALUES are removed wherever they appear, whatever they contain. Value-list
#: redaction only removes what somebody thought to collect; this removes what sits in a place
#: secrets sit. `EM3C-EVIDENCE-0001` was right that neither alone is enough, and that the two
#: together still cannot reach a secret nobody named in a place nobody expected.
_SECRET_KEYS = ("token", "secret", "password", "passwd", "credential", "private_key",
                "privatekey", "api_key", "apikey", "pairing_code", "auth")

#: Anything shaped like private key material, wherever it turns up.
_KEY_MATERIAL = ("BEGIN OPENSSH PRIVATE KEY", "BEGIN RSA PRIVATE KEY", "BEGIN PRIVATE KEY",
                 "BEGIN EC PRIVATE KEY", "BEGIN PGP PRIVATE KEY")


def _is_secret_key(name) -> bool:
    lowered = str(name).lower()
    return any(marker in lowered for marker in _SECRET_KEYS)


def redact_deep(value, secrets, *, under_secret_key=False):
    """Redact through every value that will be serialised, at any depth.

    Three layers, because each misses what the others catch. Known VALUES are removed wherever
    they appear. Values under a key that NAMES a secret are removed whatever they contain, so a
    token nobody collected still goes. And anything carrying a private-key header is removed on
    sight, because that material is recognisable without being known.

    What none of them reaches is a secret with an unremarkable name, an unremarkable shape, and
    a value nobody collected. That is a real limit, and `verify` reports the known values it can
    still find rather than implying there are none.
    """
    if isinstance(value, str):
        if under_secret_key and value.strip():
            return REDACTED
        if any(marker in value for marker in _KEY_MATERIAL):
            return REDACTED
        return redact_text(value, secrets)
    if isinstance(value, dict):
        return {redact_deep(k, secrets): redact_deep(v, secrets,
                                                    under_secret_key=_is_secret_key(k))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_deep(v, secrets, under_secret_key=under_secret_key) for v in value]
    return value


# --------------------------------------------------------------------------- recording


#: Shown to whoever starts a recording, at the moment they start it.
#:
#: `EM3C-V8-DECISION-0001` chose to keep real command output in the record and to state the
#: exposure that comes with it, rather than to stop recording output or filter it to a shape
#: somebody predicted. It also decided that saying this in a module docstring and a documentation
#: page is not enough: the risk arrives when commands run and records are made, so it is said
#: then, to the person doing it.
RUN_NOTICE = (
    "This recording keeps the real output of every command it runs.\n"
    "  Values you name as secrets are removed wherever they appear, values under a key "
    "that names a secret are removed whatever they contain, and private-key material is "
    "removed on sight. If any secret you named survives that, nothing is written at all.\n"
    "  What is NOT removed is a credential nobody named, with an unremarkable name and "
    "shape, sitting in the middle of ordinary output. Treat the evidence file as you would "
    "treat the output of the commands themselves."
)


class Recorder:
    """Runs commands and writes down what happened, without judging any of it."""

    def __init__(self, path, role: str, secrets=(), announce=None) -> None:
        self.path = str(path)
        self.role = role
        self.secrets = list(secrets)
        self.steps: list[Step] = []
        #: Whether the notice was actually put in front of somebody. Recorded, so a run can show
        #: it rather than assert it, and so a run that suppressed it says so.
        self.announced = False
        if announce is not False:
            (announce or print)(RUN_NOTICE)
            self.announced = True

    def run(self, name: str, argv, *, timeout: float = 600.0, **extra) -> Step:
        """Execute one command. The exit code recorded is this command's own.

        Never raises for a missing executable or a timeout: both are OUTCOMES, and a recorder that
        propagated them would stop at the first step whose expected condition is that something is
        absent. That is how the first external run ended.
        """
        for reserved in OBSERVED_BY_RUNNING:
            if reserved in extra:
                raise EvidenceError(
                    f"{reserved} is an observation and cannot be supplied to run(): it is what the "
                    "command did, not what the caller expected of it.")
        safe = [redact_text(item, self.secrets) for item in redact_argv(argv)]
        started = time.time()
        try:
            done = subprocess.run(list(argv), capture_output=True, text=True,
                                  timeout=timeout, check=False)
            code, out, err, error_class = done.returncode, done.stdout or "", done.stderr or "", ""
        except FileNotFoundError as exc:
            code, out, err, error_class = None, "", str(exc), "FileNotFoundError"
        except subprocess.TimeoutExpired as exc:
            raw_out, raw_err = exc.stdout or "", exc.stderr or ""
            code, error_class = None, "TimeoutExpired"
            out = raw_out.decode("utf-8", "replace") if isinstance(raw_out, bytes) else raw_out
            err = raw_err.decode("utf-8", "replace") if isinstance(raw_err, bytes) else raw_err
        except OSError as exc:
            code, out, err, error_class = None, "", str(exc), type(exc).__name__

        # Nothing is filled in here. `EM3C-EVIDENCE-0003`: this path used to state, on the
        # caller's behalf, every expectation the caller had not -- which made "no check applies
        # to this step" and "nobody considered the question" the same record again, the exact
        # ambiguity the UNSET defaults exist to keep apart. Running a command for someone does
        # not include answering their question for them, so an expectation left out arrives at
        # `record` unset and is refused there, by name.
        stated = {k: v for k, v in extra.items() if k in EXPECTATIONS}
        rest = {k: v for k, v in extra.items() if k not in EXPECTATIONS}
        return self.record(Step(name=name, role=self.role, argv=safe,
                                started_at=started, ended_at=time.time(),
                                exit_code=code, stdout=out, stderr=err,
                                error_class=error_class, **stated, **rest))

    def record(self, step: Step) -> Step:
        unstated = [name for name in EXPECTATIONS if getattr(step, name) is UNSET]
        if unstated:
            raise EvidenceError(
                "this step never said whether " + ", ".join(unstated) + " applied. An unstated "
                "expectation and one deliberately set to nothing are different, and a record "
                "cannot carry the difference unless the caller states it.")
        self.steps.append(step)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(self.serialise(step), sort_keys=True,
                                    ensure_ascii=False) + "\n")
        return step

    def serialise(self, step: Step) -> dict:
        """The exact document written. Redaction happens here, once, over the whole record.

        What is written is then parsed by the same reader a verifier would use, so a recorder that
        produced something unreadable fails here rather than at the far end of a run.
        """
        document = redact_deep({"schema": SCHEMA, **step.as_dict()}, self.secrets)
        parse_step(dict(document))

        # The redactor's own failure has to be visible. If a value it was given survives its own
        # pass, nothing is written: a record that quietly contains a credential is worse than no
        # record, and finding out later from the file is finding out too late.
        blob = json.dumps(document, ensure_ascii=False, sort_keys=True)
        for secret in self.secrets:
            if secret and len(str(secret)) >= 8 and str(secret) in blob:
                raise EvidenceError(
                    "a value this recorder was told to redact survived its own pass, so nothing "
                    "was written. This is the redactor failing, not the step.")
        for marker in _KEY_MATERIAL:
            if marker in blob:
                raise EvidenceError(
                    "private key material reached a record, so nothing was written.")
        return document


# --------------------------------------------------------------------------- verification


def load(path) -> list[dict]:
    steps = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                steps.append(parse_step(line))
            except EvidenceError as exc:
                raise EvidenceError(f"line {number} of the evidence: {exc}") from None
    if not steps:
        raise EvidenceError("the evidence file records no steps at all.")
    return steps


def _container_findings(step, where) -> list[Finding]:
    """Whether a container may be concluded gone.

    Absence is the strongest claim in this file, because nothing is there to look at. It holds only
    when the query demonstrably ran and its answer was read. Every other outcome -- a timeout, no
    runtime, an SSH failure, a refusal, an unparseable or empty answer -- is an evidence error.
    `EM3C-E2-CLASSIFY-0001` found a harness turning all of those into an empty string and reading
    the empty string as "gone".
    """
    if not step.get("expect_container_gone"):
        return []
    query = step.get("container_query")
    if not isinstance(query, dict):
        return [Finding(where, EVIDENCE_ERROR,
                        "a container was supposed to be shown gone and no query was recorded")]
    if query.get("ran") is not True:
        return [Finding(where, EVIDENCE_ERROR, "the query for the container never ran")]
    if query.get("error_class"):
        return [Finding(where, EVIDENCE_ERROR,
                        f"the query failed ({query['error_class']}), so absence was never observed")]
    if query.get("exit_code") != 0:
        return [Finding(where, EVIDENCE_ERROR,
                        f"the query exited {query.get('exit_code')!r}; only a zero answer can be read")]
    if query.get("parsed") is not True:
        return [Finding(where, EVIDENCE_ERROR, "the query's answer was not parsed")]

    if query.get("complete") is not True:
        return [Finding(where, EVIDENCE_ERROR,
                        "the listing did not show that it ran to the end, so an empty answer "
                        "cannot be told apart from a truncated one")]
    if not str(query.get("stdout") or "").strip():
        return [Finding(where, EVIDENCE_ERROR,
                        "the listing produced nothing at all, which is an unknown answer rather "
                        "than an empty one")]

    names, ids = query.get("names"), query.get("ids")
    if not isinstance(names, list) or not isinstance(ids, list):
        return [Finding(where, EVIDENCE_ERROR,
                        "the query did not yield a list of container names and a list of ids")]
    sought_name = str(step.get("container") or "")
    sought_id = str(query.get("sought_id") or "")
    if not sought_name or not sought_id:
        # Both, not either. A name can be reused and an id cannot, so absence of one is a
        # weaker claim than absence of the two together -- and the criterion asks for the two.
        missing = "name" if not sought_name else "id"
        return [Finding(where, EVIDENCE_ERROR,
                        f"the container's {missing} was never named, so what was looked for is "
                        "not established")]

    still_there = []
    if sought_name and sought_name in names:
        still_there.append(f"name {sought_name}")
    if sought_id and sought_id in ids:
        still_there.append(f"id {sought_id}")
    if still_there:
        return [Finding(where, FAIL, "the container is still there: " + ", ".join(still_there))]
    return []


def _run_findings(step, where) -> list[Finding]:
    problems: list[Finding] = []
    run_id = (step.get("run_id") or "").strip()
    record = step.get("gateway_record")
    if not run_id:
        if step.get("expect_cleanup"):
            problems.append(Finding(
                where, EVIDENCE_ERROR,
                "cleanup was supposed to be established and the step names no run"))
        return problems
    if record is None:
        return [Finding(where, EVIDENCE_ERROR,
                        f"names run {run_id} and carries no gateway record for it, so only one "
                        "side of the run is evidenced")]

    if record.get("error") or record.get("status") == 404:
        problems.append(Finding(
            where, EVIDENCE_ERROR,
            f"the gateway had no record of run {run_id} "
            f"({record.get('error') or 'HTTP 404'}); an absent record is not a match"))
    recorded = str(record.get("run_id") or "")
    if recorded and recorded != run_id:
        problems.append(Finding(
            where, FAIL,
            f"the step is about run {run_id} and the record is about {recorded}. Two runs in one "
            "piece of evidence establish neither"))
    elif not recorded and not record.get("error"):
        problems.append(Finding(where, EVIDENCE_ERROR,
                                "the gateway record does not say which run it is about"))

    # `EM3C-EVIDENCE-0009`: these four were recorded and read by nothing, which made them
    # fields no evidence could contradict. Each is now held against the gateway's own answer.
    claimed_job = (step.get("job_id") or "").strip()
    held_job = str(record.get("job_id") or "")
    if claimed_job and held_job and claimed_job != held_job:
        problems.append(Finding(
            where, FAIL,
            f"the step is about job {claimed_job} and the gateway's record is about {held_job}"))
    elif held_job and not claimed_job:
        problems.append(Finding(
            where, EVIDENCE_ERROR,
            "the gateway record names a job and the step recorded none, so the two were never "
            "compared"))

    claimed_deltas = step.get("policy_deltas")
    held_deltas = record.get("policy_deltas")
    if claimed_deltas is not None and held_deltas is not None \
            and list(claimed_deltas) != list(held_deltas):
        problems.append(Finding(
            where, FAIL,
            "the narrowing the step recorded is not the narrowing the gateway reported, so one "
            "of the two is not about this run"))

    claimed_cleanup = step.get("cleanup_verified")
    held_cleanup = record.get("cleanup_verified", "__absent__")
    if claimed_cleanup is not None and held_cleanup != "__absent__" \
            and claimed_cleanup != held_cleanup:
        problems.append(Finding(
            where, FAIL,
            f"the step recorded cleanup as {claimed_cleanup!r} and the gateway says "
            f"{held_cleanup!r}"))

    for field_name in ("request_policy_sha256", "effective_policy_sha256"):
        claimed = (step.get(field_name) or "").strip()
        held = str(record.get(field_name) or "")
        if claimed and held and claimed != held:
            problems.append(Finding(
                where, FAIL,
                f"{field_name} recorded as {claimed[:12]}... and the gateway says {held[:12]}..."))
        elif claimed and not held:
            problems.append(Finding(
                where, EVIDENCE_ERROR,
                f"{field_name} was recorded but the gateway record has none to compare it with"))
        elif held and not claimed:
            problems.append(Finding(
                where, EVIDENCE_ERROR,
                f"the gateway record carries {field_name} and the step recorded none, so the two "
                "were never compared"))

    req = str(record.get("request_policy_sha256") or "")
    eff = str(record.get("effective_policy_sha256") or "")
    deltas = record.get("policy_deltas")
    if req and eff and req != eff:
        if deltas is None:
            problems.append(Finding(
                where, EVIDENCE_ERROR,
                "the policy digests differ and the record carries no policy_deltas at all"))
        elif not deltas:
            problems.append(Finding(
                where, FAIL,
                "the policy digests differ and no narrowing was reported, so the caller was not "
                "told what changed"))

    if step.get("expect_cleanup"):
        cleanup = record.get("cleanup_verified", "__absent__")
        if cleanup == "__absent__":
            problems.append(Finding(
                where, EVIDENCE_ERROR,
                "cleanup was supposed to be established and the record does not mention it"))
        elif cleanup is None:
            problems.append(Finding(where, EVIDENCE_ERROR,
                                    "cleanup is unknown, which is not the same as clean"))
        elif cleanup is not True:
            problems.append(Finding(where, FAIL, f"cleanup was not verified ({cleanup!r})"))
    return problems


_HEX64 = ("0123456789abcdef", 64)


def _looks_like_a_digest(value) -> bool:
    text = str(value or "")
    return len(text) == _HEX64[1] and all(c in _HEX64[0] for c in text)


#: The three network modes an operator policy can be in. A fourth value is not a stricter or a
#: looser policy, it is a policy this rule cannot place, and it is treated as such.
_MODES = ("none", "restricted", "unrestricted")


def _allowlist_findings(binding: dict, where: str) -> list[Finding]:
    """What the allowlist SAYS, rather than that the field was there.

    `EM3C-EVIDENCE-0003`: the allowlist was captured and never read. A list nobody looks at
    cannot contradict anything, so a policy listing hosts under a mode that reaches nothing, or
    listing a host in a spelling the digest was never taken over, produced no finding at all.
    """
    problems: list[Finding] = []
    mode = str(binding.get("mode") or "").strip().lower()
    if mode not in _MODES:
        problems.append(Finding(
            where, EVIDENCE_ERROR,
            "the binding names no network mode this rule knows: "
            f"{str(binding.get('mode'))[:24]!r}"))

    listed = binding.get("allowlist")
    if not isinstance(listed, list):
        return problems + [Finding(where, EVIDENCE_ERROR, "the binding records no allowlist")]

    hosts = [str(host) for host in listed]
    if any(not host.strip() for host in hosts):
        problems.append(Finding(
            where, FAIL,
            "the allowlist contains an empty entry, which names no host and can never be matched"))
    unnormalised = [host for host in hosts if host != host.strip().lower()]
    if unnormalised:
        problems.append(Finding(
            where, FAIL,
            "the allowlist is not in the form the policy digest is taken over: "
            f"{unnormalised[0]!r}. Two spellings of one host digest differently, so this list "
            "and that digest are not about the same policy"))
    if len(set(hosts)) != len(hosts):
        problems.append(Finding(
            where, FAIL,
            "the allowlist names a host twice, so it is not the canonical set the digest covers"))
    if hosts != sorted(hosts):
        problems.append(Finding(
            where, FAIL,
            "the allowlist is not in canonical order, so it is not the list the digest covers"))

    if mode == "restricted" and not hosts:
        problems.append(Finding(
            where, FAIL,
            "the policy restricts egress to a list of hosts and the list is empty. The mode and "
            "the list describe two different policies, and only one of them can be in force"))
    if mode in ("none", "unrestricted") and hosts:
        problems.append(Finding(
            where, FAIL,
            f"the mode is {mode}, under which a per-host list decides nothing, and "
            f"{len(hosts)} host(s) are listed. One of the two is not the policy in force"))
    return problems


def _containment_findings(effective: dict, binding: dict, where: str) -> list[Finding]:
    """Whether what one job was granted is inside what the machine allows.

    The two digests cannot be compared for equality: a job digest is taken over what that job
    asked for and was granted, an operator digest over what this machine permits anyone at all.
    They are different documents and will never match, so an equality check between them would
    either always fail or, written defensively, always pass. What binds them is containment.
    """
    problems: list[Finding] = []
    mode = str(binding.get("mode") or "").strip().lower()
    allowed = [str(host) for host in (binding.get("allowlist") or ())]

    # Both keys are required by the shape, and a policy map that lacks either is refused when
    # the record is READ. There is no guard here for that: a branch no input can reach reads as
    # cover for a case that is handled, and this one is handled earlier and harder.
    enabled = effective["network.enabled"]
    granted = effective["network.allowed_destinations"]

    if mode == "none":
        if enabled:
            problems.append(Finding(
                where, FAIL,
                "this job was granted network under a policy that reaches nothing. Either the job "
                "ran outside the policy or this capture is not of the policy it ran under"))
    elif mode == "restricted":
        if granted is None:
            problems.append(Finding(
                where, FAIL,
                "this job was granted every destination under a policy that permits a named list, "
                "which is wider than what the machine allows"))
        else:
            outside = sorted({str(host) for host in granted} - set(allowed))
            if outside:
                problems.append(Finding(
                    where, FAIL,
                    f"this job was granted {', '.join(outside)}, which the policy in force does "
                    "not permit"))
    return problems


def verify_jobs_under_policy(steps) -> list[Finding]:
    """Whether every job that ran was inside the operator policy in force when it ran.

    `EM3C-EVIDENCE-0003`: the binding was checked for its own consistency, the jobs were checked
    against the gateway's own answer, and nothing held the two together. A binding that was
    stale, or about another machine entirely, could sit in the same record as a job that ran with
    network the policy forbade, and no rule looked across.

    The policy in force for a step is the last one captured before it. A job with no binding
    before it is an evidence error rather than a pass: nothing in the record then says what the
    machine allowed at the time.
    """
    problems: list[Finding] = []
    in_force: dict | None = None
    for index, raw in enumerate(steps):
        binding = raw.get("binding")
        if isinstance(binding, dict):
            in_force = binding
            continue
        record = raw.get("gateway_record")
        if not isinstance(record, dict):
            continue
        where = f"step {index + 1} ({raw.get('name') or 'unnamed'})"
        effective = record.get("effective_policy")
        if not isinstance(effective, dict):
            if str(record.get("effective_policy_sha256") or "").strip():
                problems.append(Finding(
                    where, EVIDENCE_ERROR,
                    "the record carries a digest of the policy this job ran under and not the "
                    "policy itself, so what that digest is over was never held against what the "
                    "machine allows"))
            continue
        if in_force is None:
            problems.append(Finding(
                where, EVIDENCE_ERROR,
                "a job ran and no operator policy was captured before it, so nothing here says "
                "what this machine allowed at the time"))
            continue
        problems.extend(_containment_findings(effective, in_force, where))
    return problems


def verify_bindings(steps) -> list[Finding]:
    """Whether the operator-policy binding was captured, and stayed the same across the run.

    `EM3C-EVIDENCE-0001`: the snapshots were recorded and never checked, so a missing, stale or
    divergent binding produced no finding at all. A binding nobody reads is a field, not a rule.
    """
    problems: list[Finding] = []
    seen: list[dict] = []
    for index, raw in enumerate(steps):
        binding = raw.get("binding")
        if not isinstance(binding, dict):
            continue
        where = f"step {index + 1} ({raw.get('name') or 'unnamed'})"
        for field_name in ("generation", "policy_digest", "configured_digest", "runtime",
                           "backend", "conformance_digest"):
            if not str(binding.get(field_name) or "").strip():
                problems.append(Finding(where, EVIDENCE_ERROR,
                                        f"the binding records no {field_name}"))
        for field_name in ("policy_digest", "configured_digest", "conformance_digest"):
            value = binding.get(field_name)
            if value and not _looks_like_a_digest(value):
                problems.append(Finding(where, FAIL,
                                        f"{field_name} is not a digest: {str(value)[:24]!r}"))
        if str(binding.get("digests_agree")) != "True":
            problems.append(Finding(
                where, FAIL,
                "what is configured and what is in force do not agree, so nothing under this "
                "policy was running as configured"))
        if not binding.get("required_properties"):
            problems.append(Finding(where, EVIDENCE_ERROR,
                                    "the binding records no required-property set"))
        problems.extend(_allowlist_findings(binding, where))
        seen.append({"where": where, **binding})

    if not seen:
        return [Finding("policy binding", EVIDENCE_ERROR,
                        "no operator-policy binding was captured, so nothing here says which "
                        "policy the run happened under")]
    if len(seen) < 2:
        problems.append(Finding("policy binding", EVIDENCE_ERROR,
                                "the binding was captured once, so it was never compared before "
                                "and after"))
        return problems

    # Counting the captures says nothing. What matters is whether the LAST one still describes
    # the policy the run finished under, and whether every change between them was a change the
    # record accounts for. `EM3C-EVIDENCE-0002`: two identical-looking captures were being
    # accepted without either being compared to the other.
    first, last = seen[0], seen[-1]
    changed = [k for k in ("policy_digest", "generation")
               if str(first.get(k)) != str(last.get(k))]
    if changed:
        # A change is legitimate only when the record contains a capture that announced it: the
        # generation must move forward, never back, and the digest must move with it.
        try:
            before_generation = int(str(first.get("generation") or 0))
            after_generation = int(str(last.get("generation") or 0))
        except ValueError:
            return problems + [Finding("policy binding", FAIL,
                                       "a generation is not a number, so no comparison is possible")]
        if after_generation < before_generation:
            problems.append(Finding(
                "policy binding", FAIL,
                f"the generation went backwards, {before_generation} to {after_generation}: the "
                "run finished under an older activation than it started with"))
        elif after_generation == before_generation and "policy_digest" in changed:
            problems.append(Finding(
                "policy binding", FAIL,
                "the policy digest changed while the generation did not, so a policy was "
                "substituted without an activation"))
    else:
        for key in ("mode", "allowlist", "configured_digest", "required_properties", "runtime",
                    "backend", "conformance_digest"):
            if str(first.get(key)) != str(last.get(key)):
                problems.append(Finding(
                    "policy binding", FAIL,
                    f"{key} changed while the policy digest and generation did not, so the "
                    "binding does not describe one state"))
    return problems


def verify(steps, secrets=()) -> list[Finding]:
    """Every way this evidence fails to establish what it claims. Empty means it holds."""
    problems: list[Finding] = []

    for index, raw in enumerate(steps):
        try:
            step = parse_step(dict(raw))
        except EvidenceError as exc:
            problems.append(Finding(f"step {index + 1}", EVIDENCE_ERROR, str(exc)))
            continue
        where = f"step {index + 1} ({step.get('name') or 'unnamed'})"

        # What ran, who ran it, and when. `EM3C-EVIDENCE-0009`: these four identify the step and
        # were read by nothing, so a record could carry an unnamed step with no command, run by
        # nobody, in negative time, and every other rule would still have its say about it.
        if not str(step.get("name") or "").strip():
            problems.append(Finding(
                where, EVIDENCE_ERROR,
                "this step does not say what it is, so nothing it contains can be read as "
                "evidence of anything in particular"))
        if str(step.get("role") or "") not in _ROLES:
            problems.append(Finding(
                where, EVIDENCE_ERROR,
                f"this step was run by {str(step.get('role'))[:24]!r}, which is neither of the "
                "two machines this record is about"))
        if not (step.get("argv") or []):
            problems.append(Finding(
                where, EVIDENCE_ERROR,
                "this step records no command, so what it observed cannot be attributed to "
                "anything that ran"))
        began, finished = step.get("started_at"), step.get("ended_at")
        if isinstance(began, (int, float)) and isinstance(finished, (int, float)) \
                and finished < began:
            problems.append(Finding(
                where, FAIL,
                f"this step ended before it started ({began} to {finished}), so its times are "
                "not a record of when anything happened"))

        expected = step.get("expected_exit", None)
        actual = step.get("exit_code")
        if step.get("error_class") and actual == 0:
            problems.append(Finding(
                where, FAIL,
                f"this step reports {step['error_class']} and an exit code of 0. A command that "
                "could not run has no successful status"))
        if actual is None:
            problems.append(Finding(
                where, EVIDENCE_ERROR,
                "no exit code was captured"
                + (f" ({step['error_class']})" if step.get("error_class") else "")
                + ", so whether the command succeeded is unknown"))
        elif expected is not None and actual != expected:
            problems.append(Finding(where, FAIL, f"exited {actual}, and {expected} was required"))

        if step.get("expect_output") and not (step.get("stdout") or "").strip():
            problems.append(Finding(
                where, EVIDENCE_ERROR,
                "stdout is empty. An empty capture and a check that never ran look the same, so it "
                "is not accepted as either"))

        wanted = (step.get("expected_refusal") or "").strip()
        if wanted:
            record = step.get("gateway_record") or {}
            seen = (step.get("stdout") or "") + (step.get("stderr") or "") \
                + str(record.get("refusal") or "")
            if wanted.lower() not in seen.lower():
                problems.append(Finding(
                    where, FAIL,
                    f"was supposed to be refused because {wanted!r}, and that reason does not "
                    "appear. A refusal for another reason is not this test passing"))

        problems.extend(_run_findings(step, where))
        problems.extend(_container_findings(step, where))

        container = (step.get("container") or "").strip()
        run_id = (step.get("run_id") or "").strip()
        if container and run_id:
            short = run_id[:12]
            if short and short not in container:
                problems.append(Finding(
                    where, FAIL,
                    f"container {container!r} does not carry run {short}, so it is not shown to be "
                    "this run's"))

        blob = json.dumps(step, ensure_ascii=False, sort_keys=True)
        for secret in secrets or ():
            if secret and len(str(secret)) >= 8 and str(secret) in blob:
                problems.append(Finding(where, FAIL,
                                        "a live secret value was written into the evidence"))

    problems.extend(verify_answers(steps))
    problems.extend(verify_one_client(steps))
    problems.extend(verify_two_machines(steps))
    problems.extend(verify_bindings(steps))
    problems.extend(verify_jobs_under_policy(steps))
    return problems


#: Where a sentinel travelled. Two different names are required for a crossing: a value carried
#: and confirmed over the same channel has only been seen by one path.
#: The two machines a record is about. A step run by anything else is not part of this evidence.
_ROLES = ("client", "gateway")

_CHANNELS = ("agentnode-job", "ssh")


def _sentinel_findings(sentinel: dict, crossed: dict) -> list[Finding]:
    """Whether one sentinel establishes a crossing, from what is recorded rather than from what
    it says about itself.

    `generated_on` is a label, and a label is not provenance. What makes the origin checkable is
    `in_request`: whether the value appeared in what the CLIENT sent. A value the client sent and
    the gateway echoed came from the client. A value the client never sent, which came back in the
    response and is also present on the gateway host, did not -- the client could not have
    produced it. So the origin is derived here, and a record whose label disagrees with its own
    fields is refused rather than believed.

    ## What this is, and what it is not

    This is OBSERVED provenance. It establishes that a value moved between two machines over two
    separate channels, and that the record's own fields agree about which direction it moved. It
    is NOT remote attestation: nothing here is signed by the far machine, and nothing proves the
    far machine is the hardware or image it claims to be. An operator who controls both ends could
    produce a record that satisfies every rule below.

    That is adequate for a gateway whose operator runs both machines and is checking their
    separation, which is what these runs do. It would not be adequate for a managed service
    accepting a stranger's claim about their own sandbox -- see `docs/managed-sandbox-binding.md`,
    which records what such a service would additionally need.
    """
    where = "two machines"
    made = str(sentinel.get("generated_on") or "")
    carried = str(sentinel.get("carried_over") or "")
    confirmed = str(sentinel.get("confirmed_over") or "")

    if made not in ("client", "gateway"):
        return [Finding(where, EVIDENCE_ERROR, "a sentinel does not say where it was made")]
    if carried not in _CHANNELS or confirmed not in _CHANNELS:
        return [Finding(where, EVIDENCE_ERROR,
                        f"a sentinel does not name two known channels (carried over {carried!r}, "
                        f"confirmed over {confirmed!r})")]
    if carried == confirmed:
        return [Finding(where, FAIL,
                        f"a sentinel was carried and confirmed over the same channel ({carried}), "
                        "so only one path ever saw it")]

    for field_name in ("in_request", "in_response", "in_other_channel"):
        if not isinstance(sentinel.get(field_name), bool):
            return [Finding(where, EVIDENCE_ERROR,
                            f"a sentinel does not record {field_name}, so its origin rests on "
                            "what it calls itself")]

    if sentinel.get("in_response") is not True:
        return [Finding(where, FAIL,
                        f"the sentinel said to be made on {made} never came back over {carried}")]
    if sentinel.get("in_other_channel") is not True:
        return [Finding(where, FAIL,
                        f"the sentinel said to be made on {made} was not found over {confirmed}, "
                        "so only one channel ever saw it")]

    # `in_request` decided the origin and was, until now, a boolean the recorder asserted --
    # so the derivation was only as good as the recorder's own bookkeeping. The request itself
    # is in the record, and the claim is checked against it: the value is in what the client
    # sent, or it is not, and no field gets to say otherwise (`EM3C-EVIDENCE-0009`).
    sent = str(sentinel.get("request_text") or "")
    value = str(sentinel.get("value_sha256") or "")
    if not _looks_like_a_digest(value):
        return [Finding(where, EVIDENCE_ERROR,
                        f"a sentinel's value is not a digest: {value[:24]!r}")]
    if not sent.strip():
        return [Finding(where, EVIDENCE_ERROR,
                        "a sentinel records nothing of what the client sent, so whether the "
                        "value was in it cannot be checked and its origin rests on a label")]
    really_in_request = any(
        hashlib.sha256(token.encode("utf-8")).hexdigest() == value
        for token in re.findall(r"[0-9a-zA-Z_-]{8,}", sent))
    if bool(sentinel.get("in_request")) != really_in_request:
        return [Finding(
            where, FAIL,
            "a sentinel says the value was "
            + ("in" if sentinel.get("in_request") else "not in")
            + " what the client sent, and the recorded request says the opposite. The origin of "
            "the crossing is what that field decides, so it is read from the request rather "
            "than believed")]

    # The origin, derived. A value the client sent is the client's; one it never sent is not.
    derived = "client" if really_in_request else "gateway"
    if derived != made:
        return [Finding(where, FAIL,
                        f"a sentinel calls itself made on {made}, but it was "
                        f"{'in' if sentinel.get('in_request') else 'not in'} what the client sent, "
                        f"which makes it {derived}'s. The label is not evidence")]

    crossed[made] = str(sentinel.get("value_sha256") or "")
    return []


def verify_answers(steps) -> list[Finding]:
    """Whether each recorded answer is the answer it says it is.

    `EM3C-E3-CLASSIFY-0001` asked for the outside of an answer to be tied to its inside. The tie
    is not checked here by hand: the gateway signs a binding over a named tuple of the answer's
    own fields, and this recomputes that binding with the SAME function the gateway used. Change
    the run id, the job id, the artifact digest, either policy digest or the result, or leave any
    of them out, and the recomputed binding stops matching the recorded one.

    What this cannot do is check the signature: that needs the paired client's secret, which is
    not in the record and must not be. The client checked it at the moment it received the
    answer, using the production verifier, and recorded whether that verifier accepted -- as an
    observation, beside the answer rather than inside it. A record that carries a signed answer
    the client's own verification did not accept is refused here.
    """
    problems: list[Finding] = []
    gateways: set[str] = set()
    for index, raw in enumerate(steps):
        record = raw.get("gateway_record")
        if not isinstance(record, dict):
            continue
        where = f"step {index + 1} ({raw.get('name') or 'unnamed'})"
        production = _production()

        for field_name in production["stamp"]:
            if field_name not in record:
                problems.append(Finding(
                    where, EVIDENCE_ERROR,
                    f"the answer carries no {field_name}, so it is not an answer this gateway "
                    "stamped and nothing ties it to the build that produced it"))
        if record.get("protocol") not in (None, production["protocol"]):
            problems.append(Finding(
                where, FAIL,
                f"the answer says it speaks {str(record.get('protocol'))[:24]!r} and this build "
                f"speaks {production['protocol']}. Two protocols are not one conversation"))

        said = record.get("gateway")
        if isinstance(said, dict):
            expected_print = hashlib.sha256(
                f"{said.get('gateway_id', '')}\n{said.get('version', '')}".encode()).hexdigest()
            if str(record.get("fingerprint") or "") != expected_print:
                problems.append(Finding(
                    where, FAIL,
                    "the fingerprint is not the one this gateway identity and version produce, "
                    "so the stamp and what it stamps disagree"))
            if said.get("gateway_id"):
                gateways.add(str(said["gateway_id"]))

        binding = record.get("binding")
        observed = raw.get("answer")
        if binding is None:
            # Only an answer that says there is nothing to sign may be unsigned.
            if not str(record.get("error") or "").strip():
                problems.append(Finding(
                    where, FAIL,
                    "this answer is about a run and carries no binding, so nothing in it shows "
                    "it came from the gateway this client paired with"))
            continue

        expected = production["response_binding"](
            gateway_id=binding.get("gateway_id", ""), version=binding.get("version", ""),
            job_id=record.get("job_id", ""), run_id=record.get("run_id", ""),
            artifact_sha256=record.get("artifact_sha256", ""),
            request_policy_sha256=record.get("request_policy_sha256", ""),
            effective_policy_sha256=record.get("effective_policy_sha256", ""),
            result=record.get("stdout", ""))
        if expected != binding:
            differing = sorted(k for k in set(expected) | set(binding)
                               if expected.get(k) != binding.get(k))
            problems.append(Finding(
                where, FAIL,
                "the answer's own fields do not produce the binding it carries; they disagree "
                "about " + ", ".join(differing) + ". Either the answer was altered after it was "
                "signed or the binding belongs to another answer"))

        if isinstance(said, dict):
            for key in ("gateway_id", "version"):
                if str(said.get(key, "")) != str(binding.get(key, "")):
                    problems.append(Finding(
                        where, FAIL,
                        f"the stamp says the {key} is {str(said.get(key))[:24]!r} and the signed "
                        f"binding says {str(binding.get(key))[:24]!r}"))

        # The signature cannot be checked here, so what is checked is that NOTHING in the answer
        # changed after the client checked it. The client sealed the whole answer, canonically,
        # at the moment its verification accepted it; recomputing that seal over what is in the
        # record catches a signature exchanged afterwards, which recomputing the binding cannot
        # (`EM3C-EVIDENCE-0013`).
        sealed = str(observed.get("verified_sha256") or "") if isinstance(observed, dict) else ""
        if isinstance(observed, dict) and observed.get("verified") is True:
            if not sealed:
                problems.append(Finding(
                    where, EVIDENCE_ERROR,
                    "the client accepted this answer and recorded nothing that would show it is "
                    "still the answer it accepted"))
            elif sealed != production["seal"](record):
                problems.append(Finding(
                    where, FAIL,
                    "this is not the answer the client verified: something in it changed after "
                    "it was accepted"))

        if not str(record.get("signature") or "").strip():
            problems.append(Finding(
                where, EVIDENCE_ERROR,
                "the answer carries a binding and no signature, so the binding is a claim rather "
                "than a proof"))

        if not isinstance(observed, dict):
            problems.append(Finding(
                where, EVIDENCE_ERROR,
                "a signed answer was recorded and nothing says whether the client's own "
                "verification accepted it"))
        elif observed.get("verified") is not True:
            problems.append(Finding(
                where, FAIL,
                "the client's own verification did not accept this answer"
                + (": " + str(observed.get("refusal"))[:120] if observed.get("refusal") else "")
                + ". An answer that failed verification is not evidence of what it says"))

    if len(gateways) > 1:
        problems.append(Finding(
            "one gateway", FAIL,
            "the answers in this record come from more than one gateway (" +
            ", ".join(sorted(g[:12] for g in gateways)) + "), so they are not one run"))
    return problems


def verify_one_client(steps) -> list[Finding]:
    """Whether one client made this record.

    `EM3C-EVIDENCE-0009`: `client_id` was recorded and read by nothing. A record that names two
    clients is not one run seen from one side; it is two runs, or one run and something else, and
    the two-machine argument -- which rests on what "the client" sent -- does not hold across it.
    """
    named = {str(raw.get("client_id") or "").strip() for raw in steps}
    named.discard("")
    if len(named) > 1:
        return [Finding("one client", FAIL,
                        "this record names more than one client (" + ", ".join(sorted(named))
                        + "), so it is not one run seen from one side")]
    return []


def verify_two_machines(steps) -> list[Finding]:
    """Whether the record shows two different hosts rather than one machine describing itself.

    `EM3C-E2-CLASSIFY-0001`: a local `ls` that failed proves only that a local `ls` failed.
    Separation is established here by each machine describing itself over its own channel, and by
    a random value made on one being read back over the other -- so neither side's claim rests on
    its own account.
    """
    problems: list[Finding] = []
    machines: dict[str, dict] = {}
    for index, raw in enumerate(steps):
        machine = raw.get("machine")
        if not isinstance(machine, dict) or not machine.get("role"):
            continue
        where = f"step {index + 1} ({raw.get('name') or 'unnamed'})"
        claimed = str(machine["role"])
        channel = str(raw.get("role") or "")

        # `EM3C-EVIDENCE-0011`: the identity was indexed by the label inside it, and nothing
        # required that label to match the channel the step was recorded over. So both machines
        # could describe themselves over ONE channel -- the whole point of asking each to speak
        # for itself -- and the record would still show two identities. A machine describes
        # itself over its own channel or it has not described itself.
        if claimed != channel:
            problems.append(Finding(
                where, FAIL,
                f"this identity says it is the {claimed} and it was recorded over the "
                f"{channel or 'unnamed'} channel. An identity carried on the other machine's "
                "channel is that machine's account of its neighbour, not the neighbour's own"))
            continue

        # And two identities under one name is not two machines. Taking the last silently made
        # a later, weaker or wrong identity replace an earlier one with no finding at all.
        if claimed in machines:
            problems.append(Finding(
                where, FAIL,
                f"a second identity claims to be the {claimed}. Two accounts of one machine are "
                "not two machines, and nothing here says which of them is this run's"))
            continue
        machines[claimed] = machine

    if len(machines) < 2:
        # `problems +`, not instead of: a record where an identity arrived on the wrong channel
        # would otherwise report only that two machines are missing, and the reason they are
        # missing is the finding worth having.
        return problems + [Finding(
            "two machines", EVIDENCE_ERROR,
            "fewer than two machines described themselves over their own channels, so nothing "
            "here shows the client and the gateway are different hosts")]

    roles = sorted(machines)
    first, second = machines[roles[0]], machines[roles[1]]
    for key, what in (("host_sha256", "host identity"),
                      ("filesystem_sha256", "filesystem identity")):
        one, two = str(first.get(key) or ""), str(second.get(key) or "")
        if DIGEST_OF_NOTHING in (one, two):
            # The digest of an empty string. A machine that could not read its own identity and
            # hashed the nothing it got produces a value that looks exactly like a real one, and
            # two machines that both failed differently can even look distinct.
            problems.append(Finding(
                "two machines", EVIDENCE_ERROR,
                f"a machine's {what} is the digest of an empty value, so it reports the hash of "
                "having found nothing rather than an identity"))
        elif not one or not two:
            problems.append(Finding("two machines", EVIDENCE_ERROR,
                                    f"one of the machines did not report its {what}"))
        elif one == two:
            problems.append(Finding("two machines", FAIL,
                                    f"both machines report the same {what}, so they are one"))
    # The operating system is recorded because a reader wants it, NOT as a discriminator: two
    # distinct hosts may perfectly well run the same one, and failing on that would be a rule
    # about a coincidence. `EM3C-EVIDENCE-0001` was right to call that out. What separates them
    # is the host and filesystem identities above.
    for machine in (first, second):
        if not str(machine.get("os") or ""):
            problems.append(Finding("two machines", EVIDENCE_ERROR,
                                    "a machine did not report its operating system"))
        commands = machine.get("commands")
        if not isinstance(commands, list) or not commands:
            problems.append(Finding(
                "two machines", EVIDENCE_ERROR,
                f"the {machine.get('role')} identity carries no record of the commands that "
                "produced it, so it cannot be told apart from an assertion"))
            continue
        for entry in commands:
            if not isinstance(entry, dict):
                problems.append(Finding("two machines", EVIDENCE_ERROR,
                                        "an identity command is not a record"))
                continue
            missing = [k for k in ("command", "exit_code", "stdout", "stderr")
                       if k not in entry]
            if missing:
                problems.append(Finding(
                    "two machines", EVIDENCE_ERROR,
                    f"an identity command on the {machine.get('role')} does not record "
                    + ", ".join(missing)))
            elif entry.get("exit_code") != 0:
                problems.append(Finding(
                    "two machines", EVIDENCE_ERROR,
                    f"an identity command on the {machine.get('role')} exited "
                    f"{entry.get('exit_code')!r}, so what it reported was never established"))

    crossed: dict[str, str] = {}
    for raw in steps:
        sentinel = raw.get("sentinel")
        if not isinstance(sentinel, dict):
            continue
        problems.extend(_sentinel_findings(sentinel, crossed))

    if len(crossed) < 2:
        problems.append(Finding("two machines", EVIDENCE_ERROR,
                                "a sentinel was not carried in both directions, so only one side "
                                "was ever confirmed by the other"))
    elif len(set(crossed.values())) < 2:
        problems.append(Finding("two machines", FAIL,
                                "both sentinels carried the same value, so they cannot have been "
                                "generated independently"))
    return problems


def check_file(path, secrets=()) -> list[Finding]:
    return verify(load(path), secrets)


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Check an external-run evidence file.")
    parser.add_argument("path")
    parser.add_argument("--secret", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        problems = check_file(args.path, args.secret)
    except EvidenceError as exc:
        print(f"  {exc}")
        return 2
    if problems:
        failures = [p for p in problems if p.kind == FAIL]
        errors = [p for p in problems if p.kind == EVIDENCE_ERROR]
        print(f"  This evidence does not establish what it claims "
              f"({len(failures)} failed, {len(errors)} could not be evaluated):")
        for problem in problems:
            print(f"    {problem}")
        return 1
    print("  Every step is complete, self-consistent and about the run it names.")
    return 0


if __name__ == "__main__":                                        # pragma: no cover
    raise SystemExit(main())
