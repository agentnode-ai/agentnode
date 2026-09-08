"""The evidence runner, proved against deliberately broken evidence.

`EM3C-EXTERNAL-0017` blocked on evidence rather than on code, and the way a checker of evidence
fails is by passing things it cannot see. So every test here builds a record that is wrong in one
specific way and requires the checker to say so — and each is paired with the same record made
correct, because a checker that refused everything would pass the first half of this file and
prove nothing.

The ten classes are the ones named when this work was commissioned: a swapped run id, a missing
gateway record, a wrong exit code, empty output, a mismatched request or effective digest, missing
policy deltas, the wrong refusal reason, a failed cleanup query, a container from another run, and
a secret in a log.
"""
from __future__ import annotations

import json
import sys

import pytest

from agentnode_sdk.tools import evidence


RUN = "beeac323964d45d8b1c8ef90fb51bc30"
OTHER_RUN = "d06a38ad43e54e3ab39ec18a18dcbbe6"


def good_step(**overrides) -> dict:
    """One step that is complete, self-consistent, and about the run it names."""
    step = {
        "name": "run a job",
        "role": "client",
        "argv": ["agentnode", "remote", "run", "job.py"],
        "started_at": 1.0,
        "ended_at": 2.0,
        "exit_code": 0,
        "expected_exit": 0,
        "stdout": "EXT-OK\n",
        "stderr": "",
        "run_id": RUN,
        "job_id": "job-1",
        "client_id": "client-1",
        "request_policy_sha256": "a" * 64,
        "effective_policy_sha256": "a" * 64,
        "policy_deltas": [],
        "container": f"agentnode-em3c-{RUN[:12]}-abc",
        "cleanup_verified": True,
        "error_class": "",
        "expect_output": True,
        "gateway_record": {
            "run_id": RUN,
            "request_policy_sha256": "a" * 64,
            "effective_policy_sha256": "a" * 64,
            "policy_deltas": [],
            "cleanup_verified": True,
            "state": "finished",
            "refusal": "",
        },
    }
    step.update(overrides)
    return step


def problems(step) -> list[str]:
    return evidence.verify([step])


class TestTheControlPasses:
    """Without this, a checker that refused everything would pass every test below."""

    def test_a_complete_and_consistent_step_is_accepted(self):
        assert problems(good_step()) == []

    def test_a_step_that_is_expected_to_be_refused_and_is_refused_correctly_passes(self):
        step = good_step(
            expected_exit=1,
            exit_code=1,
            expected_refusal="already been used (replay)",
            gateway_record={**good_step()["gateway_record"],
                            "state": "refused",
                            "refusal": "this request has already been used (replay)"},
        )
        assert problems(step) == []


class TestTheTenFailuresAreDetected:

    def test_1_a_swapped_run_id_is_detected(self):
        record = {**good_step()["gateway_record"], "run_id": OTHER_RUN}
        found = problems(good_step(gateway_record=record))
        assert found and any("two runs in one piece of evidence" in p.lower()
                             or "is about" in p.lower() for p in found), found

    def test_2_a_missing_gateway_record_is_detected(self):
        found = problems(good_step(gateway_record=None))
        assert any("carries no gateway record" in p for p in found), found

    def test_2b_a_404_is_not_a_match(self):
        found = problems(good_step(gateway_record={"error": "no such run", "status": 404}))
        assert any("absent record is not a match" in p for p in found), found

    def test_3_a_wrong_exit_code_is_detected(self):
        found = problems(good_step(exit_code=1, expected_exit=0))
        assert any("exited 1" in p for p in found), found

    def test_3b_an_exit_code_from_a_pipe_is_not_accepted_as_the_commands_own(self):
        """A step whose exit code was never captured is unknown, not zero."""
        found = problems(good_step(exit_code=None, error_class="OSError"))
        assert any("no exit code was captured" in p for p in found), found

    def test_4_empty_output_is_detected(self):
        found = problems(good_step(stdout="   \n", expect_output=True))
        assert any("stdout is empty" in p for p in found), found

    def test_5_a_mismatched_request_digest_is_detected(self):
        found = problems(good_step(request_policy_sha256="b" * 64))
        assert any("request_policy_sha256" in p for p in found), found

    def test_5b_a_mismatched_effective_digest_is_detected(self):
        found = problems(good_step(effective_policy_sha256="c" * 64))
        assert any("effective_policy_sha256" in p for p in found), found

    def test_6_missing_policy_deltas_are_detected(self):
        """The exact defect the external run found: digests differ, nothing reported."""
        record = {**good_step()["gateway_record"],
                  "effective_policy_sha256": "b" * 64, "policy_deltas": []}
        found = problems(good_step(gateway_record=record,
                                   effective_policy_sha256="b" * 64))
        assert any("no narrowing was reported" in p for p in found), found

    def test_6b_an_absent_deltas_field_is_detected(self):
        record = {k: v for k, v in good_step()["gateway_record"].items() if k != "policy_deltas"}
        record["effective_policy_sha256"] = "b" * 64
        found = problems(good_step(gateway_record=record, effective_policy_sha256="b" * 64))
        assert any("no policy_deltas at all" in p for p in found), found

    def test_7_the_wrong_refusal_reason_is_detected(self):
        """A refusal for another reason is not the test passing."""
        record = {**good_step()["gateway_record"],
                  "state": "refused",
                  "refusal": "this request is 880s old; the limit is 120s"}
        found = problems(good_step(exit_code=1, expected_exit=1,
                                   expected_refusal="already been used (replay)",
                                   gateway_record=record))
        assert any("does not appear" in p for p in found), found

    def test_8_a_failed_cleanup_query_is_detected(self):
        record = {**good_step()["gateway_record"], "cleanup_verified": None}
        found = problems(good_step(gateway_record=record, expect_cleanup=True))
        assert any("cleanup is unknown" in p for p in found), found

    def test_8b_an_absent_cleanup_field_is_detected(self):
        record = {k: v for k, v in good_step()["gateway_record"].items()
                  if k != "cleanup_verified"}
        found = problems(good_step(gateway_record=record, expect_cleanup=True))
        assert any("does not mention it" in p for p in found), found

    def test_8c_a_cleanup_that_failed_is_detected(self):
        record = {**good_step()["gateway_record"], "cleanup_verified": False}
        found = problems(good_step(gateway_record=record, expect_cleanup=True))
        assert any("not verified" in p for p in found), found

    def test_9_a_container_from_another_run_is_detected(self):
        found = problems(good_step(container=f"agentnode-em3c-{OTHER_RUN[:12]}-zzz"))
        assert any("does not carry run" in p for p in found), found

    def test_10_a_secret_in_the_evidence_is_detected(self):
        secret = "s3cr3t-token-value-0123456789"
        step = good_step(stdout=f"token={secret}\n")
        found = evidence.verify([step], secrets=[secret])
        assert any("live secret value" in p for p in found), found


