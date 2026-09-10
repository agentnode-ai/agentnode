"""The evidence contract, exercised on the path a real run takes.

`EM3C-E2-CLASSIFY-0001` found the previous version of this file passing while the module it tested
was unusable: the verifier read `expect_output` and `expect_cleanup`, the `Step` could carry
neither, and every verifier test here built its input as a dictionary by hand. The tests therefore
exercised the verifier with records the recorder could not produce, and the one path that mattered
-- record, write, read, judge -- was never travelled.

So the acceptance tests below go through `Recorder`, through the file, back through `load`, and into
`verify`. `_recorded()` is the only way they construct a record. Where a test needs a malformed
document, it writes malformed TEXT, because that is what a reader must survive and no recorder can
produce it.
"""
from __future__ import annotations

import json
import sys

import pytest

from agentnode_sdk.tools import evidence


# --------------------------------------------------------------- the answers these tests use
#
# NOT WRITTEN HERE. `EM3C-E3-CLASSIFY-0001`: the one authorised external run died because this
# file built the gateway's answer by hand, as the inner object the gateway keeps rather than the
# stamped and signed envelope a client receives -- so the reader and the recorder agreed with
# each other and with nothing else.
#
# `real_answers.py` starts a real gateway, pairs a real client, submits a real job and fetches
# the answer through the production client. `OK_RECORD` is that answer. A test that needs a
# different run, a refusal or a broken field starts from it and changes what it is about.

pytest_plugins = ("tests.real_answers",)

#: Filled, before any test runs, with an answer a real gateway really gave.
OK_RECORD: dict = {}
#: A run this gateway never heard of. Used where a test is about the WRONG run.
OTHER_RUN = "d06a38ad43e54e3ab39ec18a18dcbbe6"


@pytest.fixture(scope="session", autouse=True)
def _the_answer_these_tests_use(real_gateway, real_answer):
    GATEWAY[:] = [real_gateway]
    OK_RECORD.clear()
    OK_RECORD.update(real_answer)


def RUN() -> str:
    """The run the real answer is about."""
    return OK_RECORD["run_id"]


def container_for(run: str) -> str:
    return f"agentnode-em3c-{run[:12]}-abc"


#: The gateway that gave the answer these tests use. Set with it, so a test that needs a
#: variant can ask that gateway to sign one rather than assembling it here.
GATEWAY: list = []


def resigned(**changes) -> dict:
    """A real answer with something changed, signed and stamped by the gateway that gave it.

    For tests about something OTHER than the tie between an answer's outside and its inside: a
    changed field would otherwise break the tie and every such test would fail for that rather
    than for its own reason. `EM3C-EVIDENCE-0013`: this used to recompute the binding by hand
    and leave the old signature, which made it an object no gateway would ever send.
    """
    return GATEWAY[0].resigned(OK_RECORD, **changes)


def sealed(answer: dict) -> str:
    """The digest the client records when its verification accepts an answer."""
    from agentnode_sdk.gateway.protocol import canonical_bytes, digest

    return digest(canonical_bytes(answer))


def accepted(**changes) -> dict:
    """What the client observed about an answer it accepted. `verified_sha256` is over the
    answer as accepted, so anything changed in the record afterwards stops matching."""
    answer = changes.pop("over", None) or OK_RECORD
    observed = {"http_status": 200, "verified": True, "refusal": "",
                "asked_for": "/v1/jobs/" + answer.get("run_id", ""),
                "verified_sha256": sealed(answer)}
    observed.update(changes)
    if observed["verified"] is not True:
        observed["verified_sha256"] = ""
    return observed


STATED = {"expected_exit": None, "expected_refusal": "", "expect_output": False,
          "expect_cleanup": False, "expect_container_gone": False, "expect_timeout": False}


def a_step(**values):
    """A Step with every expectation stated. The recorder refuses one that leaves any unset, so
    a test that did not think about them would fail for that rather than for its own reason."""
    return evidence.Step(**{**STATED, **values})


def digest_for(name):
    return ("1" if name == "client" else "2") * 64


def identity(role, host, filesystem, os_name, **overrides):
    """A machine describing itself, with the commands that produced each value."""
    machine = {"role": role, "host_sha256": host, "filesystem_sha256": filesystem,
               "os": os_name,
               "commands": [{"command": "hostname", "exit_code": 0,
                             "stdout": "a-host", "stderr": ""},
                            {"command": "filesystem id", "exit_code": 0,
                             "stdout": "an-id", "stderr": ""}]}
    machine.update(overrides)
    return machine


#: What the client sent. The client's value is in it and the gateway's is not, which is the whole
#: of the two-machine argument -- and since `EM3C-EVIDENCE-0009` the rule reads this rather than
#: taking `in_request` at its word, so a helper that only set the boolean would now be refused.
CLIENT_VALUE = "a1b2c3d4e5f6071829"
GATEWAY_VALUE = "998877665544332211"
PAYLOAD = "print('E3-FROM-CLIENT " + CLIENT_VALUE + "')"


def value_for(made_on):
    return CLIENT_VALUE if made_on == "client" else GATEWAY_VALUE


def digest_of(text):
    import hashlib

    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def crossing(made_on, value=None, request_text=PAYLOAD, **overrides):
    """A sentinel that crosses properly.

    `in_request` is what makes the origin checkable: a value the client sent is the client's, one
    it never sent is not. The request itself is carried too, so that claim can be read rather
    than believed.
    """
    raw = value_for(made_on) if value is None else value
    sentinel = {"generated_on": made_on,
                "carried_over": "agentnode-job", "confirmed_over": "ssh",
                "value_sha256": digest_of(raw),
                "request_text": request_text,
                "in_request": made_on == "client",
                "in_response": True, "in_other_channel": True, "matched": True}
    sentinel.update(overrides)
    return sentinel


def two_machines(recorder):
    """The pair of identity steps and the two crossed sentinels every record needs.

    Added by every test that is not about machine separation, so those tests fail for their own
    reason rather than for a missing precondition.
    """
    recorder.record(a_step(
        name="client identity", role="client", argv=["(identity)"],
        started_at=1.0, ended_at=1.1, exit_code=0,
        machine=identity("client", "c" * 64, "cf" * 32, "Windows")))
    recorder.record(a_step(
        name="gateway identity", role="gateway", argv=["(identity)"],
        started_at=1.2, ended_at=1.3, exit_code=0,
        machine=identity("gateway", "g" * 64, "gf" * 32, "Linux")))
    recorder.record(a_step(
        name="a sentinel made on the client, read back over the gateway",
        role="client", argv=["(sentinel)"], started_at=1.4, ended_at=1.5, exit_code=0,
        sentinel=crossing("client")))
    recorder.record(a_step(
        name="a sentinel made on the gateway, read back over the client",
        role="gateway", argv=["(sentinel)"], started_at=1.6, ended_at=1.7, exit_code=0,
        sentinel=crossing("gateway")))


BINDING = {"mode": "none",
           "generation": "1", "policy_digest": "a" * 64, "configured_digest": "a" * 64,
           "digests_agree": "True", "required_properties": "container_isolation",
           "allowlist": [], "runtime": "docker 29.8.0", "backend": "docker",
           "conformance_digest": "b" * 64}

#: What one job was granted, in the gateway's own shape. Recorded because a digest of it says
#: nothing about whether it was inside what the machine allows.
#: The same binding, opened to one host. Used by the allowlist and containment rules below.
RESTRICTED = dict(BINDING, mode="restricted", allowlist=["api.example.com"])

NO_NETWORK = {"network.enabled": False, "network.allowed_destinations": [],
              "limits.cpu": 1, "limits.memory_mb": 512, "limits.processes": 64,
              "limits.wall_clock_s": 120}


def bindings(recorder, times=2):
    """The binding, captured before and after, as every record must carry it."""
    for n in range(times):
        recorder.record(a_step(
            name=f"the operator-policy binding, capture {n + 1}", role="gateway",
            argv=["(gateway egress --verbose)"], started_at=1.8 + n, ended_at=1.9 + n,
            exit_code=0, binding=dict(BINDING)))


def _recorded(tmp_path, steps, *, secrets=(), with_machines=True):
    """Record, write, read back, and judge. The only route these tests use.

    Returns the findings. Nothing here hands `verify` a dictionary the recorder never wrote.
    """
    path = tmp_path / "evidence.jsonl"
    recorder = evidence.Recorder(path, role="client", secrets=secrets, announce=False)
    if with_machines:
        two_machines(recorder)
        bindings(recorder)
    for step in steps:
        recorder.record(step)
    return evidence.check_file(path, secrets)


def good_step(**overrides) -> evidence.Step:
    """One step that is complete, self-consistent, and about the run it names.

    Every identifier comes from the real answer, because a step and the answer it attaches have
    to be about the same run and the answer is not this file's to invent.
    """
    answer = overrides.get("gateway_record", OK_RECORD) or {}
    values = dict(
        name="run a job", role="client",
        argv=["agentnode", "remote", "run", "job.py"],
        started_at=2.0, ended_at=3.0, exit_code=0, expected_exit=0,
        stdout="EXT-OK\n", stderr="",
        run_id=answer.get("run_id", ""), job_id=answer.get("job_id", ""),
        client_id="client-1",
        request_policy_sha256=answer.get("request_policy_sha256", ""),
        effective_policy_sha256=answer.get("effective_policy_sha256", ""),
        policy_deltas=answer.get("policy_deltas"),
        container=container_for(answer.get("run_id", "")),
        cleanup_verified=answer.get("cleanup_verified"), expect_output=True,
        gateway_record=dict(OK_RECORD), answer=accepted(over=answer),
    )
    values.update(overrides)
    return a_step(**values)


def _a_real_document(tmp_path) -> dict:
    """One complete record, as a recorder wrote it, read back as a plain document.

    The malformed-input tests need text no recorder can produce, and they need it to differ from
    a real record in exactly one way. Building the base by hand made that untrue twice over: it
    omitted fields the recorder always writes, and it was not what the reader would ever see.
    """
    path = tmp_path / "base.jsonl"
    recorder = evidence.Recorder(path, role="client", announce=False)
    recorder.record(good_step())
    return json.loads(path.read_text(encoding="utf-8").splitlines()[0])


