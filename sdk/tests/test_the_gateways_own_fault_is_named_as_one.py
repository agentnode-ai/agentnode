"""When the gateway catches ITSELF composing a policy wider than the job asked for, it says so.

`admit()` checks, rather than assumes, that the fold only ever narrows: "A gateway may narrow and never
widen." When that check fires, the gateway has contradicted its own invariant. Refusing is right; the
job must not run. But it refused with `ProtocolError`, which both classifiers render as `malformed` --
"Correct the request and send it again." -- to a caller whose request was never wrong. Observation `O2`
of the F12 arc.

THE WORD IS AN EXISTING ONE. `sandbox_unavailable`: "nothing could run it, and this is not the caller's
fault". What was missing is a type both classifiers recognise, so the remedy can say what is true -- a
fault in this sandbox, which sending the same job again will not fix -- instead of the generic "Try
again", which stays as it is for the transient failures it was written for (`o2-the-gateways-own-
invariant/writing/DECISION-0001`, after the consultation `Q0001`).

HOW IT IS REACHED. With the current fold no ordinary request widens anything (the consultation found
no path), so the fault is INJECTED where a future defect would be: `compose` returns a policy with a
longer wall clock than the job asked for. The check itself is not touched; patching it would only test
that an `if` raises.

WHAT EACH TEST GOES RED WITH on the unrepaired build, predeclared before the run:

* `test_1` to `test_6` -- "was named 'malformed'"
* `test_7` -- "the console still tells"

`test_8` to `test_11` must be GREEN BEFORE AND AFTER. They are the controls: nothing runs, a composition
that does not widen is admitted, the generic remedy for an unexpected failure is unchanged, and a
malformed request is still called malformed.
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import os
import urllib.error
import urllib.request

import pytest

from agentnode_sdk.access import contract, dispatch, rest
from agentnode_sdk.gateway import client as gc
from agentnode_sdk.gateway.server import name_the_refusal
from tests import consent
from tests.test_em3c_gateway import _granted, _paired
from tests.test_every_door import sandbox  # noqa: F401
from tests.test_only_narrowing import _admit_asking, _asking_for, gateway  # noqa: F401

UNAVAILABLE = "sandbox_unavailable"
ASKED = 37                 # the wall clock the job asks for; the fault grants ten times that


def _composes_wider(service):
    """The defect the check exists for, put where a defect would be: in the composition."""
    real = service.compose

    def wider(request, token="", *, client_id=""):
        got = real(request, token, client_id=client_id)
        return dataclasses.replace(
            got, limits=dataclasses.replace(got.limits,
                                            wall_clock_s=int(request.wall_clock_s) * 10))

    service.compose = wider


def _refused(gateway):
    _composes_wider(gateway)
    refused = _admit_asking(gateway, wall_clock_s=ASKED)
    assert refused is not None, "a composition wider than the job was admitted"
    return refused


def _on_the_record(refused):
    return name_the_refusal(refused)


def _at_the_doors(refused):
    told = dispatch._translate(refused)
    return told.refusal, told.what_to_do


class TestTheRecordAndTheDoorsNameIt:

    def test_1_the_record_names_it_the_sandboxs_fault(self, gateway):
        word, _remedy = _on_the_record(_refused(gateway))
        assert word == UNAVAILABLE, (
            "the gateway's own composition fault was named %r on the record" % word)

    def test_2_the_doors_name_it_the_sandboxs_fault(self, gateway):
        word, _remedy = _at_the_doors(_refused(gateway))
        assert word == UNAVAILABLE, (
            "the gateway's own composition fault was named %r by the doors" % word)

    def test_3_and_neither_tells_the_caller_to_retry_or_fix_the_request(self, gateway):
        refused = _refused(gateway)
        word, remedy = _on_the_record(refused)
        door_word, door_remedy = _at_the_doors(refused)
        for said in (remedy, door_remedy):
            assert ("whoever runs this sandbox" in said and "will not help" in said
                    and not said.lower().startswith("try again")), (
                "the fault was named %r and its remedy is %r" % (word, said))
        assert door_word == word


class TestTheOperatorIsTold:

    def test_4_on_stderr_by_path_and_never_by_value(self, gateway, capsys):
        refused = _refused(gateway)
        err = capsys.readouterr().err
        word = _on_the_record(refused)[0]
        assert "limits.wall_clock_s" in err and ("r" * 12) in err, (
            "the fault was named %r and the operator was told nothing on stderr: %r" % (word, err))
        assert str(ASKED * 10) not in err, "the diagnostic printed a policy value: %r" % err


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


def _a_prepared_job(base, token, run_id):
    job = {"command": ["python", "-c", "print('hi')"], "network": "none", "wall_clock_s": ASKED}
    status, told = _call(base, token, "prepare",
                         dict(job, artifact_sha256=CODE_SHA, artifact_bytes=len(CODE)))
    assert status == 200, (status, told)
    return dict(job, run_id=run_id, artifact=base64.b64encode(CODE).decode("ascii"),
                accepted_disclosure=told["accepted_disclosure"])


class TestEveryDoorSaysTheSameWord:

    def test_5_the_rest_door(self, sandbox):
        service, base, token = sandbox
        # THE FAULT IS IN PLACE BEFORE `prepare`, which the first version of this test got wrong:
        # the disclosure binds the composed policy, so composing differently at `submit` was refused
        # as `disclosure_required` -- correctly, and before the check this file is about (`E0004`).
        _composes_wider(service)
        job = _a_prepared_job(base, token, "w" * 32)
        status, answer = _call(base, token, "submit", job)
        word = answer.get("refused")
        assert word == UNAVAILABLE, (
            "the REST door: the gateway's own fault was named %r (HTTP %s)" % (word, status))
        assert status == rest.how_it_should_answer(UNAVAILABLE) == 503

    def test_6_the_older_signed_door(self, sandbox, monkeypatch):
        service, base, _token = sandbox
        conn = _paired(base, service.state)
        seen = []
        real_post = gc._post

        def _keeping_the_status(*args, **kwargs):
            status, answer = real_post(*args, **kwargs)
            seen.append(status)
            return status, answer

        monkeypatch.setattr(gc, "_post", _keeping_the_status)
        granted = _granted(service, wall_clock_s=ASKED)
        _composes_wider(service)
        answer = consent.submit(conn, CODE, wall_clock_s=ASKED, granted=granted)
        assert answer["state"] == "refused" and answer.get("signature"), answer
        word = answer.get("refused")
        assert word == UNAVAILABLE, (
            "the older signed door: the gateway's own fault was named %r" % word)
        assert seen[-1] == 409, "the older door's status for this refusal changed: %r" % seen


class TestTheConsoleSaysSomethingTrue:

    def test_7_it_no_longer_says_the_sandbox_is_not_running(self):
        """The console renders `sandbox_unavailable` with its own sentence and then the gateway's
        own words. Its sentence said the sandbox "is not running right now" and to try later --
        true of a stopped worker, false of a fault that a retry will meet again."""
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "agentnode_sdk", "console", "app.js"),
                  encoding="utf-8") as fh:
            app = fh.read()
        line = app[app.index("sandbox_unavailable:"):].split("],", 1)[0]
        assert "läuft gerade nicht" not in line and "später noch einmal" not in line, (
            "the console still tells every sandbox_unavailable refusal that the sandbox is not "
            "running and to try later: %r" % line)


class TestWhatMustNotChange:
    """CONTROLS. Green before and after."""

    def test_8_nothing_ran_and_nothing_was_claimed(self, sandbox):
        service, base, token = sandbox
        _composes_wider(service)                       # before `prepare`, as in test_5
        job = _a_prepared_job(base, token, "x" * 32)
        _call(base, token, "submit", job)
        record = service.runs.get("x" * 32)
        assert record is not None and record.state == "refused", record
        assert not service.ledger.knows_run("x" * 32), "a refused job was claimed in the ledger"
        assert not record.container_name, "a refused job created a sandbox"

    def test_9_a_composition_that_does_not_widen_is_admitted(self, gateway):
        assert _admit_asking(gateway, wall_clock_s=ASKED) is None

    def test_10_an_unexpected_failure_keeps_its_generic_answer(self):
        boom = RuntimeError("something nobody planned for")
        assert name_the_refusal(boom) == (
            "sandbox_unavailable",
            "Try again; if it keeps happening, tell whoever runs this sandbox.")
        told = dispatch._translate(boom)
        assert (told.refusal, told.what_to_do) == (
            "sandbox_unavailable", "Try again; if it keeps happening, tell whoever runs it.")

    def test_11_a_malformed_request_is_still_malformed(self, gateway):
        request = _asking_for(artifact_sha256="a" * 64)
        with pytest.raises(Exception) as caught:
            gateway.admit(request, b"print(1)\n")
        assert "does not match the digest" in str(caught.value)
        assert name_the_refusal(caught.value)[0] == "malformed"