class TestMissingStructureIsNotAPass:

    @pytest.mark.parametrize("field", ["name", "role", "argv", "exit_code"])
    def test_a_missing_required_field_is_detected(self, field):
        step = good_step()
        del step[field]
        found = problems(step)
        assert any(field in p for p in found), found

    def test_an_empty_evidence_file_is_refused(self, tmp_path):
        path = tmp_path / "evidence.jsonl"
        path.write_text("", encoding="utf-8")
        with pytest.raises(evidence.EvidenceError):
            evidence.check_file(path)

    def test_unreadable_evidence_is_refused(self, tmp_path):
        path = tmp_path / "evidence.jsonl"
        path.write_text("{not json\n", encoding="utf-8")
        with pytest.raises(evidence.EvidenceError):
            evidence.check_file(path)


class TestSecretsNeverReachTheRecord:

    def test_a_pairing_code_argument_is_redacted(self):
        argv = ["agentnode", "remote", "connect", "http://x", "--code", "ABCD-EFGH-IJKL"]
        assert evidence.redact_argv(argv) == [
            "agentnode", "remote", "connect", "http://x", "--code", evidence.REDACTED]

    def test_an_equals_form_is_redacted(self):
        assert evidence.redact_argv(["x", "--token=abcdef123456"]) == ["x", "--token=[redacted]"]

    def test_the_command_itself_survives_redaction(self):
        """Redaction that removed the command would make the evidence useless."""
        argv = ["agentnode", "remote", "run", "job.py", "--allow", "example.com"]
        assert evidence.redact_argv(argv) == argv

    def test_a_recorded_command_has_its_secret_values_removed(self, tmp_path):
        secret = "tok_abcdef0123456789"
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", secrets=[secret])
        recorder.run("echo the secret", [sys.executable, "-c",
                                         f"print('value={secret}')"], expected_exit=0)
        written = (tmp_path / "e.jsonl").read_text(encoding="utf-8")
        assert secret not in written
        assert evidence.REDACTED in written