def wrong_values() -> dict:
    """One wrong value per field, chosen so a rule that reads that field has to object.

    Written out rather than generated, because a generated one would be as blind as the field it
    is checking. A function rather than a constant, because several of these are built from the
    real answer, which does not exist until a real gateway has given one.
    """
    return {
        "name": {"name": "  "},
        "role": {"role": "somewhere-else"},
        "argv": {"argv": []},
        "started_at": {"started_at": 9.0, "ended_at": 3.0},
        "ended_at": {"ended_at": 1.0},
        "exit_code": {"exit_code": None},
        "stdout": {"stdout": "", "expect_output": True},
        "stderr": {"stderr": "", "exit_code": 1, "expected_exit": 1,
                   "expected_refusal": "a reason that appears nowhere"},
        "error_class": {"error_class": "FileNotFoundError", "exit_code": 0},
        "run_id": {"run_id": OTHER_RUN},
        "job_id": {"job_id": "another-job"},
        # Two steps: one client per record is a property OF the record, not of a step.
        "client_id": [{"client_id": "client-1"}, {"client_id": "client-2"}],
        "request_policy_sha256": {"request_policy_sha256": "b" * 64},
        "effective_policy_sha256": {"effective_policy_sha256": "b" * 64},
        "policy_deltas": {"policy_deltas": [{"path": "network.enabled", "requested": True,
                                             "effective": False}]},
        # A real answer with one field changed and the binding NOT recomputed: the tie between
        # an answer's outside and its inside is what must object here.
        "gateway_record": {"gateway_record": {**OK_RECORD, "run_id": OTHER_RUN}},
        "answer": {"answer": accepted(verified=False, refusal="it was not accepted")},
        "container": {"container": "agentnode-em3c-somebody-elses-run"},
        "container_query": {"expect_container_gone": True, "container_query": None},
        "cleanup_verified": {"cleanup_verified": False},
        "machine": {"machine": identity("client", "c" * 64, "cf" * 32, "Windows",
                                        commands=[{"command": "hostname", "exit_code": 1,
                                                   "stdout": "", "stderr": "no"}])},
        "sentinel": {"sentinel": crossing("client", request_text="nothing was sent")},
        "binding": {"binding": {**BINDING, "digests_agree": "False"}},
        "expected_exit": {"expected_exit": 9},
        "expected_refusal": {"expected_refusal": "a reason that appears nowhere"},
        "expect_output": {"expect_output": True, "stdout": ""},
        "expect_cleanup": {"expect_cleanup": True,
                           "gateway_record": resigned(cleanup_verified=None),
                           "cleanup_verified": None},
        "expect_container_gone": {"expect_container_gone": True},
        # A step that says the run was stopped at its limit, over an answer that says it exited.
        "expect_timeout": {"expect_timeout": True},
    }


def _fingerprint_of(said: dict) -> str:
    """The fingerprint that identity and version produce, the way the gateway produces it."""
    import hashlib

    return hashlib.sha256(
        f"{said.get('gateway_id', '')}\n{said.get('version', '')}".encode()).hexdigest()


def kinds(findings):
    return {f.kind for f in findings}


def messages(findings):
    return " | ".join(f.message for f in findings)


class TestTheControlPasses:
    """Without these, a verifier that refused everything would pass every test below."""

    def test_a_complete_record_is_accepted(self, tmp_path):
        assert _recorded(tmp_path, [good_step()]) == []

    def test_a_correctly_refused_step_is_accepted(self, tmp_path):
        step = good_step(
            expected_exit=1, exit_code=1,
            expected_refusal="already been used (replay)",
            gateway_record=resigned(state="refused",
                            refusal="this request has already been used (replay)"))
        assert _recorded(tmp_path, [step]) == []

    def test_a_step_expecting_cleanup_and_getting_it_is_accepted(self, tmp_path):
        assert _recorded(tmp_path, [good_step(expect_cleanup=True)]) == []


class TestTheContractIsOneClosedSchema:
    """EM3C-E2-CLASSIFY-0001: the verifier read two fields the Step could not carry."""

    def test_every_field_the_verifier_reads_can_be_recorded(self):
        """The defect itself. Read the module's source for `step.get("...")` and require each
name to be a field a Step can carry."""
        import inspect
        import re

        source = inspect.getsource(evidence)
        read = set(re.findall(r'step\.get\(\s*"([a-z_]+)"', source))
        unreachable = sorted(read - set(evidence.FIELD_NAMES))
        assert not unreachable, (
            "the verifier reads fields no recorded step can carry: " + str(unreachable))

    def test_every_field_a_step_carries_is_read(self):
        """The other direction, which the parity test used not to check.

        `EM3C-EVIDENCE-0009`: five fields were carried and read by nothing, listed in a constant
        and defended as a decision. An unread field is one no evidence can contradict, so it is
        not a weaker version of a checked field -- it is a place where the record can say
        anything. `notes` had no rule that could be written for it and is gone; the rest have
        one.
        """
        assert set(evidence.FIELD_NAMES) == set(evidence.READ_BY_RULES)
        assert not hasattr(evidence, "RECORDED_ONLY")

    @pytest.mark.parametrize("field", list(evidence.FIELD_NAMES))
    def test_each_field_a_step_carries_is_really_read(self, tmp_path, field):  # noqa: D
        """Not by reading the source for the field's name -- a list kept by hand passes that.

        Each field is given a value a rule should object to, on the real path, and the record
        must come back with at least one finding. A field nothing objects to is a field the
        record can say anything in, which is what `EM3C-EVIDENCE-0009` found five of.
        """
        broken = wrong_values()[field]
        cases = broken if isinstance(broken, list) else [broken]
        found = _recorded(tmp_path, [good_step(**case) for case in cases])
        assert found, f"{field} was made wrong and no rule said anything"

    def test_the_matrix_covers_every_field(self):
        assert set(wrong_values()) == set(evidence.FIELD_NAMES), \
            sorted(set(wrong_values()) ^ set(evidence.FIELD_NAMES))

    def test_the_unbroken_step_is_accepted(self, tmp_path):
        """The control: those findings are about what was broken, not about the step itself."""
        assert _recorded(tmp_path, [good_step()]) == []

    def test_expect_output_and_expect_cleanup_are_carriable(self):
        assert "expect_output" in evidence.FIELD_NAMES
        assert "expect_cleanup" in evidence.FIELD_NAMES

    def test_the_declared_types_cover_exactly_the_fields(self):
        assert set(evidence.FIELD_NAMES) == set(evidence._TYPES)

    def test_the_module_refuses_to_load_if_the_schema_and_its_types_drift(self):
        """The guard that made the first counter-check for this fail to collect rather than fail
a test. It is a stronger outcome than a test noticing, so it gets its own cover."""
        import inspect

        source = inspect.getsource(evidence)
        assert "if set(FIELD_NAMES) != set(_TYPES):" in source
        assert "drifted apart" in source

    def test_what_the_recorder_writes_is_what_the_reader_accepts(self, tmp_path):
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        recorder.record(good_step())
        loaded = evidence.load(path)
        assert set(loaded[0]) <= set(evidence.FIELD_NAMES)
        assert loaded[0]["expect_output"] is True

    def test_an_unknown_field_is_refused(self, tmp_path):
        path = tmp_path / "e.jsonl"
        path.write_text(json.dumps({"schema": evidence.SCHEMA, "name": "x", "role": "client",
                                    "argv": [], "started_at": 1.0, "ended_at": 2.0,
                                    "exit_code": 0, "surprise": True}) + "\n", encoding="utf-8")
        with pytest.raises(evidence.EvidenceError) as caught:
            evidence.load(path)
        assert "does not describe" in str(caught.value)

    @pytest.mark.parametrize("field", list(evidence.MANDATORY))
    def test_a_missing_mandatory_field_is_refused(self, tmp_path, field):
        """Every mandatory field, one at a time, over a document a recorder really wrote.

        `EM3C-EVIDENCE-0005`: the matrix named six of the eleven, and its base document omitted
        all five expectations -- so the five that were missing anyway were never shown to be
        refused for being missing, and the six that were tested were removed from a document
        that no recorder could have produced. The base is now taken from a real record, so the
        removal is the only thing wrong with it.
        """
        document = _a_real_document(tmp_path)
        assert field in document, "the base document does not carry the field being removed"
        del document[field]
        path = tmp_path / "broken.jsonl"
        path.write_text(json.dumps(document) + "\n", encoding="utf-8")
        with pytest.raises(evidence.EvidenceError) as caught:
            evidence.load(path)
        assert field in str(caught.value)

    @pytest.mark.parametrize("field", list(evidence.MANDATORY))
    def test_the_recorder_cannot_be_asked_for_a_step_without_one(self, tmp_path, field):
        """The same matrix on the other entry point.

        `EM3C-EVIDENCE-0009`: the negatives above start from a real record and then edit it as
        text, which is the reader's side. This is the recorder's: a step missing a mandatory
        field cannot be recorded either, and the two together are what "at every entry point"
        means. The five expectations are refused by the recorder, the six that identify a step
        cannot be left out of a `Step` at all -- and both are a refusal, not a default.
        """
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", announce=False)
        values = dict(name="x", role="client", argv=[], started_at=1.0, ended_at=1.1,
                      exit_code=0, **STATED)
        del values[field]
        with pytest.raises((evidence.EvidenceError, TypeError)) as caught:
            recorder.record(evidence.Step(**values))
        assert field in str(caught.value), str(caught.value)
        assert not (tmp_path / "e.jsonl").exists()

    def test_the_recorder_does_record_the_same_step_when_it_is_whole(self, tmp_path):
        """The control for the matrix above: those refusals are about the missing field, not
        about a recorder that refuses this shape of step whatever it is given."""
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        recorder.record(evidence.Step(name="x", role="client", argv=[], started_at=1.0,
                                      ended_at=1.1, exit_code=0, **STATED))
        assert len(evidence.load(path)) == 1

    def test_the_base_of_that_matrix_is_itself_accepted(self, tmp_path):
        """The control. Without it, every case above could pass on a document refused for some
        reason that has nothing to do with the field that was removed."""
        path = tmp_path / "whole.jsonl"
        path.write_text(json.dumps(_a_real_document(tmp_path)) + "\n", encoding="utf-8")
        assert len(evidence.load(path)) == 1

    def test_the_matrix_covers_every_mandatory_field(self):
        """A field added to MANDATORY without a case above would go untested while the parametrise
        stayed green. Read off the marker itself, so this cannot drift from what actually runs."""
        cases = [mark for mark in
                 self.test_a_missing_mandatory_field_is_refused.pytestmark
                 if mark.name == "parametrize"]
        assert len(cases) == 1
        assert list(cases[0].args[1]) == list(evidence.MANDATORY)
        assert len(evidence.MANDATORY) == 12

    # -- the shapes inside a nested list ------------------------------------------------------

    @pytest.mark.parametrize("entry,expected", [
        ("not a record", "says a record"),
        ({"command": "hostname", "exit_code": 0, "stdout": "h"}, "has no stderr"),
        ({"command": "hostname", "exit_code": 0, "stdout": "h", "stderr": "", "extra": 1},
         "does not describe"),
        ({"command": 7, "exit_code": 0, "stdout": "h", "stderr": ""}, "is int"),
        ({"command": "hostname", "exit_code": "0", "stdout": "h", "stderr": ""}, "is str"),
    ])
    def test_an_identity_command_that_is_not_one_is_refused(self, tmp_path, entry, expected):
        """`EM3C-EVIDENCE-0005`: `commands` was closed as a list and open in its elements, so a
        reader accepted anything inside it while the rules read four keys out of each entry."""
        document = _a_real_document(tmp_path)
        document["machine"] = {"role": "client", "host_sha256": "c" * 64,
                               "filesystem_sha256": "f" * 64, "os": "Windows",
                               "commands": [entry]}
        path = tmp_path / "broken.jsonl"
        path.write_text(json.dumps(document) + "\n", encoding="utf-8")
        with pytest.raises(evidence.EvidenceError) as caught:
            evidence.load(path)
        assert expected in str(caught.value), str(caught.value)


    def test_a_well_formed_identity_command_is_accepted(self, tmp_path):
        """The control for the five above."""
        document = _a_real_document(tmp_path)
        document["machine"] = {"role": "client", "host_sha256": "c" * 64,
                               "filesystem_sha256": "f" * 64, "os": "Windows",
                               "commands": [{"command": "hostname", "exit_code": 0,
                                             "stdout": "a-host", "stderr": ""}]}
        path = tmp_path / "whole.jsonl"
        path.write_text(json.dumps(document) + "\n", encoding="utf-8")
        assert len(evidence.load(path)) == 1

    def test_a_duplicated_key_is_refused(self, tmp_path):
        path = tmp_path / "e.jsonl"
        path.write_text('{"schema": 2, "name": "a", "name": "b", "role": "client", "argv": [],'
                        ' "started_at": 1.0, "ended_at": 2.0, "exit_code": 0}\n', encoding="utf-8")
        with pytest.raises(evidence.EvidenceError) as caught:
            evidence.load(path)
        assert "more than once" in str(caught.value)

    @pytest.mark.parametrize("field,value", [
        ("expect_output", "yes"), ("expect_cleanup", 1), ("exit_code", "0"),
        ("argv", "a string"), ("run_id", 7), ("policy_deltas", {}),
    ])
    def test_a_wrongly_typed_field_is_refused(self, tmp_path, field, value):
        document = {"schema": evidence.SCHEMA, "name": "x", "role": "client", "argv": [],
                    "started_at": 1.0, "ended_at": 2.0, "exit_code": 0}
        document[field] = value
        path = tmp_path / "e.jsonl"
        path.write_text(json.dumps(document) + "\n", encoding="utf-8")
        with pytest.raises(evidence.EvidenceError):
            evidence.load(path)

    def test_a_record_from_another_schema_is_not_read_approximately(self, tmp_path):
        path = tmp_path / "e.jsonl"
        path.write_text(json.dumps({"schema": evidence.SCHEMA + 1, "name": "x", "role": "c",
                                    "argv": [], "started_at": 1.0, "ended_at": 2.0,
                                    "exit_code": 0}) + "\n", encoding="utf-8")
        with pytest.raises(evidence.EvidenceError):
            evidence.load(path)


