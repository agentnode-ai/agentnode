"""A well-formed job that the operator's policy refuses is named as a policy refusal, everywhere.

WHAT WAS MEASURED. Finding `F12` of the fourth beta acceptance: a job naming only destinations the
operator does not allow was refused -- correctly, nothing ran -- and recorded as `malformed`, the same
word a garbage request gets (`beta-readiness-r5/evidence/E0145`: `submit malformed about=network`).
A client branching on the closed list of refusals was told "fix your request" when the only party who
could change anything was the operator.

THE WORD ALREADY EXISTED. `contract.REFUSALS` declares `refused_by_policy` -- "the operator's policy
refused this job" -- and `submit` lists it; `admission.REASONS` has `refused_by_operator_policy` mapped
onto it; REST answers it with 403; the console has a label for it. Nothing raised it. `admit()` refused
every case with `ProtocolError`, and both translators turn that into `malformed`. So this is not a new
word: it is the declared one being used (`f12-policy-refusal/writing/DECISION-0001`).

TWO CASES, ONE CLASS. All the job's destinations removed by the operator's policy; and a path the job
declared mandatory that the operator's policy must narrow. Both are well-formed requests the operator
refuses.

AND THE CONTROLS. The refusals that ARE about a malformed request keep `malformed`, each with a test,
so the repair cannot pass by renaming everything.

Every discriminating assertion says `was named %r`, so on the unrepaired code each fails with
`was named 'malformed'` -- the phrase declared in `frozen/f12-profile.json` before this file ran.
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import io
import json
import os
import urllib.error
import urllib.request

import pytest

from agentnode_sdk.access import client as adapter
from agentnode_sdk.access import contract, dispatch, rest, schemas
from agentnode_sdk.gateway import admission
from agentnode_sdk.gateway import client as gc
from agentnode_sdk.gateway.server import name_the_refusal
from agentnode_sdk.sandbox.contract import NetworkRules, Retention, SandboxPolicy
from tests import consent
from tests.test_em3c_gateway import _granted, _paired, _store_measurement
from tests.test_every_door import rpc, sandbox  # noqa: F401
from tests.test_only_narrowing import _admit_asking, _asking_for, gateway  # noqa: F401

POLICY = "refused_by_policy"


def _an_operator_allowing(*hosts):
    return SandboxPolicy(
        network=NetworkRules(enabled=True, allowed_destinations=frozenset(hosts)),
        retention=Retention(),
    )


def _all_hosts_removed(gateway):
    """The F12 case: the job names a host, the operator allows a different one."""
    return _admit_asking(gateway, network="restricted", domains=("wanted.example",),
                         operator=_an_operator_allowing("allowed.example"))


def _mandatory_narrowed(gateway):
    """The same class: the job says the cpu limit is mandatory, the operator must narrow it."""
    return _admit_asking(gateway, mandatory=("limits.cpu",))


def _on_the_record(refused) -> str:
    return name_the_refusal(refused)[0]


def _at_the_doors(refused) -> str:
    return dispatch._translate(refused).refusal


# ------------------------------------------------------------------ the two policy cases


class TestAllDestinationsRemovedIsAPolicyRefusal:

    def test_1_the_record_names_it_a_policy_refusal(self, gateway):
        refused = _all_hosts_removed(gateway)
        assert refused is not None, "a job with no permitted destination was admitted"
        word = _on_the_record(refused)
        assert word == POLICY, (
            "a well-formed job the operator's policy refused was named %r on the record" % word)

    def test_2_the_doors_name_it_a_policy_refusal(self, gateway):
        refused = _all_hosts_removed(gateway)
        word = _at_the_doors(refused)
        assert word == POLICY, (
            "a well-formed job the operator's policy refused was named %r by the doors" % word)

    def test_3_it_is_the_typed_operator_policy_reason(self, gateway):
        refused = _all_hosts_removed(gateway)
        reason = getattr(refused, "reason", "")
        assert reason == "refused_by_operator_policy", (
            "the refusal was named %r and carries no operator-policy reason (%r)"
            % (_on_the_record(refused), reason))

    def test_4_the_sentence_a_person_reads_is_unchanged(self, gateway):
        """A CONTROL: green before and after. The repair renames; it does not reword."""
        refused = _all_hosts_removed(gateway)
        assert str(refused) == (
            "this job asked to reach wanted.example, and the policy in force here allows none of "
            "them, so there is nothing left to allow. This is not something the job can change: "
            "ask whoever runs this sandbox to allow one of those destinations, or ask for no "
            "network at all. Nothing was started.")

    def test_5_the_remedy_is_addressed_to_the_operator(self, gateway):
        refused = _all_hosts_removed(gateway)
        remedy = name_the_refusal(refused)[1]
        assert "whoever runs this sandbox" in remedy, (
            "the refusal was named %r and its remedy does not point at the operator: %r"
            % (_on_the_record(refused), remedy))


class TestAMandatoryPathTheOperatorNarrowsIsAPolicyRefusal:

    def test_6_the_record_names_it_a_policy_refusal(self, gateway):
        refused = _mandatory_narrowed(gateway)
        assert refused is not None, "a narrowed mandatory path was clamped rather than refused"
        word = _on_the_record(refused)
        assert word == POLICY, (
            "a mandatory path the operator's policy narrows was named %r on the record" % word)

    def test_7_the_doors_name_it_a_policy_refusal(self, gateway):
        word = _at_the_doors(_mandatory_narrowed(gateway))
        assert word == POLICY, (
            "a mandatory path the operator's policy narrows was named %r by the doors" % word)

    def test_8_it_now_has_a_remedy_naming_both_ways_out(self, gateway):
        refused = _mandatory_narrowed(gateway)
        remedy = name_the_refusal(refused)[1]
        assert "optional" in remedy and "whoever runs this sandbox" in remedy, (
            "the refusal was named %r and its remedy names neither way out: %r"
            % (_on_the_record(refused), remedy))

    def test_9_and_still_names_the_path_and_that_nothing_started(self, gateway):
        """A CONTROL, the assertions `test_only_narrowing` already makes."""
        said = str(_mandatory_narrowed(gateway))
        assert "limits.cpu" in said and "Nothing was started" in said


# ------------------------------------------------------------------ what stays malformed


class TestWhatIsMalformedStaysMalformed:
    """CONTROLS. Green before and after: the repair must not rename a real request error."""

    def test_a_restricted_network_naming_no_host(self, gateway):
        refused = _admit_asking(gateway, network="restricted", domains=(),
                                operator=_an_operator_allowing("example.com"))
        assert _on_the_record(refused) == "malformed"
        assert _at_the_doors(refused) == "malformed"

    def test_an_unknown_policy_path(self, gateway):
        refused = _admit_asking(gateway, mandatory=("limits.no_such_thing",))
        assert refused is not None
        assert _on_the_record(refused) == "malformed"

    def test_an_artifact_that_does_not_match_its_digest(self, gateway):
        request = _asking_for(artifact_sha256="a" * 64)
        gateway._operator_policy = _an_operator_allowing("example.com")
        with pytest.raises(Exception) as caught:
            gateway.admit(request, b"print(1)\n")
        assert "does not match the digest" in str(caught.value)
        assert _on_the_record(caught.value) == "malformed"

    def test_a_policy_digest_that_does_not_match_the_policy(self, gateway):
        code = b"print(1)\n"
        request = _asking_for(artifact_sha256=hashlib.sha256(code).hexdigest(),
                              policy_sha256="f" * 64)
        gateway._operator_policy = SandboxPolicy(
            network=NetworkRules(enabled=True, allowed_destinations=None))
        with pytest.raises(Exception) as caught:
            gateway.admit(request, code)
        assert "policy digest does not match" in str(caught.value)
        assert _on_the_record(caught.value) == "malformed"


# ------------------------------------------------------------------ every surface, over the wire

CODE = b"print('hi')"
CODE_SHA = hashlib.sha256(CODE).hexdigest()


def _call(base, token, operation, params):
    """One contract operation over HTTP, keeping the status as well as the body."""
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


def _a_job_for_hosts_the_operator_removed(base, token, run_id):
    job = {"command": ["python", "-c", "print('hi')"], "network": "allowlist",
           "allowed_domains": ["wanted.example"], "wall_clock_s": 30}
    status, told = _call(base, token, "prepare",
                         dict(job, artifact_sha256=CODE_SHA, artifact_bytes=len(CODE)))
    assert status == 200, (status, told)
    return dict(job, run_id=run_id, artifact=base64.b64encode(CODE).decode("ascii"),
                accepted_disclosure=told["accepted_disclosure"])


def _in_force(service, *hosts):
    """Put an operator policy allowing these hosts INTO FORCE, the way a gateway does.

    Setting `_operator_policy` alone gives a gateway whose configured policy was never measured, and
    it rightly refuses to run anything under that (`sandbox_unavailable`, "a policy takes effect only
    after it has been measured as itself"). The first run of this file did exactly that and recorded
    the wrong red (`E0004`, `E0005`). Measuring it is what makes the policy the one in force.
    """
    service._operator_policy = _an_operator_allowing(*hosts)
    _store_measurement(service)


def _a_job_prepared_over_mcp(base, token, run_id):
    """The same job, approved over MCP -- an approval is bound to the connection it was given on."""
    job = {"command": ["python", "-c", "print('hi')"], "network": "allowlist",
           "allowed_domains": ["wanted.example"], "wall_clock_s": 30}
    status, called = rpc(base, token, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": schemas.tool_name_for("prepare"),
                   "arguments": dict(job, artifact_sha256=CODE_SHA, artifact_bytes=len(CODE))}})
    assert status == 200 and not called["result"].get("isError"), called
    return dict(job, run_id=run_id, artifact=base64.b64encode(CODE).decode("ascii"),
                accepted_disclosure=called["result"]["structuredContent"]["accepted_disclosure"])


def _audit_outcomes(service, operation):
    path = os.path.join(str(service.state.root), "audit.jsonl")
    with io.open(path, encoding="utf-8") as fh:
        return [json.loads(line)["outcome"] for line in fh
                if line.strip() and json.loads(line).get("operation") == operation]


class TestEverySurfaceSaysTheSameWord:

    def test_10_the_rest_door(self, sandbox):
        service, base, token = sandbox
        _in_force(service, "allowed.example")
        status, answer = _call(base, token, "submit",
                               _a_job_for_hosts_the_operator_removed(base, token, "p" * 32))
        word = answer.get("refused")
        assert word == POLICY, (
            "the REST door: a job the operator's policy refused was named %r (HTTP %s)"
            % (word, status))
        assert status == rest.how_it_should_answer(POLICY) == 403
        assert "wanted.example" in answer.get("because", "")

    def test_11_the_audit_line(self, sandbox):
        service, base, token = sandbox
        _in_force(service, "allowed.example")
        _call(base, token, "submit", _a_job_for_hosts_the_operator_removed(base, token, "q" * 32))
        outcomes = _audit_outcomes(service, "submit")
        word = outcomes[-1] if outcomes else "(no line)"
        assert word == POLICY, (
            "the audit: a job the operator's policy refused was named %r" % word)

    def test_12_the_mcp_door(self, sandbox):
        service, base, token = sandbox
        _in_force(service, "allowed.example")
        job = _a_job_prepared_over_mcp(base, token, "m" * 32)
        status, called = rpc(base, token, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": schemas.tool_name_for("submit"), "arguments": job}})
        assert status == 200 and called["result"]["isError"] is True, called
        word = called["result"]["structuredContent"].get("refused")
        assert word == POLICY, (
            "the MCP door: a job the operator's policy refused was named %r" % word)

    def test_13_nothing_ran_and_nothing_was_claimed(self, sandbox):
        """A CONTROL on what the refusal must not do, before and after."""
        service, base, token = sandbox
        _in_force(service, "allowed.example")
        _call(base, token, "submit", _a_job_for_hosts_the_operator_removed(base, token, "n" * 32))
        record = service.runs.get("n" * 32)
        assert record is not None and record.state == "refused", record
        assert not service.ledger.knows_run("n" * 32), "a refused job was claimed in the ledger"
        assert not record.container_name, "a refused job created a sandbox"

    def test_14_the_older_signed_door(self, sandbox, monkeypatch):
        service, base, _token = sandbox
        _in_force(service, "allowed.example")
        conn = _paired(base, service.state)
        seen = []
        real_post = gc._post

        def _keeping_the_status(*args, **kwargs):
            status, answer = real_post(*args, **kwargs)
            seen.append(status)
            return status, answer

        monkeypatch.setattr(gc, "_post", _keeping_the_status)
        answer = consent.submit(conn, CODE, network="allowlist",
                                allowed_domains=("wanted.example",), wall_clock_s=30,
                                granted=_granted(service, network="allowlist",
                                                 domains=("wanted.example",), wall_clock_s=30))
        assert answer["state"] == "refused" and answer.get("signature"), answer
        word = answer.get("refused")
        assert word == POLICY, (
            "the older signed door: a job the operator's policy refused was named %r" % word)
        assert seen[-1] == 409, "the older door's status for this refusal changed: %r" % seen


# ------------------------------------------------------------------ a word a client does not know


class TestAClientKeepsAWordItDoesNotKnow:
    """A CONTROL on the compatibility rule: pass it through as text, with the sentence and remedy."""

    def test_15_an_unknown_word_survives_the_client(self):
        body = json.dumps({"refused": "a_word_from_a_future_contract",
                           "because": "the operator said no", "what_to_do": "ask them"})
        error = urllib.error.HTTPError("http://x", 403, "Forbidden", {},
                                       io.BytesIO(body.encode("utf-8")))
        refused = adapter._as_refusal(error)
        assert refused.refusal == "a_word_from_a_future_contract"
        assert "the operator said no" in refused.in_words()
        assert "ask them" in refused.in_words()


def test_the_word_is_one_the_contract_already_declares():
    """No new vocabulary: the repair uses what was declared."""
    assert POLICY in contract.REFUSALS
    assert admission.AS_A_REFUSAL["refused_by_operator_policy"] == POLICY
    assert dataclasses and pytest