class TestTheRecorderCapturesWhatItMustCapture:

    def test_the_exit_code_is_the_commands_own(self, tmp_path):
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client")
        step = recorder.run("fail on purpose", [sys.executable, "-c", "raise SystemExit(3)"],
                            expected_exit=3)
        assert step.exit_code == 3

    def test_stdout_and_stderr_are_kept_apart(self, tmp_path):
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client")
        step = recorder.run("both streams", [
            sys.executable, "-c",
            "import sys; sys.stdout.write('OUT'); sys.stderr.write('ERR')"])
        assert step.stdout.strip() == "OUT"
        assert step.stderr.strip() == "ERR"

    def test_a_command_that_does_not_exist_is_recorded_as_unknown_not_as_success(self, tmp_path):
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client")
        step = recorder.run("missing binary", ["definitely-not-a-real-binary-xyz"])
        assert step.exit_code is None
        assert step.error_class
        assert any("no exit code was captured" in p for p in evidence.verify([step.as_dict()]))

    def test_each_step_is_one_line_of_readable_json(self, tmp_path):
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client")
        recorder.run("one", [sys.executable, "-c", "print(1)"])
        recorder.run("two", [sys.executable, "-c", "print(2)"])
        lines = [ln for ln in (tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines() if ln]
        assert len(lines) == 2
        assert all(json.loads(ln)["schema"] == evidence.SCHEMA for ln in lines)

    def test_the_times_are_recorded_and_ordered(self, tmp_path):
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client")
        step = recorder.run("timed", [sys.executable, "-c", "pass"])
        assert step.ended_at >= step.started_at > 0


class TestTheCommandLineEntryPoint:

    def _write(self, path, steps):
        path.write_text("\n".join(json.dumps({"schema": 1, **s}) for s in steps) + "\n",
                        encoding="utf-8")

    def test_good_evidence_exits_zero(self, tmp_path, capsys):
        path = tmp_path / "e.jsonl"
        self._write(path, [good_step()])
        assert evidence.main([str(path)]) == 0
        assert "about the run it names" in capsys.readouterr().out

    def test_bad_evidence_exits_non_zero_and_says_why(self, tmp_path, capsys):
        path = tmp_path / "e.jsonl"
        self._write(path, [good_step(gateway_record=None)])
        assert evidence.main([str(path)]) == 1
        assert "no gateway record" in capsys.readouterr().out

    def test_a_missing_file_is_not_a_pass(self, tmp_path):
        with pytest.raises((OSError, SystemExit)):
            evidence.main([str(tmp_path / "nope.jsonl")])


class TestRedactionCoversEveryFieldNotThreeOfThem:
    """EM3C-FINAL-0001: only argv, stdout and stderr were redacted, so a token arriving in a
    note, a gateway record or a policy delta was written to disk in full."""

    SECRET = "tok_9f8e7d6c5b4a3210fedcba"

    def _written(self, tmp_path, step):
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", secrets=[self.SECRET])
        recorder.record(step)
        return (tmp_path / "e.jsonl").read_text(encoding="utf-8")

    def test_a_secret_in_a_note_is_removed(self, tmp_path):
        step = evidence.Step(name="n", role="client", argv=["x"], started_at=1.0, ended_at=2.0,
                             exit_code=0, stdout="", stderr="",
                             notes=f"the token was {self.SECRET}")
        written = self._written(tmp_path, step)
        assert self.SECRET not in written
        assert evidence.REDACTED in written

    def test_a_secret_nested_in_a_gateway_record_is_removed(self, tmp_path):
        step = evidence.Step(name="n", role="client", argv=["x"], started_at=1.0, ended_at=2.0,
                             exit_code=0, stdout="", stderr="",
                             gateway_record={"auth": {"token": self.SECRET},
                                             "list": [{"deep": self.SECRET}]})
        assert self.SECRET not in self._written(tmp_path, step)

    def test_a_secret_in_a_policy_delta_is_removed(self, tmp_path):
        step = evidence.Step(name="n", role="client", argv=["x"], started_at=1.0, ended_at=2.0,
                             exit_code=0, stdout="", stderr="",
                             policy_deltas=[{"field": "x", "requested": self.SECRET}])
        assert self.SECRET not in self._written(tmp_path, step)

    def test_a_secret_in_a_run_id_field_is_removed(self, tmp_path):
        step = evidence.Step(name="n", role="client", argv=["x"], started_at=1.0, ended_at=2.0,
                             exit_code=0, stdout="", stderr="", client_id=self.SECRET)
        assert self.SECRET not in self._written(tmp_path, step)

    def test_the_rest_of_the_record_survives_redaction(self, tmp_path):
        """The control. A redactor that emptied the document would pass everything above."""
        step = evidence.Step(name="a distinctive step name", role="client",
                             argv=["agentnode", "remote", "run"], started_at=1.0, ended_at=2.0,
                             exit_code=7, stdout="ordinary output", stderr="",
                             notes=f"the token was {self.SECRET}")
        written = self._written(tmp_path, step)
        assert "a distinctive step name" in written
        assert "ordinary output" in written
        assert '"exit_code": 7' in written

    def test_a_secret_reaching_the_recorder_through_run_is_removed_everywhere(self, tmp_path):
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", secrets=[self.SECRET])
        recorder.run("echo", [sys.executable, "-c", f"print('{self.SECRET}')"],
                     notes=f"note holding {self.SECRET}",
                     gateway_record={"nested": self.SECRET})
        assert self.SECRET not in (tmp_path / "e.jsonl").read_text(encoding="utf-8")