class TestAnExpectationNeverBecomesAnObservation:

    @pytest.mark.parametrize("field", evidence.OBSERVED_BY_RUNNING)
    def test_the_recorder_refuses_to_be_told_what_it_observed(self, tmp_path, field):
        """Every expectation is stated, so this refusal is about the observation being supplied
        and not about an expectation left out -- which is a different rule with its own tests."""
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", announce=False)
        with pytest.raises(evidence.EvidenceError) as caught:
            recorder.run("x", [sys.executable, "-c", "pass"], **{**STATED, field: 0})
        assert field in str(caught.value) and "observation" in str(caught.value)

    def test_a_failing_command_records_its_real_status_not_the_expected_one(self, tmp_path):
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", announce=False)
        step = recorder.run("fails", [sys.executable, "-c", "raise SystemExit(3)"],
                            **{**STATED, "expected_exit": 0})
        assert step.exit_code == 3
        assert step.expected_exit == 0

    def test_the_two_kinds_of_field_do_not_overlap(self):
        assert not set(evidence.EXPECTATIONS) & set(evidence.OBSERVED_BY_RUNNING)


class TestRunStatesNothingOnTheCallersBehalf:
    """EM3C-EVIDENCE-0003: `run()` used to fill in every expectation the caller had not, which
    put back the ambiguity the UNSET defaults exist to remove. A convenience that answers the
    caller's question for them makes "no check applies" and "nobody said" the same record."""

    def _recorder(self, tmp_path):
        return evidence.Recorder(tmp_path / "e.jsonl", role="client", announce=False)

    def test_running_a_command_without_stating_the_expectations_is_refused(self, tmp_path):
        with pytest.raises(evidence.EvidenceError) as caught:
            self._recorder(tmp_path).run("x", [sys.executable, "-c", "pass"])
        assert "never said whether" in str(caught.value)

    @pytest.mark.parametrize("left_out", list(evidence.EXPECTATIONS))
    def test_leaving_out_any_one_of_them_is_refused_by_name(self, tmp_path, left_out):
        stated = {k: v for k, v in STATED.items() if k != left_out}
        with pytest.raises(evidence.EvidenceError) as caught:
            self._recorder(tmp_path).run("x", [sys.executable, "-c", "pass"], **stated)
        assert left_out in str(caught.value)

    def test_nothing_is_written_when_a_step_was_refused(self, tmp_path):
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        with pytest.raises(evidence.EvidenceError):
            recorder.run("x", [sys.executable, "-c", "pass"])
        assert not path.exists() or path.read_text(encoding="utf-8") == ""

    def test_stating_them_all_records_the_step(self, tmp_path):
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        step = recorder.run("x", [sys.executable, "-c", "print('hi')"], **STATED)
        assert step.expect_output is False
        assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["stdout"].strip() \
            == "hi"

    def test_what_the_caller_states_is_what_is_written(self, tmp_path):
        """The control against a `run` that refused everything: a stated expectation has to
        arrive in the file as stated, not merely be accepted."""
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        recorder.run("x", [sys.executable, "-c", "print('hi')"],
                     **{**STATED, "expect_output": True, "expected_exit": 0})
        written = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert written["expect_output"] is True and written["expected_exit"] == 0


class TestTheRulesFireOnRecordedSteps:
    """Each rule, on the real path. The previous file proved these only against hand-built dicts."""

    def test_a_wrong_exit_code_fails(self, tmp_path):
        found = _recorded(tmp_path, [good_step(exit_code=1, expected_exit=0)])
        assert evidence.FAIL in kinds(found) and "exited 1" in messages(found)

    def test_an_uncaptured_exit_code_is_an_evidence_error(self, tmp_path):
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", announce=False)
        two_machines(recorder)
        recorder.run("a binary that is not there", ["definitely-not-a-real-binary-xyz"],
                     **STATED)
        found = evidence.check_file(tmp_path / "e.jsonl")
        assert evidence.EVIDENCE_ERROR in kinds(found)
        assert "no exit code was captured" in messages(found)

    def test_empty_output_where_output_was_required_is_an_evidence_error(self, tmp_path):
        found = _recorded(tmp_path, [good_step(stdout="   \n", expect_output=True)])
        assert "stdout is empty" in messages(found)

    def test_a_swapped_run_id_fails(self, tmp_path):
        """The step is about one run and the answer about another. Resigned, so the tie between
        an answer's outside and its inside is intact and this fails for the swap alone."""
        answer = resigned(run_id=OTHER_RUN)
        found = _recorded(tmp_path, [good_step(gateway_record=answer, run_id=RUN(),
                                               container=container_for(RUN()))])
        assert evidence.FAIL in kinds(found) and "is about" in messages(found)

    def test_a_missing_gateway_record_is_an_evidence_error(self, tmp_path):
        found = _recorded(tmp_path, [good_step(
            gateway_record=None, run_id=RUN(), container=container_for(RUN()),
            answer=accepted(verified=False, refusal="nothing came back"))])
        assert "carries no gateway record" in messages(found)

    def test_a_404_is_not_a_match(self, tmp_path, real_gateway):
        """A real not-found answer, from a real gateway."""
        status, body = real_gateway.an_absent_run()
        found = _recorded(tmp_path, [good_step(
            gateway_record=body, run_id=RUN(), container=container_for(RUN()),
            answer=accepted(http_status=status, verified=False,
                            refusal="the gateway does not know that run"))])
        assert "absent record is not a match" in messages(found)

    def test_a_mismatched_digest_fails(self, tmp_path):
        found = _recorded(tmp_path, [good_step(request_policy_sha256="b" * 64)])
        assert "request_policy_sha256" in messages(found)

    def test_a_digest_the_step_never_recorded_is_an_evidence_error(self, tmp_path):
        """EM3C-E2-CLASSIFY-0001 noted the comparison was skipped when the claim was empty."""
        found = _recorded(tmp_path, [good_step(request_policy_sha256="")])
        assert "never compared" in messages(found)

    def test_missing_deltas_where_the_digests_differ_fails(self, tmp_path):
        found = _recorded(tmp_path, [good_step(
            effective_policy_sha256="b" * 64,
            gateway_record=resigned(effective_policy_sha256="b" * 64,
                            policy_deltas=[]))])
        assert "no narrowing was reported" in messages(found)

    def test_the_wrong_refusal_reason_fails(self, tmp_path):
        found = _recorded(tmp_path, [good_step(
            exit_code=1, expected_exit=1, expected_refusal="already been used (replay)",
            gateway_record=resigned(state="refused",
                            refusal="this request is 880s old; the limit is 120s"))])
        assert "does not appear" in messages(found)

    def test_unknown_cleanup_where_cleanup_was_required_is_an_evidence_error(self, tmp_path):
        found = _recorded(tmp_path, [good_step(
            expect_cleanup=True, gateway_record=resigned(cleanup_verified=None))])
        assert "cleanup is unknown" in messages(found)

    def test_absent_cleanup_where_cleanup_was_required_is_an_evidence_error(self, tmp_path):
        record = {k: v for k, v in OK_RECORD.items() if k != "cleanup_verified"}
        found = _recorded(tmp_path, [good_step(expect_cleanup=True, gateway_record=record)])
        assert "does not mention it" in messages(found)

    def test_a_container_from_another_run_fails(self, tmp_path):
        found = _recorded(tmp_path, [good_step(
            container=f"agentnode-em3c-{OTHER_RUN[:12]}-zzz")])
        assert "does not carry run" in messages(found)

    def test_a_secret_in_the_record_fails(self, tmp_path):
        secret = "s3cr3t-token-value-0123456789"
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", secrets=[], announce=False)
        two_machines(recorder)
        recorder.record(good_step(stdout=f"token={secret}\n"))
        found = evidence.check_file(tmp_path / "e.jsonl", [secret])
        assert "live secret value" in messages(found)


class TestAContainerIsOnlyGoneWhenSomebodyLooked:
    """EM3C-E2-CLASSIFY-0001: every remote failure became an empty string, and the empty string
was read as absence."""

    GOOD_QUERY = {"ran": True, "exit_code": 0, "stdout": "END-OF-LISTING\n", "stderr": "",
                  "parsed": True, "complete": True, "names": [], "ids": [],
                  "sought_id": "abc123def456", "error_class": "", "command": "docker ps -a"}

    def _found(self, tmp_path, query, **overrides):
        values = {"expect_container_gone": True, "container_query": query,
                  "container": f"agentnode-em3c-{RUN()[:12]}-abc"}
        values.update(overrides)
        return _recorded(tmp_path, [good_step(**values)])

    def test_a_query_that_ran_and_found_nothing_passes(self, tmp_path):
        """The control. Without it a rule that refused everything would pass the rest."""
        assert self._found(tmp_path, dict(self.GOOD_QUERY)) == []

    def test_the_container_still_being_there_fails(self, tmp_path):
        query = {**self.GOOD_QUERY, "names": [f"agentnode-em3c-{RUN()[:12]}-abc"]}
        found = self._found(tmp_path, query)
        assert evidence.FAIL in kinds(found) and "still there" in messages(found)

    def test_the_id_still_being_there_fails(self, tmp_path):
        query = {**self.GOOD_QUERY, "ids": ["abc123def456"]}
        found = self._found(tmp_path, query)
        assert evidence.FAIL in kinds(found)

    @pytest.mark.parametrize("defect,expected", [
        ({"ran": False}, "never ran"),
        ({"exit_code": 1}, "only a zero answer"),
        ({"error_class": "TimeoutExpired"}, "the query failed"),
        ({"parsed": False}, "was not parsed"),
        ({"complete": False}, "ran to the end"),
        ({"stdout": "   "}, "unknown answer"),
        ({"names": "__absent__"}, "list of container names"),
    ])
    def test_every_way_of_not_knowing_is_an_evidence_error(self, tmp_path, defect, expected):
        """Each query is complete except for the one thing it is about, so it reaches the rule it
        names rather than being turned away at the door."""
        query = {**self.GOOD_QUERY, **defect}
        for key, value in list(query.items()):
            if value == "__absent__":
                del query[key]
        found = self._found(tmp_path, query)
        assert evidence.EVIDENCE_ERROR in kinds(found), messages(found)
        assert expected in messages(found)
        assert evidence.FAIL not in kinds(found), (
            "not knowing was reported as the container being there")

    @pytest.mark.parametrize("missing", ["sought_id", "container"])
    def test_absence_needs_both_the_name_and_the_id(self, tmp_path, missing):
        """A name can be reused and an id cannot, so absence of one is weaker than absence of
        both -- and the criterion asks for both."""
        query = dict(self.GOOD_QUERY)
        overrides = {}
        if missing == "sought_id":
            query["sought_id"] = ""
        else:
            overrides["container"] = ""
        found = self._found(tmp_path, query, **overrides)
        assert "never named" in messages(found)


