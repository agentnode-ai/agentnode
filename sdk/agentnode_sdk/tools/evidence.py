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

import json
import os
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
    notes: str = ""

    # -- what was expected, declared before the step ran ---------------------------------------
    expected_exit: int | None = None
    expected_refusal: str = ""
    expect_output: bool = False
    expect_cleanup: bool = False
    expect_container_gone: bool = False

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

#: Fields the verifier reads. Every other field must appear in `RECORDED_ONLY` below, so a field
#: added with no rule behind it is caught by a test rather than sitting unread for a release.
READ_BY_RULES = (
    "name", "role", "argv", "started_at", "ended_at", "exit_code", "stdout", "stderr",
    "error_class", "run_id", "gateway_record", "container", "container_query", "machine",
    "sentinel", "binding", "request_policy_sha256", "effective_policy_sha256",
    "expected_exit", "expected_refusal", "expect_output", "expect_cleanup",
    "expect_container_gone",
)

#: Fields kept for a reader and deliberately not used by any rule. Naming them is the point: an
#: unread field is a decision, not an oversight.
RECORDED_ONLY = ("job_id", "client_id", "policy_deltas", "cleanup_verified", "notes")

#: The shapes of the nested structures. `dict` alone says nothing about what is inside, and the
#: rules below read specific keys out of these.
_NESTED: dict[str, dict] = {
    "machine": {"required": ("role", "host_sha256", "filesystem_sha256", "os", "commands"),
                "optional": ()},
    "sentinel": {"required": ("generated_on", "carried_over", "confirmed_over", "value_sha256",
                              "in_request", "in_response", "in_other_channel", "matched"),
                 "optional": ()},
    "container_query": {"required": ("ran", "exit_code", "stdout", "stderr", "error_class",
                                     "parsed", "command"),
                        "optional": ("names", "ids", "sought_id", "complete")},
    "binding": {"required": ("generation", "policy_digest", "configured_digest", "digests_agree",
                             "required_properties", "allowlist", "runtime", "backend",
                             "conformance_digest"),
                "optional": ()},
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
    "notes": (str,),
    "expected_exit": (int, type(None)),
    "expected_refusal": (str,),
    "expect_output": (bool,), "expect_cleanup": (bool,), "expect_container_gone": (bool,),
}

FIELD_NAMES = tuple(f.name for f in fields(Step))

if set(FIELD_NAMES) != set(_TYPES):                               # pragma: no cover - a guard
    raise RuntimeError("the schema and its declared types have drifted apart")


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


class Recorder:
    """Runs commands and writes down what happened, without judging any of it."""

    def __init__(self, path, role: str, secrets=()) -> None:
        self.path = str(path)
        self.role = role
        self.secrets = list(secrets)
        self.steps: list[Step] = []

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

        return self.record(Step(name=name, role=self.role, argv=safe,
                                started_at=started, ended_at=time.time(),
                                exit_code=code, stdout=out, stderr=err,
                                error_class=error_class, **extra))

    def record(self, step: Step) -> Step:
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
    if not isinstance(query.get("stdout"), str) or not isinstance(query.get("stderr"), str):
        return [Finding(where, EVIDENCE_ERROR, "the query's two streams were not both captured")]
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
    if not sought_name and not sought_id:
        return [Finding(where, EVIDENCE_ERROR,
                        "nothing was named as the container to look for, so its absence means "
                        "nothing")]

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

        expected = step.get("expected_exit", None)
        actual = step.get("exit_code")
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

    problems.extend(verify_two_machines(steps))
    problems.extend(verify_bindings(steps))
    return problems


#: Where a sentinel travelled. Two different names are required for a crossing: a value carried
#: and confirmed over the same channel has only been seen by one path.
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

    # The origin, derived. A value the client sent is the client's; one it never sent is not.
    derived = "client" if sentinel.get("in_request") else "gateway"
    if derived != made:
        return [Finding(where, FAIL,
                        f"a sentinel calls itself made on {made}, but it was "
                        f"{'in' if sentinel.get('in_request') else 'not in'} what the client sent, "
                        f"which makes it {derived}'s. The label is not evidence")]

    crossed[made] = str(sentinel.get("value_sha256") or "")
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
    for raw in steps:
        machine = raw.get("machine")
        if isinstance(machine, dict) and machine.get("role"):
            machines[str(machine["role"])] = machine

    if len(machines) < 2:
        return [Finding("two machines", EVIDENCE_ERROR,
                        "fewer than two machines described themselves, so nothing here shows the "
                        "client and the gateway are different hosts")]

    roles = sorted(machines)
    first, second = machines[roles[0]], machines[roles[1]]
    for key, what in (("host_sha256", "host identity"),
                      ("filesystem_sha256", "filesystem identity")):
        one, two = str(first.get(key) or ""), str(second.get(key) or "")
        if not one or not two:
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
