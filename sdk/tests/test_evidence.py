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


RUN = "beeac323964d45d8b1c8ef90fb51bc30"
OTHER_RUN = "d06a38ad43e54e3ab39ec18a18dcbbe6"
OK_RECORD = {
    "run_id": RUN,
    "request_policy_sha256": "a" * 64,
    "effective_policy_sha256": "a" * 64,
    "policy_deltas": [],
    "cleanup_verified": True,
    "state": "finished",
    "refusal": "",
}


STATED = {"expected_exit": None, "expected_refusal": "", "expect_output": False,
          "expect_cleanup": False, "expect_container_gone": False}


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


def crossing(made_on, value_sha256, **overrides):
    """A sentinel that crosses properly. `in_request` is what makes the origin checkable: a value
the client sent is the client's, one it never sent is not."""
    sentinel = {"generated_on": made_on,
                "carried_over": "agentnode-job", "confirmed_over": "ssh",
                "value_sha256": value_sha256,
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
        sentinel=crossing("client", "1" * 64)))
    recorder.record(a_step(
        name="a sentinel made on the gateway, read back over the client",
        role="gateway", argv=["(sentinel)"], started_at=1.6, ended_at=1.7, exit_code=0,
        sentinel=crossing("gateway", "2" * 64)))


BINDING = {"generation": "1", "policy_digest": "a" * 64, "configured_digest": "a" * 64,
           "digests_agree": "True", "required_properties": "container_isolation",
           "allowlist": [], "runtime": "docker 29.8.0", "backend": "docker",
           "conformance_digest": "b" * 64}


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
    """One step that is complete, self-consistent, and about the run it names."""
    values = dict(
        name="run a job", role="client",
        argv=["agentnode", "remote", "run", "job.py"],
        started_at=2.0, ended_at=3.0, exit_code=0, expected_exit=0,
        stdout="EXT-OK\n", stderr="",
        run_id=RUN, job_id="job-1", client_id="client-1",
        request_policy_sha256="a" * 64, effective_policy_sha256="a" * 64,
        policy_deltas=[], container=f"agentnode-em3c-{RUN[:12]}-abc",
        cleanup_verified=True, expect_output=True,
        gateway_record=dict(OK_RECORD),
    )
    values.update(overrides)
    return a_step(**values)


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
            gateway_record={**OK_RECORD, "state": "refused",
                            "refusal": "this request has already been used (replay)"})
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

    @pytest.mark.parametrize("field", ["name", "role", "argv", "started_at", "ended_at",
                                       "exit_code"])
    def test_a_missing_mandatory_field_is_refused(self, tmp_path, field):
        document = {"schema": evidence.SCHEMA, "name": "x", "role": "client", "argv": [],
                    "started_at": 1.0, "ended_at": 2.0, "exit_code": 0}
        del document[field]
        path = tmp_path / "e.jsonl"
        path.write_text(json.dumps(document) + "\n", encoding="utf-8")
        with pytest.raises(evidence.EvidenceError) as caught:
            evidence.load(path)
        assert field in str(caught.value)

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
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", announce=False)
        with pytest.raises(evidence.EvidenceError):
            recorder.run("x", [sys.executable, "-c", "pass"], **{field: 0})

    def test_a_failing_command_records_its_real_status_not_the_expected_one(self, tmp_path):
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", announce=False)
        step = recorder.run("fails", [sys.executable, "-c", "raise SystemExit(3)"],
                            expected_exit=0)
        assert step.exit_code == 3
        assert step.expected_exit == 0

    def test_the_two_kinds_of_field_do_not_overlap(self):
        assert not set(evidence.EXPECTATIONS) & set(evidence.OBSERVED_BY_RUNNING)