class TestTwoMachinesAreShownToBeTwo:
    """EM3C-E2-CLASSIFY-0001: a locally failed `ls` proves only that a local `ls` failed."""

    def test_a_record_with_no_machines_cannot_establish_separation(self, tmp_path):
        found = _recorded(tmp_path, [good_step()], with_machines=False)
        assert "fewer than two machines" in messages(found)

    def test_two_machines_with_the_same_host_identity_fail(self, tmp_path):
        """Each machine reports once, over its own channel, and they report the same host.

        It used to add a THIRD identity that replaced the gateway's, which worked only because
        a later identity silently won. `EM3C-EVIDENCE-0011` closed that, so this describes the
        state it is about instead of arriving at it by replacement.
        """
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        recorder.record(a_step(
            name="client identity", role="client", argv=["(identity)"],
            started_at=1.0, ended_at=1.1, exit_code=0,
            machine=identity("client", "c" * 64, "cf" * 32, "Windows")))
        recorder.record(a_step(
            name="gateway identity", role="gateway", argv=["(identity)"],
            started_at=1.2, ended_at=1.3, exit_code=0,
            machine=identity("gateway", "c" * 64, "gf" * 32, "Linux")))
        recorder.record(a_step(
            name="a crossing", role="client", argv=["x"], started_at=1.4, ended_at=1.5,
            exit_code=0, sentinel=crossing("client")))
        recorder.record(a_step(
            name="the other way", role="gateway", argv=["x"], started_at=1.6, ended_at=1.7,
            exit_code=0, sentinel=crossing("gateway")))
        found = evidence.check_file(path)
        assert "same host identity" in messages(found)

    def test_a_sentinel_carried_and_confirmed_over_one_channel_fails(self, tmp_path):
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        two_machines(recorder)
        recorder.record(a_step(
            name="a sentinel that only one path ever saw", role="client", argv=["(sentinel)"],
            started_at=3.0, ended_at=3.1, exit_code=0,
            sentinel=crossing("client", confirmed_over="agentnode-job")))
        found = evidence.check_file(path)
        assert "same channel" in messages(found)

    def test_a_label_that_disagrees_with_the_record_fails(self, tmp_path):
        """The founder's concern, and the one that matters: a sentinel calling itself the
gateway's while sitting in what the client sent is claiming its own provenance."""
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        two_machines(recorder)
        recorder.record(a_step(
            name="a value the client sent, calling itself the gateway's", role="gateway",
            argv=["(sentinel)"], started_at=3.0, ended_at=3.1, exit_code=0,
            # The label says gateway and the value IS in what the client sent, so the client
            # could have produced it. The request in the record is what makes that visible.
            sentinel=crossing("gateway", CLIENT_VALUE, in_request=True)))
        found = evidence.check_file(path)
        assert "The label is not evidence" in messages(found)

    def test_a_client_value_that_was_never_sent_fails(self, tmp_path):
        """The mirror. A value the client did not send is not the client's."""
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        two_machines(recorder)
        recorder.record(a_step(
            name="a value the client never sent, calling itself the client's", role="client",
            argv=["(sentinel)"], started_at=3.0, ended_at=3.1, exit_code=0,
            # The label says client and the value was never in what the client sent.
            sentinel=crossing("client", GATEWAY_VALUE, in_request=False)))
        found = evidence.check_file(path)
        assert "The label is not evidence" in messages(found)

    def test_a_sentinel_with_no_provenance_fields_is_refused_when_read(self, tmp_path):
        """Stronger than a finding: the shape requires the fields, so such a record cannot even
        be read. A recorder cannot produce one, so this is written as text."""
        path = tmp_path / "e.jsonl"
        bare = {"generated_on": "client", "carried_over": "agentnode-job",
                "confirmed_over": "ssh", "value_sha256": "6" * 64, "matched": True}
        document = {"schema": evidence.SCHEMA, "name": "x", "role": "client", "argv": [],
                    "started_at": 1.0, "ended_at": 2.0, "exit_code": 0,
                    "expected_exit": None, "expected_refusal": "", "expect_output": False,
                    "expect_cleanup": False, "expect_container_gone": False,
                    "expect_timeout": False,
                    "sentinel": bare}
        path.write_text(json.dumps(document) + "\n", encoding="utf-8")
        with pytest.raises(evidence.EvidenceError) as caught:
            evidence.load(path)
        assert "sentinel has no" in str(caught.value)

    def test_a_value_not_seen_on_the_other_channel_fails(self, tmp_path):
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        two_machines(recorder)
        recorder.record(a_step(
            name="a value only one channel saw", role="client", argv=["(sentinel)"],
            started_at=3.0, ended_at=3.1, exit_code=0,
            sentinel=crossing("client", in_other_channel=False)))
        found = evidence.check_file(path)
        assert "not found over ssh" in messages(found)

    def test_a_sentinel_that_did_not_come_back_fails(self, tmp_path):
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        recorder.record(a_step(
            name="client identity", role="client", argv=["(identity)"],
            started_at=1.0, ended_at=1.1, exit_code=0,
            machine=identity("client", "c" * 64, "cf" * 32, "Windows")))
        recorder.record(a_step(
            name="gateway identity", role="gateway", argv=["(identity)"],
            started_at=1.2, ended_at=1.3, exit_code=0,
            machine=identity("gateway", "g" * 64, "gf" * 32, "Linux")))
        recorder.record(a_step(
            name="a sentinel that never arrived", role="client", argv=["(sentinel)"],
            started_at=1.4, ended_at=1.5, exit_code=0,
            sentinel=crossing("client", in_response=False, matched=False)))
        found = evidence.check_file(path)
        assert "never came back" in messages(found)

    def test_one_direction_only_is_an_evidence_error(self, tmp_path):
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        recorder.record(a_step(
            name="client identity", role="client", argv=["(identity)"],
            started_at=1.0, ended_at=1.1, exit_code=0,
            machine=identity("client", "c" * 64, "cf" * 32, "Windows")))
        recorder.record(a_step(
            name="gateway identity", role="gateway", argv=["(identity)"],
            started_at=1.2, ended_at=1.3, exit_code=0,
            machine=identity("gateway", "g" * 64, "gf" * 32, "Linux")))
        recorder.record(a_step(
            name="only one direction", role="client", argv=["(sentinel)"],
            started_at=1.4, ended_at=1.5, exit_code=0,
            sentinel=crossing("client", confirmed_over="ssh", matched=True)))
        found = evidence.check_file(path)
        assert "both directions" in messages(found)

    def test_the_same_sentinel_value_both_ways_fails(self, tmp_path):
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        recorder.record(a_step(
            name="client identity", role="client", argv=["(identity)"],
            started_at=1.0, ended_at=1.1, exit_code=0,
            machine=identity("client", "c" * 64, "cf" * 32, "Windows")))
        recorder.record(a_step(
            name="gateway identity", role="gateway", argv=["(identity)"],
            started_at=1.2, ended_at=1.3, exit_code=0,
            machine=identity("gateway", "g" * 64, "gf" * 32, "Linux")))
        for made, checked in (("client", "gateway"), ("gateway", "client")):
            recorder.record(a_step(
                name=f"sentinel {made}", role=made, argv=["(sentinel)"],
                started_at=1.4, ended_at=1.5, exit_code=0,
                # The same value both ways. The gateway's copy records a request that does NOT
                # contain it, so it still derives as the gateway's and the rule about two
                # values being one is the rule that fires.
                sentinel=crossing(made, CLIENT_VALUE,
                                  **({} if made == "client"
                                     else {"request_text": "print('nothing of the sort')",
                                           "in_request": False}))))
        found = evidence.check_file(path)
        assert "generated independently" in messages(found)

    def test_the_same_operating_system_on_both_is_allowed(self, tmp_path):
        """Two distinct hosts may run the same operating system. Failing on that would be a rule
        about a coincidence -- EM3C-EVIDENCE-0001 was right to say so. The host and filesystem
        identities are what separate them."""
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        recorder.record(a_step(
            name="client identity", role="client", argv=["(identity)"],
            started_at=1.0, ended_at=1.1, exit_code=0,
            machine=identity("client", "c" * 64, "cf" * 32, "Linux")))
        recorder.record(a_step(
            name="gateway identity", role="gateway", argv=["(identity)"],
            started_at=1.2, ended_at=1.3, exit_code=0,
            machine=identity("gateway", "g" * 64, "gf" * 32, "Linux")))
        for made, checked in (("client", "gateway"), ("gateway", "client")):
            recorder.record(a_step(
                name=f"sentinel {made}", role=made, argv=["(sentinel)"],
                started_at=1.4, ended_at=1.5, exit_code=0,
                sentinel=crossing(made)))
        bindings(recorder)
        found = evidence.check_file(path)
        assert "operating system" not in messages(found), messages(found)

    def test_an_identity_command_that_failed_is_an_evidence_error(self, tmp_path):
        """An identity built from a command that did not succeed is an assertion."""
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        broken = identity("client", "c" * 64, "cf" * 32, "Windows")
        broken["commands"][0]["exit_code"] = 1
        recorder.record(a_step(
            name="client identity", role="client", argv=["(identity)"],
            started_at=1.0, ended_at=1.1, exit_code=0, machine=broken))
        recorder.record(a_step(
            name="gateway identity", role="gateway", argv=["(identity)"],
            started_at=1.2, ended_at=1.3, exit_code=0,
            machine=identity("gateway", "g" * 64, "gf" * 32, "Linux")))
        found = evidence.check_file(path)
        assert "never established" in messages(found)


