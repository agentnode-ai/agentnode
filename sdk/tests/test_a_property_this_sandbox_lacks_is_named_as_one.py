"""A job that requires a property this sandbox does not provide is told so, by a word of its own.

`required_properties` is a declared field of `submit`: "what the sandbox must actually enforce, or the
job is refused rather than run with less". When this sandbox cannot show one of them, `admit()` refused
-- rightly, nothing ran -- with `ProtocolError`, which every door renders as `malformed`: "Correct the
request and send it again." The request was never wrong. Observation `O1` of the F12 arc.

NO EXISTING WORD IS TRUE OF IT. `sandbox_unavailable` means a sandbox that cannot run anything right now:
REST says 503, the console says try later, and a retry here meets the same answer. `refused_by_policy`
is an operator's decision, and this is a capability. So the contract gains `sandbox_incompatible`, with
REST 422, and the cause beneath it says which situation it is: `property_not_provided` when the sandbox
MEASURED the property and it did not hold, `property_unmeasured` when nothing established it either way
(`o1-a-property-this-sandbox-lacks/writing/DECISION-0001`, after the consultation `Q0001`, which
disagreed with my first leaning and said the closed list exists for exactly this).

WHAT EACH TEST GOES RED WITH on the unrepaired build, predeclared before the run:

* `test_1` to `test_7` -- "was named 'malformed'"
* `test_8` -- "has no sentence for"

`test_9` to `test_11` must be GREEN BEFORE AND AFTER: the sentence a person reads is unchanged, nothing
runs or is claimed, and a job requiring only what this sandbox provides is admitted.
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import io
import json
import os
import re
import urllib.error
import urllib.request

from agentnode_sdk.access import contract, dispatch, rest, schemas
from agentnode_sdk.gateway import client as gc
from agentnode_sdk.gateway.readiness import PROPERTY_CHECKS
from agentnode_sdk.gateway.server import name_the_refusal
from tests import consent
from tests.test_em3c_gateway import _granted, _paired, _store_measurement
from tests.test_every_door import rpc, sandbox  # noqa: F401
from tests.test_only_narrowing import _asking_for, gateway  # noqa: F401

INCOMPATIBLE = "sandbox_incompatible"
NOT_PROVIDED = "property_not_provided"
UNMEASURED = "property_unmeasured"
#: A property this sandbox MEASURED and found not to hold. Its one check is measured and failed; every
#: other check passes, so the gateway itself stays ready -- a sandbox that is fine, and lacks this.
REFUTED = "egress_allowlist"
#: A name nothing measures at all.
NEVER = "a-property-nobody-has-measured"


def _measured_without_the_allowlist(service):
    every = {c for ids in PROPERTY_CHECKS.values() for c in ids}
    _store_measurement(service, only=every - set(PROPERTY_CHECKS[REFUTED]))


def _admit_requiring(service, *properties):
    """A real admission of a job that requires these properties. The refusal, or None."""
    from agentnode_sdk.gateway.policy_paths import policy_shape
    from agentnode_sdk.gateway.protocol import canonical_bytes, digest

    code = b"print(1)\n"
    request = _asking_for(required_properties=tuple(properties), wall_clock_s=30,
                          artifact_sha256=hashlib.sha256(code).hexdigest())
    request = dataclasses.replace(request, policy_sha256=digest(canonical_bytes(
        policy_shape(service.requested_policy(request)))))
    try:
        service.admit(request, code)
    except Exception as exc:                                    # noqa: BLE001
        return exc
    return None


def _refused(service, *properties):
    _measured_without_the_allowlist(service)
    refused = _admit_requiring(service, *properties)
    assert refused is not None, "a job requiring %r was admitted" % (properties,)
    return refused


class TestTheRecordAndTheDoorsNameIt:

    def test_1_the_record_names_it(self, gateway):
        word = name_the_refusal(_refused(gateway, REFUTED))[0]
        assert word == INCOMPATIBLE, (
            "a job requiring a property this sandbox lacks was named %r on the record" % word)

    def test_2_the_doors_name_it(self, gateway):
        word = dispatch._translate(_refused(gateway, REFUTED)).refusal
        assert word == INCOMPATIBLE, (
            "a job requiring a property this sandbox lacks was named %r by the doors" % word)

    def test_3_and_say_which_situation_it_is(self, gateway):
        measured_false = dispatch._translate(_refused(gateway, REFUTED))
        never = dispatch._translate(_refused(gateway, NEVER))
        both = dispatch._translate(_refused(gateway, REFUTED, NEVER))
        got = (measured_false.cause, never.cause, both.cause)
        assert got == (NOT_PROVIDED, UNMEASURED, NOT_PROVIDED), (
            "the refusal was named %r and its causes were %r" % (measured_false.refusal, got))

    def test_4_with_a_remedy_that_fits_each(self, gateway):
        measured_false = dispatch._translate(_refused(gateway, REFUTED))
        never = dispatch._translate(_refused(gateway, NEVER))
        for told in (measured_false, never):
            ok = ("a sandbox that provides" in told.what_to_do
                  and "only if the job can safely run without" in told.what_to_do
                  and not told.what_to_do.startswith(("Correct the request", "Try again")))
            assert ok, "the refusal was named %r and its remedy is %r" % (told.refusal,
                                                                          told.what_to_do)
        assert "will not help" in measured_false.what_to_do, (
            "the refusal was named %r and its remedy for a measured lack does not say a resend "
            "will not help: %r" % (measured_false.refusal, measured_false.what_to_do))
        assert "measure" in never.what_to_do, (
            "the refusal was named %r and its remedy for an unmeasured property does not mention "
            "measuring: %r" % (never.refusal, never.what_to_do))


CODE = b"print('hi')"
CODE_SHA = hashlib.sha256(CODE).hexdigest()


def _call(base, token, operation, params):
    request = urllib.request.Request(base + rest.NAMESPACE + operation,
                                     data=json.dumps(params).encode("utf-8"), method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header(rest.SPEAKS_HEADER, contract.PROTOCOL_VERSION)
    request.add_header(rest.TOKEN_HEADER, token)
    try:
        with urllib.request.urlopen(request, timeout=20) as answer:
            return answer.status, json.loads(answer.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as refused:
        return refused.code, json.loads(refused.read().decode("utf-8") or "{}")


JOB = {"command": ["python", "-c", "print('hi')"], "network": "none", "wall_clock_s": 30}


def _a_prepared_job(base, token, run_id):
    status, told = _call(base, token, "prepare",
                         dict(JOB, artifact_sha256=CODE_SHA, artifact_bytes=len(CODE)))
    assert status == 200, (status, told)
    return dict(JOB, run_id=run_id, artifact=base64.b64encode(CODE).decode("ascii"),
                accepted_disclosure=told["accepted_disclosure"],
                required_properties=[REFUTED])


class TestEveryDoorSaysTheSameWord:

    def test_5_the_rest_door(self, sandbox):
        service, base, token = sandbox
        _measured_without_the_allowlist(service)
        status, answer = _call(base, token, "submit", _a_prepared_job(base, token, "i" * 32))
        word = answer.get("refused")
        assert word == INCOMPATIBLE, (
            "the REST door: a job requiring a property this sandbox lacks was named %r (HTTP %s)"
            % (word, status))
        assert status == rest.how_it_should_answer(INCOMPATIBLE) == 422
        assert answer.get("cause") == NOT_PROVIDED, answer

    def test_6_the_mcp_door(self, sandbox):
        service, base, token = sandbox
        _measured_without_the_allowlist(service)
        status, called = rpc(base, token, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": schemas.tool_name_for("prepare"),
                       "arguments": dict(JOB, artifact_sha256=CODE_SHA,
                                         artifact_bytes=len(CODE))}})
        assert status == 200 and not called["result"].get("isError"), called
        job = dict(JOB, run_id="m" * 32, artifact=base64.b64encode(CODE).decode("ascii"),
                   accepted_disclosure=called["result"]["structuredContent"]["accepted_disclosure"],
                   required_properties=[REFUTED])
        status, called = rpc(base, token, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": schemas.tool_name_for("submit"), "arguments": job}})
        assert status == 200 and called["result"]["isError"] is True, called
        word = called["result"]["structuredContent"].get("refused")
        assert word == INCOMPATIBLE, (
            "the MCP door: a job requiring a property this sandbox lacks was named %r" % word)

    def test_7_the_older_signed_door(self, sandbox, monkeypatch):
        service, base, _token = sandbox
        _measured_without_the_allowlist(service)
        conn = _paired(base, service.state)
        seen = []
        real_post = gc._post

        def _keeping_the_status(*args, **kwargs):
            status, answer = real_post(*args, **kwargs)
            seen.append(status)
            return status, answer

        monkeypatch.setattr(gc, "_post", _keeping_the_status)
        answer = consent.submit(conn, CODE, wall_clock_s=30, required_properties=(REFUTED,),
                                granted=_granted(service, wall_clock_s=30))
        assert answer["state"] == "refused" and answer.get("signature"), answer
        word = answer.get("refused")
        assert word == INCOMPATIBLE, (
            "the older signed door: a job requiring a property this sandbox lacks was named %r"
            % word)
        assert seen[-1] == 409, "the older door's status for this refusal changed: %r" % seen


class TestTheConsoleHasASentence:

    def test_8_for_the_new_word(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with io.open(os.path.join(here, "agentnode_sdk", "console", "app.js"),
                     encoding="utf-8") as fh:
            app = fh.read()
        assert re.search(r"\b%s\s*:\s*\[" % INCOMPATIBLE, app), (
            "the console has no sentence for %r, so a person meeting it is shown a generic "
            "message" % INCOMPATIBLE)


class TestWhatMustNotChange:
    """CONTROLS. Green before and after."""

    def test_9_the_sentence_a_person_reads_is_unchanged(self, gateway):
        assert str(_refused(gateway, REFUTED)) == (
            "this gateway cannot provide egress_allowlist. The job was not started.")

    def test_10_nothing_ran_and_nothing_was_claimed(self, sandbox):
        service, base, token = sandbox
        _measured_without_the_allowlist(service)
        _call(base, token, "submit", _a_prepared_job(base, token, "n" * 32))
        record = service.runs.get("n" * 32)
        assert record is not None and record.state == "refused", record
        assert not service.ledger.knows_run("n" * 32), "a refused job was claimed in the ledger"
        assert not record.container_name, "a refused job created a sandbox"

    def test_11_a_job_requiring_only_what_holds_is_admitted(self, gateway):
        _measured_without_the_allowlist(gateway)
        assert _admit_requiring(gateway, "container_isolation") is None