class TestTheRulesFireOnRecordedSteps:
    """Each rule, on the real path. The previous file proved these only against hand-built dicts."""

    def test_a_wrong_exit_code_fails(self, tmp_path):
        found = _recorded(tmp_path, [good_step(exit_code=1, expected_exit=0)])
        assert evidence.FAIL in kinds(found) and "exited 1" in messages(found)

    def test_an_uncaptured_exit_code_is_an_evidence_error(self, tmp_path):
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", announce=False)
        two_machines(recorder)
        recorder.run("a binary that is not there", ["definitely-not-a-real-binary-xyz"])
        found = evidence.check_file(tmp_path / "e.jsonl")
        assert evidence.EVIDENCE_ERROR in kinds(found)
        assert "no exit code was captured" in messages(found)

    def test_empty_output_where_output_was_required_is_an_evidence_error(self, tmp_path):
        found = _recorded(tmp_path, [good_step(stdout="   \n", expect_output=True)])
        assert "stdout is empty" in messages(found)

    def test_a_swapped_run_id_fails(self, tmp_path):
        found = _recorded(tmp_path, [good_step(
            gateway_record={**OK_RECORD, "run_id": OTHER_RUN})])
        assert evidence.FAIL in kinds(found) and "is about" in messages(found)

    def test_a_missing_gateway_record_is_an_evidence_error(self, tmp_path):
        found = _recorded(tmp_path, [good_step(gateway_record=None)])
        assert "carries no gateway record" in messages(found)

    def test_a_404_is_not_a_match(self, tmp_path):
        found = _recorded(tmp_path, [good_step(
            gateway_record={"error": "no such run", "status": 404})])
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
            gateway_record={**OK_RECORD, "effective_policy_sha256": "b" * 64,
                            "policy_deltas": []})])
        assert "no narrowing was reported" in messages(found)

    def test_the_wrong_refusal_reason_fails(self, tmp_path):
        found = _recorded(tmp_path, [good_step(
            exit_code=1, expected_exit=1, expected_refusal="already been used (replay)",
            gateway_record={**OK_RECORD, "state": "refused",
                            "refusal": "this request is 880s old; the limit is 120s"})])
        assert "does not appear" in messages(found)

    def test_unknown_cleanup_where_cleanup_was_required_is_an_evidence_error(self, tmp_path):
        found = _recorded(tmp_path, [good_step(
            expect_cleanup=True, gateway_record={**OK_RECORD, "cleanup_verified": None})])
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
                  "container": f"agentnode-em3c-{RUN[:12]}-abc"}
        values.update(overrides)
        return _recorded(tmp_path, [good_step(**values)])

    def test_a_query_that_ran_and_found_nothing_passes(self, tmp_path):
        """The control. Without it a rule that refused everything would pass the rest."""
        assert self._found(tmp_path, dict(self.GOOD_QUERY)) == []

    def test_the_container_still_being_there_fails(self, tmp_path):
        query = {**self.GOOD_QUERY, "names": [f"agentnode-em3c-{RUN[:12]}-abc"]}
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
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        two_machines(recorder)
        recorder.record(a_step(
            name="gateway identity again", role="gateway", argv=["(identity)"],
            started_at=2.0, ended_at=2.1, exit_code=0,
            machine=identity("gateway", "c" * 64, "gf" * 32, "Linux")))
        found = evidence.check_file(path)
        assert "same host identity" in messages(found)

    def test_a_sentinel_carried_and_confirmed_over_one_channel_fails(self, tmp_path):
        path = tmp_path / "e.jsonl"
        recorder = evidence.Recorder(path, role="client", announce=False)
        two_machines(recorder)
        recorder.record(a_step(
            name="a sentinel that only one path ever saw", role="client", argv=["(sentinel)"],
            started_at=3.0, ended_at=3.1, exit_code=0,
            sentinel=crossing("client", "3" * 64, confirmed_over="agentnode-job")))
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
            sentinel=crossing("gateway", "4" * 64, in_request=True)))
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
            sentinel=crossing("client", "5" * 64, in_request=False)))
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
            sentinel=crossing("client", "7" * 64, in_other_channel=False)))
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
            sentinel=crossing("client", "1" * 64, in_response=False, matched=False)))
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
            sentinel=crossing("client", "1" * 64, confirmed_over="ssh", matched=True)))
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
                sentinel=crossing(made, "same" * 16)))
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
                sentinel=crossing(made, digest_for(made))))
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
        ("notes", "the token was {s}"),
        ("client_id", "{s}"),
        ("stdout", "value={s}\n"),
    ])
    def test_a_secret_in_any_string_field_is_removed(self, tmp_path, field, value):
        step = good_step(**{field: value.format(s=self.SECRET)})
        written = self._written(tmp_path, step)
        assert self.SECRET not in written
        assert evidence.REDACTED in written

    def test_a_secret_nested_in_a_record_is_removed(self, tmp_path):
        step = good_step(gateway_record={**OK_RECORD, "auth": {"token": self.SECRET},
                                         "list": [{"deep": self.SECRET}]})
        assert self.SECRET not in self._written(tmp_path, step)

    def test_a_secret_in_a_command_line_is_removed(self, tmp_path):
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", secrets=[self.SECRET], announce=False)
        recorder.run("echo", [sys.executable, "-c", f"print('{self.SECRET}')"])
        assert self.SECRET not in (tmp_path / "e.jsonl").read_text(encoding="utf-8")

    def test_a_value_nobody_collected_under_a_secret_named_key_is_removed(self, tmp_path):
        """The layer the value list cannot reach. This value is not in `secrets`, so only the key
        it sits under can save it -- which is the point of having that layer at all."""
        never_collected = "unknown-value-nobody-told-the-recorder-about"
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", secrets=[], announce=False)
        recorder.record(good_step(gateway_record={**OK_RECORD,
                                                  "auth": {"token": never_collected}}))
        written = (tmp_path / "e.jsonl").read_text(encoding="utf-8")
        assert never_collected not in written
        assert evidence.REDACTED in written

    def test_private_key_material_is_removed_wherever_it_appears(self, tmp_path):
        """Recognisable without being known: no value list can contain a key nobody collected."""
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", secrets=[], announce=False)
        recorder.record(good_step(notes="-----BEGIN OPENSSH PRIVATE KEY----- abc"))
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
            recorder.record(good_step(notes="a-live-credential-value is here"))
        assert "survived its own pass" in str(caught.value)
        assert not (tmp_path / "e.jsonl").exists() or             "a-live-credential-value" not in (tmp_path / "e.jsonl").read_text(encoding="utf-8")

    def test_the_rest_of_the_record_survives(self, tmp_path):
        """The control. A redactor that emptied the document would pass everything above."""
        step = good_step(notes=f"the token was {self.SECRET}", name="a distinctive step name")
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