class TestSecretsNeverReachTheRecord:

    SECRET = "tok_9f8e7d6c5b4a3210fedcba"

    def _written(self, tmp_path, step):
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", secrets=[self.SECRET], announce=False)
        recorder.record(step)
        return (tmp_path / "e.jsonl").read_text(encoding="utf-8")

    def test_a_pairing_code_argument_is_redacted(self):
        assert evidence.redact_argv(
            ["agentnode", "remote", "connect", "http://x", "--code", "ABCD-EFGH-IJKL"]) == [
            "agentnode", "remote", "connect", "http://x", "--code", evidence.REDACTED]

    def test_the_command_itself_survives_redaction(self):
        argv = ["agentnode", "remote", "run", "job.py", "--allow", "example.com"]
        assert evidence.redact_argv(argv) == argv

    @pytest.mark.parametrize("field,value", [
        ("stderr", "the token was {s}" + chr(10)),
        ("client_id", "{s}"),
        ("stdout", "value={s}\n"),
        # EM3C-EVIDENCE-0003: the parametrisation stopped at stdout, so the two places a failure
        # actually puts text -- the other stream, and the class of the exception that ended the
        # command -- were not shown to be reached.
        ("stderr", "Traceback: connecting with {s} failed\n"),
        ("error_class", "CalledProcessError: {s}"),
        ("expected_refusal", "the token {s} has already been used"),
        ("container", "agentnode-em3c-{s}"),
        ("job_id", "{s}"),
        ("run_id", "{s}"),
    ])
    def test_a_secret_in_any_string_field_is_removed(self, tmp_path, field, value):
        step = good_step(**{field: value.format(s=self.SECRET)})
        written = self._written(tmp_path, step)
        assert self.SECRET not in written
        assert evidence.REDACTED in written

    @pytest.mark.parametrize("where", [
        {"machine": {"role": "gateway", "host_sha256": "g" * 64, "filesystem_sha256": "f" * 64,
                     "os": "Linux",
                     "commands": [{"command": "ssh --key {s}", "exit_code": 1,
                                   "stdout": "", "stderr": "permission denied for {s}"}]}},
        {"container_query": {"ran": True, "exit_code": 1, "stdout": "",
                             "stderr": "docker: error response: {s}", "error_class": "",
                             "parsed": True, "command": "docker ps --filter {s}"}},
        {"sentinel": {"generated_on": "client", "carried_over": "agentnode-job",
                      "confirmed_over": "ssh", "value_sha256": "{s}", "in_request": True,
                      "request_text": "sent {s}",
                      "in_response": True, "in_other_channel": True, "matched": True}},
    ])
    def test_a_secret_inside_a_nested_structure_is_removed(self, tmp_path, where):
        filled = json.loads(json.dumps(where).replace("{s}", self.SECRET))
        assert self.SECRET not in self._written(tmp_path, good_step(**filled))

    def test_a_secret_a_real_command_printed_to_stderr_is_removed(self, tmp_path):
        """Through `run`, so the stream is what a subprocess actually wrote rather than a value
        this test placed in the field itself."""
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", secrets=[self.SECRET], announce=False)
        recorder.run("a command that fails loudly",
                     [sys.executable, "-c",
                      f"import sys; sys.stderr.write('failed: {self.SECRET}'); sys.exit(2)"],
                     **STATED)
        written = path.read_text(encoding="utf-8")
        assert self.SECRET not in written
        assert evidence.REDACTED in written and "failed:" in written

    def test_a_secret_in_the_text_of_an_exception_is_removed(self, tmp_path):
        """A missing executable puts the OS error text into the record. When the thing that is
        missing is named with a credential, that text is where it arrives."""
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", secrets=[self.SECRET], announce=False)
        recorder.run("a binary named with a credential",
                     [f"no-such-binary-{self.SECRET}"], **STATED)
        written = path.read_text(encoding="utf-8")
        assert self.SECRET not in written
        assert evidence.REDACTED in written

    def test_a_secret_nested_in_a_record_is_removed(self, tmp_path):
        """A policy delta holds whatever the field it is about held, at whatever depth. That is
        the one place in the record where an arbitrary value legitimately lives."""
        step = good_step(gateway_record={
            **OK_RECORD,
            "policy_deltas": [{"path": "network.allowed_destinations",
                               "requested": {"auth": {"token": self.SECRET}},
                               "effective": [{"deep": self.SECRET}]}]})
        assert self.SECRET not in self._written(tmp_path, step)

    def test_a_secret_in_a_command_line_is_removed(self, tmp_path):
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", secrets=[self.SECRET], announce=False)
        recorder.run("echo", [sys.executable, "-c", f"print('{self.SECRET}')"], **STATED)
        assert self.SECRET not in (tmp_path / "e.jsonl").read_text(encoding="utf-8")

    def test_a_value_nobody_collected_under_a_secret_named_key_is_removed(self, tmp_path):
        """The layer the value list cannot reach. This value is not in `secrets`, so only the key
        it sits under can save it -- which is the point of having that layer at all."""
        never_collected = "unknown-value-nobody-told-the-recorder-about"
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", secrets=[], announce=False)
        recorder.record(good_step(gateway_record={
            **OK_RECORD,
            "policy_deltas": [{"path": "network.enabled", "requested": True,
                               "effective": {"auth": {"token": never_collected}}}]}))
        written = (tmp_path / "e.jsonl").read_text(encoding="utf-8")
        assert never_collected not in written
        assert evidence.REDACTED in written

    def test_private_key_material_is_removed_wherever_it_appears(self, tmp_path):
        """Recognisable without being known: no value list can contain a key nobody collected."""
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", secrets=[], announce=False)
        recorder.record(good_step(stderr="-----BEGIN OPENSSH PRIVATE KEY----- abc"))
        written = (tmp_path / "e.jsonl").read_text(encoding="utf-8")
        assert "BEGIN OPENSSH PRIVATE KEY" not in written
        assert evidence.REDACTED in written

    def test_a_secret_that_survives_its_own_redaction_stops_the_record(self, tmp_path,
                                                                        monkeypatch):
        """The redactor's failure has to be visible. A record quietly containing a credential is
        worse than no record, so the check is made against a redactor that does nothing."""
        monkeypatch.setattr(evidence, "redact_deep",
                            lambda value, secrets, **kw: value)
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client",
                                     secrets=["a-live-credential-value"], announce=False)
        with pytest.raises(evidence.EvidenceError) as caught:
            recorder.record(good_step(stderr="a-live-credential-value is here"))
        assert "survived its own pass" in str(caught.value)
        # Not "the value is absent from the file" -- an empty file satisfies that by accident.
        # NOTHING was written: no record at all, not a record with a hole in it.
        path = tmp_path / "e.jsonl"
        assert not path.exists() or path.read_text(encoding="utf-8") == ""

    def test_the_same_holds_for_key_material_the_redactor_let_through(self, tmp_path,
                                                                     monkeypatch):
        monkeypatch.setattr(evidence, "redact_deep", lambda value, secrets, **kw: value)
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", secrets=[], announce=False)
        with pytest.raises(evidence.EvidenceError) as caught:
            recorder.record(good_step(stderr="-----BEGIN OPENSSH PRIVATE KEY----- abc"))
        assert "nothing was written" in str(caught.value)
        assert not path.exists() or path.read_text(encoding="utf-8") == ""

    def test_a_working_redactor_does_write(self, tmp_path):
        """The control for the two above: they must fail because the redactor was broken, not
        because the recorder writes nothing under these conditions anyway."""
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", secrets=["a-live-credential-value"],
                                     announce=False)
        recorder.record(good_step(stderr="a-live-credential-value is here"))
        assert path.read_text(encoding="utf-8").strip() != ""

    def test_the_rest_of_the_record_survives(self, tmp_path):
        """The control. A redactor that emptied the document would pass everything above."""
        step = good_step(stderr=f"the token was {self.SECRET}",
                         name="a distinctive step name")
        written = self._written(tmp_path, step)
        assert "a distinctive step name" in written
        assert "EXT-OK" in written


class TestTheCommandLineEntryPoint:

    def test_good_evidence_exits_zero(self, tmp_path, capsys):
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        two_machines(recorder)
        bindings(recorder)
        recorder.record(good_step())
        assert evidence.main([str(path)]) == 0
        assert "about the run it names" in capsys.readouterr().out

    def test_bad_evidence_exits_non_zero_and_separates_the_two_kinds(self, tmp_path, capsys):
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        two_machines(recorder)
        recorder.record(good_step(gateway_record=None))
        recorder.record(good_step(exit_code=9, expected_exit=0))
        assert evidence.main([str(path)]) == 1
        out = capsys.readouterr().out
        assert "failed" in out and "could not be evaluated" in out

    def test_an_unreadable_file_exits_two(self, tmp_path, capsys):
        path = tmp_path / "e.jsonl"
        path.write_text("{not json\n", encoding="utf-8")
        assert evidence.main([str(path)]) == 2


class TestTheGatewayRecordIsClosedToo:
    """`EM3C-EVIDENCE-0006`: it was left open on the reasoning that its shape belongs to the
    gateway. A rule reads it, and what a rule reads is this module's business whoever wrote it."""

    def _written(self, tmp_path, record):
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        recorder.record(good_step(gateway_record=record))
        return path

    def test_a_complete_record_is_accepted(self, tmp_path):
        """The control for everything below."""
        assert len(evidence.load(self._written(tmp_path, dict(OK_RECORD)))) == 1

    @pytest.mark.parametrize("key,value", [
        ("state", 7), ("exit_code", "0"), ("started_at", "soon"), ("cleanup_verified", "maybe"),
        ("gateway", "a-gateway"), ("fingerprint", 1), ("binding", []), ("signature", 3),
    ])
    def test_a_wrongly_typed_key_is_refused_whatever_it_is(self, tmp_path, key, value):
        """`EM3C-EVIDENCE-0013`: the names were derived from the gateway and the types were not,
        so a key nobody had thought about was accepted whatever it held."""
        path = self._written(tmp_path, dict(OK_RECORD))
        document = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        document["gateway_record"][key] = value
        path.write_text(json.dumps(document) + chr(10), encoding="utf-8")
        with pytest.raises(evidence.EvidenceError):
            evidence.load(path)

    def test_every_key_an_answer_may_carry_has_a_declared_type(self):
        assert set(evidence.answer_types()) >= set(evidence.answer_fields())

    def test_a_key_the_gateway_does_not_emit_is_refused(self, tmp_path):
        path = self._written(tmp_path, dict(OK_RECORD))
        document = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        document["gateway_record"]["surprise"] = 1
        path.write_text(json.dumps(document) + "\n", encoding="utf-8")
        with pytest.raises(evidence.EvidenceError) as caught:
            evidence.load(path)
        assert "does not describe" in str(caught.value)

    @pytest.mark.parametrize("key,value", [
        ("cleanup_verified", 7), ("policy_deltas", {}), ("state", 3), ("exit_code", "0"),
        ("requested_policy", []), ("started_at", "soon"),
    ])
    def test_a_wrongly_typed_key_is_refused(self, tmp_path, key, value):
        path = self._written(tmp_path, dict(OK_RECORD))
        document = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        document["gateway_record"][key] = value
        path.write_text(json.dumps(document) + "\n", encoding="utf-8")
        with pytest.raises(evidence.EvidenceError):
            evidence.load(path)

    @pytest.mark.parametrize("policy,expected", [
        ({"network.enabled": False}, "has no network.allowed_destinations"),
        ({"network.enabled": False, "network.allowed_destinations": [], "limits.pids": 3},
         "does not describe"),
        ({"network.enabled": "no", "network.allowed_destinations": []}, "is str"),
        ({"network.enabled": False, "network.allowed_destinations": "example.com"}, "is str"),
    ])
    def test_a_policy_map_that_is_not_one_is_refused(self, tmp_path, policy, expected):
        """The containment rule reads two of these keys, so a map that drifted, or that was
        never a policy, must not arrive at it looking like one."""
        path = self._written(tmp_path, dict(OK_RECORD))
        document = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        document["gateway_record"]["effective_policy"] = policy
        path.write_text(json.dumps(document) + "\n", encoding="utf-8")
        with pytest.raises(evidence.EvidenceError) as caught:
            evidence.load(path)
        assert expected in str(caught.value), str(caught.value)

    def test_a_real_not_found_answer_is_readable(self, tmp_path, real_gateway):
        """The gateway's own answer when there is no such run. Read whole, not approximately."""
        _status, body = real_gateway.an_absent_run()
        assert len(evidence.load(self._written(tmp_path, body))) == 1

    def test_no_record_at_all_is_readable(self, tmp_path):
        """Nothing came back, so nothing is attached -- and the client says so beside it."""
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        recorder.record(good_step(gateway_record=None,
                                  answer=accepted(http_status=None, verified=False,
                                                  refusal="nothing came back")))
        assert len(evidence.load(path)) == 1

    def test_what_the_reader_accepts_is_what_a_real_answer_carries(self, real_answer):
        """`EM3C-E3-CLASSIFY-0001`. The old version of this test held the declaration against
        `RunRecord.public()` -- the object the gateway keeps, not the answer a client gets -- so
        it passed while every real answer was refused. It is held against a real answer now."""
        assert set(real_answer) - set(evidence.answer_fields()) == set(), \
            sorted(set(real_answer) - set(evidence.answer_fields()))

    def test_a_real_not_found_answer_carries_exactly_this(self, real_gateway):
        """What the gateway writes when there is no run, held against a real endpoint."""
        status, body = real_gateway.an_absent_run()
        assert status == 404
        from agentnode_sdk.gateway.protocol import ERROR_FIELDS, STAMP_FIELDS

        assert set(body) == set(ERROR_FIELDS) | set(STAMP_FIELDS), sorted(body)
        assert set(body) - set(evidence.answer_fields()) == set()

    def test_the_reader_takes_a_real_answer_whole(self, tmp_path, real_answer):
        """End to end: a real answer, recorded, written, read back, with nothing removed."""
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        recorder.record(good_step(gateway_record=dict(real_answer)))
        back = evidence.load(path)[0]["gateway_record"]
        assert set(back) == set(real_answer)
        assert back["binding"] == real_answer["binding"]
        assert back["signature"] == real_answer["signature"]

    def test_the_declared_policy_map_is_the_one_the_gateway_pins(self):
        from agentnode_sdk.gateway.policy_paths import policy_shape

        shape = evidence._policy_shape_for_reading()
        declared = set(shape["required"]) | set(shape["optional"])
        assert set(policy_shape(None)) == declared, sorted(set(policy_shape(None)) ^ declared)


class TestTheFieldsThatUsedToBeUnread:
    """`EM3C-EVIDENCE-0009`: job_id, client_id, policy_deltas and cleanup_verified were recorded
    and read by nothing, so the record could say anything in them and no rule would notice."""

    def test_a_job_id_that_disagrees_with_the_gateway_fails(self, tmp_path):
        found = _recorded(tmp_path, [good_step(
            job_id="job-1", gateway_record=resigned(job_id="job-2"))])
        assert "is about job job-1" in messages(found)

    def test_a_gateway_job_the_step_never_recorded_is_an_evidence_error(self, tmp_path):
        found = _recorded(tmp_path, [good_step(
            job_id="", gateway_record=resigned(job_id="job-2"))])
        assert "never compared" in messages(found)

    def test_deltas_that_are_not_the_gateway_s_deltas_fail(self, tmp_path):
        delta = {"path": "network.enabled", "requested": True, "effective": False}
        found = _recorded(tmp_path, [good_step(
            policy_deltas=[delta],
            gateway_record=resigned(policy_deltas=[],
                            request_policy_sha256="a" * 64,
                            effective_policy_sha256="a" * 64))])
        assert "is not the narrowing the gateway reported" in messages(found)

    def test_cleanup_that_disagrees_with_the_gateway_fails(self, tmp_path):
        found = _recorded(tmp_path, [good_step(
            cleanup_verified=True, gateway_record=resigned(cleanup_verified=False))])
        assert "and the gateway says" in messages(found)

    def test_two_clients_in_one_record_fail(self, tmp_path):
        found = _recorded(tmp_path, [good_step(client_id="client-1"),
                                     good_step(client_id="client-2")])
        assert "more than one client" in messages(found)

    def test_one_client_and_agreeing_fields_are_accepted(self, tmp_path):
        """The control for all five."""
        assert _recorded(tmp_path, [good_step(), good_step()]) == []


class TestAnIdentityBelongsToTheChannelThatCarriedIt:
    """`EM3C-EVIDENCE-0011`: the identity was indexed by the label inside it, and nothing made
    that label agree with the channel the step was recorded over. Both machines could therefore
    describe themselves over one channel and the record would still show two."""

    def _record(self, tmp_path, *identities):
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        for index, (channel, machine) in enumerate(identities):
            recorder.record(a_step(name=f"identity {index + 1}", role=channel, argv=["(identity)"],
                                   started_at=1.0 + index, ended_at=1.1 + index, exit_code=0,
                                   machine=machine))
        recorder.record(a_step(name="a crossing", role="client", argv=["x"], started_at=3.0,
                               ended_at=3.1, exit_code=0, sentinel=crossing("client")))
        recorder.record(a_step(name="the other way", role="gateway", argv=["x"], started_at=3.2,
                               ended_at=3.3, exit_code=0, sentinel=crossing("gateway")))
        return evidence.verify_two_machines(evidence.load(path))

    CLIENT = ("client", identity("client", "c" * 64, "cf" * 32, "Windows"))
    GATEWAY = ("gateway", identity("gateway", "g" * 64, "gf" * 32, "Linux"))

    def test_each_identity_on_its_own_channel_is_accepted(self, tmp_path):
        """The control for everything below."""
        assert self._record(tmp_path, self.CLIENT, self.GATEWAY) == []

    def test_the_gateway_describing_itself_over_the_client_channel_fails(self, tmp_path):
        found = self._record(tmp_path, self.CLIENT,
                             ("client", identity("gateway", "g" * 64, "gf" * 32, "Linux")))
        assert "recorded over the client channel" in messages(found)

    def test_the_client_describing_itself_over_the_gateway_channel_fails(self, tmp_path):
        found = self._record(tmp_path,
                             ("gateway", identity("client", "c" * 64, "cf" * 32, "Windows")),
                             self.GATEWAY)
        assert "recorded over the gateway channel" in messages(found)

    def test_both_identities_over_one_channel_do_not_make_two_machines(self, tmp_path):
        """The defect exactly: one machine, two nested labels, and a record that used to read as
        two machines."""
        found = self._record(tmp_path, self.CLIENT,
                             ("client", identity("gateway", "g" * 64, "gf" * 32, "Linux")))
        assert found, "one machine wearing two labels was accepted as two"
        assert "fewer than two machines" in messages(found) \
            or "recorded over the client channel" in messages(found)

    def test_a_second_identity_for_one_machine_is_not_silently_taken(self, tmp_path):
        """It used to replace the first, so a later, weaker or wrong identity won with no
        finding at all."""
        found = self._record(
            tmp_path, self.CLIENT, self.GATEWAY,
            ("gateway", identity("gateway", "9" * 64, "99" * 32, "Linux")))
        assert "a second identity claims to be the gateway" in messages(found)

    def test_an_identity_whose_step_names_no_channel_fails(self, tmp_path):
        """A step with no role is refused for that on its own; here it must not be able to carry
        an identity past this rule either."""
        found = self._record(tmp_path, self.CLIENT, ("", self.GATEWAY[1]))
        assert found


class TestASentinelsRequestIsRead:
    """`EM3C-EVIDENCE-0009`: `in_request` decided which way a value travelled and was a boolean
    the recorder asserted. What the client sent is in the record, and the claim is read from it."""

    PAYLOAD = "print('E3-FROM-CLIENT {v}')"

    def _pair(self, tmp_path, first, second):
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        recorder.record(a_step(name="client identity", role="client", argv=["x"],
                               started_at=1.0, ended_at=1.1, exit_code=0,
                               machine=identity("client", "c" * 64, "cf" * 32, "Windows")))
        recorder.record(a_step(name="gateway identity", role="gateway", argv=["x"],
                               started_at=1.2, ended_at=1.3, exit_code=0,
                               machine=identity("gateway", "g" * 64, "gf" * 32, "Linux")))
        for index, sentinel in enumerate((first, second)):
            recorder.record(a_step(name=f"sentinel {index + 1}", role="client", argv=["x"],
                                   started_at=2.0 + index, ended_at=2.1 + index, exit_code=0,
                                   sentinel=sentinel))
        return evidence.verify_two_machines(evidence.load(path))

    def _sentinel(self, made_on, value, payload):
        import hashlib

        return {"generated_on": made_on, "carried_over": "agentnode-job",
                "confirmed_over": "ssh",
                "value_sha256": hashlib.sha256(value.encode()).hexdigest(),
                "request_text": payload,
                "in_request": made_on == "client", "in_response": True,
                "in_other_channel": True, "matched": True}

    def test_a_real_crossing_is_accepted(self, tmp_path):
        """The control. The client's value is in the payload; the gateway's is not."""
        mine, theirs = "a1b2c3d4e5f60718", "99887766554433221100"
        assert self._pair(tmp_path,
                          self._sentinel("client", mine, self.PAYLOAD.format(v=mine)),
                          self._sentinel("gateway", theirs, self.PAYLOAD.format(v=mine))) == []

    def test_a_client_value_that_is_not_in_what_was_sent_fails(self, tmp_path):
        mine, theirs = "a1b2c3d4e5f60718", "99887766554433221100"
        found = self._pair(tmp_path,
                           self._sentinel("client", mine, self.PAYLOAD.format(v="something-else")),
                           self._sentinel("gateway", theirs, self.PAYLOAD.format(v=mine)))
        assert "the recorded request says the opposite" in messages(found)

    def test_a_gateway_value_that_was_in_what_was_sent_fails(self, tmp_path):
        """The client could have produced it, so it establishes nothing about the gateway."""
        mine, theirs = "a1b2c3d4e5f60718", "99887766554433221100"
        found = self._pair(tmp_path,
                           self._sentinel("client", mine, self.PAYLOAD.format(v=mine)),
                           self._sentinel("gateway", theirs, self.PAYLOAD.format(v=theirs)))
        assert "the recorded request says the opposite" in messages(found)

    def test_a_sentinel_with_no_record_of_the_request_cannot_be_read(self, tmp_path):
        mine, theirs = "a1b2c3d4e5f60718", "99887766554433221100"
        found = self._pair(tmp_path,
                           self._sentinel("client", mine, ""),
                           self._sentinel("gateway", theirs, self.PAYLOAD.format(v=mine)))
        assert "records nothing of what the client sent" in messages(found)

    def test_a_sentinel_whose_value_is_not_a_digest_cannot_be_read(self, tmp_path):
        mine, theirs = "a1b2c3d4e5f60718", "99887766554433221100"
        broken = {**self._sentinel("client", mine, self.PAYLOAD.format(v=mine)),
                  "value_sha256": "not-a-digest"}
        found = self._pair(tmp_path, broken,
                           self._sentinel("gateway", theirs, self.PAYLOAD.format(v=mine)))
        assert "is not a digest" in messages(found)


class TestTheOutsideOfAnAnswerIsTiedToItsInside:
    """`EM3C-E3-CLASSIFY-0001`. Every case starts from a REAL answer and changes one thing about
    it. The binding is not recomputed, because the point is that changing anything the gateway
    signed over stops the answer describing itself."""

    def _found(self, tmp_path, record, **step):
        return _recorded(tmp_path, [good_step(gateway_record=record, run_id=RUN(),
                                              container=container_for(RUN()), **step)])

    def test_a_real_answer_passes(self, tmp_path, real_answer):
        """The control for every case below."""
        assert self._found(tmp_path, dict(real_answer)) == []

    @pytest.mark.parametrize("field", [
        "run_id", "job_id", "artifact_sha256", "request_policy_sha256",
        "effective_policy_sha256", "stdout",
    ])
    def test_changing_a_signed_field_stops_it_describing_itself(self, tmp_path, real_answer,
                                                                field):
        answer = {**real_answer, field: "something else entirely"}
        found = self._found(tmp_path, answer)
        assert "do not produce the binding it carries" in messages(found), messages(found)

    @pytest.mark.parametrize("field", [
        "run_id", "job_id", "artifact_sha256", "request_policy_sha256",
        "effective_policy_sha256", "stdout",
    ])
    def test_leaving_a_signed_field_out_stops_it_too(self, tmp_path, real_answer, field):
        answer = {k: v for k, v in real_answer.items() if k != field}
        found = self._found(tmp_path, answer)
        assert "do not produce the binding it carries" in messages(found), messages(found)

    def test_a_binding_from_another_answer_is_refused(self, tmp_path, real_gateway, real_answer):
        """Exchange, rather than change: a binding that is real and belongs elsewhere."""
        other = real_gateway.a_finished_run()
        assert other["run_id"] != real_answer["run_id"]
        found = self._found(tmp_path, {**real_answer, "binding": other["binding"]})
        assert "do not produce the binding it carries" in messages(found)

    #: A different value of the type the gateway declares for that field, so what fails is the
    #: tie and not the reader refusing a value the answer could never have carried.
    OTHERWISE = {"state": "cancelled", "exit_code": 7, "termination_reason": "cancelled",
                 "native_status": 137, "native_platform": "linux-container",
                 "cleanup_verified": False, "refusal": "something else", "stderr": "noise",
                 "started_at": 1.0, "finished_at": 2.0}

    @pytest.mark.parametrize("field", list(OTHERWISE))
    def test_changing_what_the_answer_says_happened_is_refused(self, tmp_path, real_answer,
                                                               field):
        """`EM3C-EVIDENCE-0020`: the signature covered the identifiers, both policy digests and a
        digest of stdout -- so everything an answer said about what HAPPENED could be changed on
        the way to the client and the binding still recomputed to what had been signed."""
        assert real_answer.get(field) != self.OTHERWISE[field], field
        changed = {**real_answer, field: self.OTHERWISE[field]}
        found = self._found(tmp_path, changed)
        assert "do not produce the binding it carries" in messages(found), messages(found)

    def test_removing_what_the_answer_says_happened_is_refused(self, tmp_path, real_answer):
        """Absent is not the same as empty."""
        changed = {k: v for k, v in real_answer.items() if k != "cleanup_verified"}
        found = self._found(tmp_path, changed)
        assert "do not produce the binding it carries" in messages(found)

    def test_the_production_client_refuses_it_too(self, tmp_path, real_gateway, real_answer):
        """Not only this reader: the client that receives an answer refuses the same change."""
        from agentnode_sdk.gateway.client import GatewayClientError

        changed = {**real_answer, "termination_reason": "timeout"}
        with pytest.raises(GatewayClientError):
            real_gateway.accepted(changed)
        assert real_gateway.accepted(dict(real_answer)) == real_answer

    def test_an_answer_with_no_binding_is_refused(self, tmp_path, real_answer):
        answer = {k: v for k, v in real_answer.items() if k != "binding"}
        found = self._found(tmp_path, answer)
        assert "carries no binding" in messages(found)

    def test_an_answer_with_no_signature_is_refused(self, tmp_path, real_answer):
        answer = {k: v for k, v in real_answer.items() if k != "signature"}
        found = self._found(tmp_path, answer)
        assert "a binding and no signature" in messages(found)

    @pytest.mark.parametrize("field", ["gateway", "fingerprint", "protocol"])
    def test_an_answer_the_gateway_did_not_stamp_is_refused(self, tmp_path, real_answer, field):
        answer = {k: v for k, v in real_answer.items() if k != field}
        found = self._found(tmp_path, answer)
        assert f"carries no {field}" in messages(found)

    def test_a_fingerprint_that_is_not_this_identity_s_is_refused(self, tmp_path, real_answer):
        found = self._found(tmp_path, {**real_answer, "fingerprint": "0" * 64})
        assert "not the one this gateway identity and version produce" in messages(found)

    def test_a_stamp_naming_another_gateway_than_the_binding_is_refused(self, tmp_path,
                                                                        real_answer):
        said = {**real_answer["gateway"], "gateway_id": "f" * 32}
        found = self._found(tmp_path, {**real_answer, "gateway": said,
                                       "fingerprint": _fingerprint_of(said)})
        assert "the signed binding says" in messages(found)

    def test_another_protocol_is_refused(self, tmp_path, real_answer):
        found = self._found(tmp_path, {**real_answer, "protocol": "em3c/999"})
        assert "not one conversation" in messages(found)

    def test_an_answer_the_client_did_not_accept_is_refused(self, tmp_path, real_answer):
        found = self._found(tmp_path, dict(real_answer),
                            answer=accepted(verified=False, refusal="it was discarded"))
        assert "did not accept this answer" in messages(found)

    def test_a_signed_answer_with_nothing_said_about_it_is_refused(self, tmp_path, real_answer):
        found = self._found(tmp_path, dict(real_answer), answer=None)
        assert "nothing says whether the client" in messages(found)

    def test_a_resigned_answer_is_one_the_gateway_would_send(self, tmp_path, real_gateway):
        """The control for `resigned`: what it returns is accepted by the production client,
        which is what makes it a fixture rather than an object of this file's making."""
        variant = real_gateway.resigned(OK_RECORD, stdout="something else")
        assert real_gateway.accepted(variant) == variant
        assert self._found(tmp_path, variant,
                           answer=accepted(over=variant)) == []

    def test_a_signature_swapped_after_it_was_accepted_is_refused(self, tmp_path, real_gateway):
        """`EM3C-EVIDENCE-0013`: recomputing the binding cannot see this, because the signature
        is not part of what the binding is over. The client sealed the whole answer."""
        other = real_gateway.resigned(OK_RECORD, stdout="a different result")
        assert other["signature"] != OK_RECORD["signature"]
        found = self._found(tmp_path, {**OK_RECORD, "signature": other["signature"]},
                            answer=accepted(over=OK_RECORD))
        assert "changed after it was accepted" in messages(found)

    @pytest.mark.parametrize("field", ["state", "refusal", "started_at"])
    def test_a_field_outside_the_binding_changed_afterwards_is_refused(self, tmp_path, field):
        """The binding covers six fields. The seal covers all of them."""
        changed = {**OK_RECORD, field: 0 if field == "started_at" else "something else"}
        found = self._found(tmp_path, changed, answer=accepted(over=OK_RECORD))
        assert "changed after it was accepted" in messages(found)

    def test_an_accepted_answer_with_no_seal_is_an_evidence_error(self, tmp_path):
        found = self._found(tmp_path, dict(OK_RECORD),
                            answer=accepted(verified_sha256=""))
        assert "still the answer it accepted" in messages(found)

    def test_two_gateways_in_one_record_are_refused(self, tmp_path, real_gateway, real_answer):
        """Two real answers, from two real gateways, in one file."""
        import tempfile

        from tests.real_answers import RealGateway

        another = RealGateway(tempfile.mkdtemp())
        try:
            theirs = another.a_finished_run()
        finally:
            another.close()
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        two_machines(recorder)
        bindings(recorder)
        for one in (real_answer, theirs):
            recorder.record(good_step(gateway_record=dict(one), run_id=one["run_id"],
                                      container=container_for(one["run_id"])))
        assert "more than one gateway" in messages(evidence.check_file(path))


class TestATimeoutIsAReasonNotANumber:
    """`EM3C-E4-CLASSIFY-0001`: a run its limit ended was reported as exit code -1, a Windows
    client observed 4294967295, and the two had to be called equal for anything to work."""

    def _found(self, tmp_path, record, **step):
        return _recorded(tmp_path, [good_step(gateway_record=record, run_id=RUN(),
                                              container=container_for(RUN()),
                                              answer=accepted(over=record), **step)])

    def test_a_real_timed_out_run_is_read_as_one(self, tmp_path, real_gateway):
        """A real submission the sandbox stopped, answered by the real gateway."""
        answer = real_gateway.a_run_its_limit_ended()
        assert answer["termination_reason"] == "timeout"
        assert answer["exit_code"] is None
        assert answer["native_status"] is not None and answer["native_platform"]
        found = _recorded(tmp_path, [good_step(
            gateway_record=answer, run_id=answer["run_id"],
            container=container_for(answer["run_id"]), answer=accepted(over=answer),
            expect_timeout=True, expected_exit=None,
            job_id=answer["job_id"], cleanup_verified=answer["cleanup_verified"],
            request_policy_sha256=answer["request_policy_sha256"],
            effective_policy_sha256=answer["effective_policy_sha256"],
            policy_deltas=answer["policy_deltas"])])
        assert found == [], messages(found)

    def test_a_run_that_exited_is_not_a_timeout(self, tmp_path, real_answer):
        found = self._found(tmp_path, dict(real_answer), expect_timeout=True)
        assert "the gateway records it as 'exited'" in messages(found)

    def test_a_reason_this_build_does_not_know_is_an_evidence_error(self, tmp_path):
        found = self._found(tmp_path, resigned(termination_reason="vanished"))
        assert "not a reason this build knows" in messages(found)

    def test_a_run_cannot_be_stopped_and_have_exited(self, tmp_path):
        found = self._found(tmp_path, resigned(termination_reason="timeout", exit_code=0))
        assert "Nothing that was stopped chose a status" in messages(found)

    def test_a_native_status_must_say_whose_it_is(self, tmp_path):
        found = self._found(tmp_path, resigned(native_status=137, native_platform=""))
        assert "without saying which platform" in messages(found)

    def test_a_stopped_run_still_has_to_show_its_cleanup(self, tmp_path):
        found = self._found(tmp_path,
                            resigned(termination_reason="timeout", exit_code=None,
                                     cleanup_verified=None),
                            expect_timeout=True, expected_exit=None)
        assert "not a reason for what was left behind to be unknown" in messages(found)

    def test_no_number_can_stand_in_for_the_reason(self, tmp_path):
        """The defect exactly: an answer carrying the old sentinel and nothing else."""
        found = self._found(tmp_path, resigned(exit_code=-1), expect_timeout=True)
        assert "the gateway records it as 'exited'" in messages(found)


class TestAHashOfNothingIsNotAnIdentity:
    """`EM3C-EVIDENCE-0005`: a machine that could not read its own identity and hashed the
    nothing it got produced a perfectly ordinary-looking digest, which passed the presence
    check -- and two machines that failed differently could even look like two."""

    EMPTY = evidence.DIGEST_OF_NOTHING

    def _pair(self, tmp_path, **client):
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        recorder.record(a_step(name="client identity", role="client", argv=["(identity)"],
                               started_at=1.0, ended_at=1.1, exit_code=0,
                               machine=identity("client", "c" * 64, "cf" * 32, "Windows",
                                                **client)))
        recorder.record(a_step(name="gateway identity", role="gateway", argv=["(identity)"],
                               started_at=1.2, ended_at=1.3, exit_code=0,
                               machine=identity("gateway", "g" * 64, "gf" * 32, "Linux")))
        return evidence.verify_two_machines(evidence.load(path))

    def test_the_known_digest_of_nothing_is_what_it_says_it_is(self):
        import hashlib

        assert hashlib.sha256(b"").hexdigest() == self.EMPTY

    @pytest.mark.parametrize("field", ["host_sha256", "filesystem_sha256"])
    def test_a_hashed_empty_identity_is_an_evidence_error(self, tmp_path, field):
        found = self._pair(tmp_path, **{field: self.EMPTY})
        assert "digest of an empty value" in messages(found)

    def test_a_real_pair_is_still_accepted(self, tmp_path):
        """The control: without it a rule refusing every identity would pass both cases above."""
        found = self._pair(tmp_path)
        assert not [f for f in found if "empty value" in f.message], messages(found)


class TestThePolicyBindingIsChecked:
    """EM3C-EVIDENCE-0001: the binding was captured and never read, so a missing, stale or
    divergent one produced no finding at all. A binding nobody checks is a field, not a rule."""

    def _with(self, tmp_path, binding, times=2):
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        two_machines(recorder)
        for n in range(times):
            recorder.record(a_step(
                name=f"binding {n + 1}", role="gateway", argv=["(egress --verbose)"],
                started_at=2.0 + n, ended_at=2.1 + n, exit_code=0, binding=dict(binding)))
        return evidence.check_file(path)

    def test_a_record_with_no_binding_at_all_is_an_evidence_error(self, tmp_path):
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        two_machines(recorder)
        found = evidence.check_file(path)
        assert "no operator-policy binding was captured" in messages(found)

    def test_a_binding_captured_once_was_never_compared(self, tmp_path):
        found = self._with(tmp_path, BINDING, times=1)
        assert "never compared" in messages(found)

    @pytest.mark.parametrize("field", ["generation", "policy_digest", "configured_digest",
                                       "runtime", "backend", "conformance_digest"])
    def test_an_empty_required_field_is_an_evidence_error(self, tmp_path, field):
        found = self._with(tmp_path, {**BINDING, field: ""})
        assert f"records no {field}" in messages(found)

    def test_a_digest_that_is_not_one_fails(self, tmp_path):
        found = self._with(tmp_path, {**BINDING, "policy_digest": "not-a-digest"})
        assert "is not a digest" in messages(found)

    def test_digests_that_do_not_agree_fail(self, tmp_path):
        found = self._with(tmp_path, {**BINDING, "digests_agree": "False"})
        assert "do not agree" in messages(found)

    def test_a_complete_binding_produces_no_binding_finding(self, tmp_path):
        """The control. Without it, a rule that rejected every binding would pass the rest."""
        found = self._with(tmp_path, BINDING)
        assert not [f for f in found if "binding" in f.message], messages(found)

    # -- what the allowlist itself says -------------------------------------------------------
    # EM3C-EVIDENCE-0003: it was captured and never read, so it could contradict the mode beside
    # it, or be in a spelling the digest was never taken over, and no rule noticed.

    def test_a_restricted_policy_with_its_hosts_is_accepted(self, tmp_path):
        """The control for the allowlist rules below."""
        found = self._with(tmp_path, RESTRICTED)
        assert not [f for f in found if "allowlist" in f.message], messages(found)

    def test_a_restricted_policy_with_an_empty_list_fails(self, tmp_path):
        found = self._with(tmp_path, dict(RESTRICTED, allowlist=[]))
        assert "the list is empty" in messages(found)

    @pytest.mark.parametrize("mode", ["none", "unrestricted"])
    def test_hosts_listed_under_a_mode_that_does_not_use_them_fail(self, tmp_path, mode):
        found = self._with(tmp_path, dict(BINDING, mode=mode, allowlist=["api.example.com"]))
        assert "is not the policy in force" in messages(found)

    def test_a_mode_nobody_knows_is_an_evidence_error(self, tmp_path):
        found = self._with(tmp_path, dict(BINDING, mode="permissive"))
        assert "names no network mode" in messages(found)

    def test_a_missing_mode_is_refused_before_any_rule_reads_it(self, tmp_path):
        binding = {k: v for k, v in BINDING.items() if k != "mode"}
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        with pytest.raises(evidence.EvidenceError) as caught:
            recorder.record(a_step(name="b", role="gateway", argv=["x"], started_at=1.0,
                                   ended_at=1.1, exit_code=0, binding=binding))
        assert "mode" in str(caught.value)

    @pytest.mark.parametrize("listed,expected", [
        (["API.example.com"], "not in the form the policy digest"),
        (["  api.example.com"], "not in the form the policy digest"),
        (["api.example.com", "api.example.com"], "names a host twice"),
        (["z.example.com", "a.example.com"], "not in canonical order"),
        (["api.example.com", ""], "contains an empty entry"),
    ])
    def test_a_list_that_is_not_the_one_the_digest_covers_fails(self, tmp_path, listed, expected):
        found = self._with(tmp_path, dict(RESTRICTED, allowlist=listed))
        assert expected in messages(found)

    def test_an_allowlist_that_changed_without_the_digest_fails(self, tmp_path):
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        two_machines(recorder)
        for index, listed in enumerate([["a.example.com"], ["a.example.com", "b.example.com"]]):
            recorder.record(a_step(
                name=f"binding {index + 1}", role="gateway", argv=["(egress --verbose)"],
                started_at=2.0 + index, ended_at=2.1 + index, exit_code=0,
                binding=dict(RESTRICTED, allowlist=listed)))
        assert "allowlist changed while the policy digest" in messages(evidence.check_file(path))


class TestAJobIsInsideThePolicyInForce:
    """EM3C-EVIDENCE-0003: the binding was checked against itself and the jobs against the
    gateway's own answer, and nothing held the two together. The digests cannot be compared --
    a job digest is over what that job was granted, an operator digest over what the machine
    allows anyone -- so what binds them is containment."""

    def _run_under(self, tmp_path, binding, effective, *, before=True):
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        two_machines(recorder)
        if before:
            for index in range(2):
                recorder.record(a_step(
                    name=f"binding {index + 1}", role="gateway", argv=["(egress --verbose)"],
                    started_at=2.0 + index, ended_at=2.1 + index, exit_code=0,
                    binding=dict(binding)))
        record = dict(OK_RECORD)
        if effective is None:
            record.pop("effective_policy")
        else:
            record["effective_policy"] = effective
        recorder.record(good_step(gateway_record=record))
        return evidence.check_file(path)

    def test_a_job_with_no_network_under_a_policy_that_allows_none_is_accepted(self, tmp_path):
        """The control. Without it a rule that failed every job would pass everything below."""
        found = self._run_under(tmp_path, BINDING, NO_NETWORK)
        assert found == [], messages(found)

    def test_a_job_granted_network_under_a_policy_that_reaches_nothing_fails(self, tmp_path):
        found = self._run_under(tmp_path, BINDING,
                                dict(NO_NETWORK, **{"network.enabled": True}))
        assert "under a policy that reaches nothing" in messages(found)

    def test_a_job_granted_a_host_the_policy_does_not_list_fails(self, tmp_path):
        binding = dict(BINDING, mode="restricted", allowlist=["api.example.com"])
        found = self._run_under(tmp_path, binding, {
            **NO_NETWORK, "network.enabled": True,
            "network.allowed_destinations": ["evil.example.com"]})
        assert "evil.example.com" in messages(found)
        assert "does not permit" in messages(found)

    def test_a_job_granted_a_listed_host_is_accepted(self, tmp_path):
        binding = dict(BINDING, mode="restricted", allowlist=["api.example.com"])
        found = self._run_under(tmp_path, binding, {
            **NO_NETWORK, "network.enabled": True,
            "network.allowed_destinations": ["api.example.com"]})
        assert found == [], messages(found)

    def test_a_job_granted_everything_under_a_named_list_fails(self, tmp_path):
        """`None` is the shape that means unrestricted, and it is deliberately not an empty set.
        Under a policy that names hosts it is wider than the policy, not narrower."""
        binding = dict(BINDING, mode="restricted", allowlist=["api.example.com"])
        found = self._run_under(tmp_path, binding, {
            **NO_NETWORK, "network.enabled": True, "network.allowed_destinations": None})
        assert "granted every destination" in messages(found)

    def test_a_record_that_carries_only_a_digest_of_its_policy_is_an_evidence_error(self,
                                                                                    tmp_path):
        found = self._run_under(tmp_path, BINDING, None)
        assert "and not the policy itself" in messages(found)

    def test_a_policy_that_does_not_say_what_it_granted_never_reaches_the_rule(self, tmp_path):
        """It is refused when the record is READ, which is earlier and harder than a finding.
        The rule has no branch for it, because a branch nothing can reach reads as cover."""
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        recorder.record(good_step())
        document = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        document["gateway_record"]["effective_policy"] = {"limits.memory_mb": 512}
        broken = tmp_path / "broken.jsonl"
        broken.write_text(json.dumps(document) + "\n", encoding="utf-8")
        with pytest.raises(evidence.EvidenceError) as caught:
            evidence.load(broken)
        assert "has no network.enabled" in str(caught.value)

    def test_a_job_with_no_binding_before_it_cannot_be_placed(self, tmp_path):
        found = self._run_under(tmp_path, BINDING, NO_NETWORK, before=False)
        assert "no operator policy was captured before it" in messages(found)


class TestTheOperatorIsToldWhatTheRecordKeeps:
    """EM3C-V8-DECISION-0001 chose to keep real command output and to state the exposure that
    comes with it -- and decided that a docstring and a documentation page are not enough,
    because the risk arrives when commands run."""

    def test_starting_a_recording_shows_the_notice(self, tmp_path):
        seen = []
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", announce=seen.append)
        assert recorder.announced is True
        assert len(seen) == 1

    def test_the_notice_names_what_is_not_removed(self, tmp_path):
        seen = []
        evidence.Recorder(tmp_path / "e.jsonl", role="client", announce=seen.append)
        text = seen[0]
        assert "nobody named" in text
        assert "unremarkable" in text
        assert "nothing is written at all" in text

    def test_the_notice_does_not_promise_more_than_it_can(self, tmp_path):
        """A notice that claimed everything was removed would be worse than none."""
        seen = []
        evidence.Recorder(tmp_path / "e.jsonl", role="client", announce=seen.append)
        text = seen[0].lower()
        for overclaim in ("all secrets are removed", "no credential", "guarantee"):
            assert overclaim not in text

    def test_the_notice_tells_its_reader_nothing_to_do(self, tmp_path):
        """`EM3C-E5-CLASSIFY-0001`, F-UNTRUSTED-OPERATIVE-INSTRUCTION. A run captures its own
        output, so this notice lands inside the evidence. Anything imperative in it is then
        operative wording sitting in untrusted material, and two independent reviews stopped and
        named it."""
        seen = []
        evidence.Recorder(tmp_path / "e.jsonl", role="client", announce=seen.append)
        text = seen[0].lower()
        for directive in ("treat the", "you should", "you must", "make sure", "do not ",
                          "please ", "note that", "ensure ", "consider "):
            assert directive not in text, directive
        assert "carries the same exposure" in text

    def test_suppressing_the_notice_is_recorded_as_suppressed(self, tmp_path):
        """A run that did not show it says so, rather than looking like one that did."""
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", announce=False)
        assert recorder.announced is False


class TestUnexpectedOutputStaysVisible:
    """The reason Option A was chosen over filtering: an anomaly is the case most worth seeing,
    and a per-step allowlist would turn exactly that into a blank."""

    def test_output_nobody_predicted_is_kept(self, tmp_path):
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", announce=False)
        surprising = "Traceback: something nobody wrote a rule for, at 0xdeadbeef"
        recorder.record(good_step(stdout=surprising))
        assert surprising in (tmp_path / "e.jsonl").read_text(encoding="utf-8")

    def test_a_stream_is_not_truncated_to_a_shape(self, tmp_path):
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", announce=False)
        long_output = ("line %d" + chr(10)) * 40
        recorder.record(good_step(stdout=long_output))
        written = (tmp_path / "e.jsonl").read_text(encoding="utf-8")
        assert written.count("line %d") == 40
