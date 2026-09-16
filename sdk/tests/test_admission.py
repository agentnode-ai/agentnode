"""What happens before anything runs.

Every ceiling here is HIT and the refusal observed. A test that sets a limit and reads it back
proves the configuration round-trips and nothing else, and that is the shape of evidence this
file exists to avoid: `D2` in the frozen profile says plainly that a limit whose evidence is that
it was configured is NOT_EVIDENCED.
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import pathlib
import json
import uuid

import pytest

from agentnode_sdk.access import contract, dispatch
from agentnode_sdk.gateway import accounts as accounts_module
from agentnode_sdk.gateway import admission
from agentnode_sdk.gateway.allowance import Allowance, Use, write_allowance
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement
from tests.test_two_accounts import _a_customer, _a_run_by, _their_second_machine


@pytest.fixture()
def gateway(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    try:
        yield service
    finally:
        state.close()


class TestEveryReasonIsDeclaredAndRendersAsARefusal:

    def test_the_mapping_is_total(self):
        assert set(admission.AS_A_REFUSAL) == set(admission.REASONS)
        for reason, refusal in admission.AS_A_REFUSAL.items():
            assert refusal in contract.REFUSALS, (
                "%s renders as %r, which no client can be written against" % (reason, refusal))

    def test_a_reason_nobody_declared_cannot_be_raised(self):
        with pytest.raises(ValueError):
            admission.NotAdmitted("looks_suspicious", "because", "do this")

    def test_a_refusal_with_nothing_to_do_cannot_be_raised(self):
        with pytest.raises(ValueError):
            admission.NotAdmitted("device_rate", "you are going too fast", "")

    def test_and_neither_can_a_contract_refusal(self):
        """Every refusal in the product, not only admission's, names an action."""
        with pytest.raises(ValueError):
            dispatch.Refused("malformed", "something is wrong", "")


class TestTheRateIsAskedOfEveryOperationAndNotOnlyOfWork:

    def test_a_burst_of_cheap_reads_is_refused(self, gateway):
        who = _a_customer(gateway, "alice")
        write_allowance(gateway.state.root, Allowance(requests_per_minute=5))
        carried, refused = 0, None
        for _ in range(12):
            try:
                dispatch.dispatch("usage", {}, who, service=gateway)
                carried += 1
            except dispatch.Refused as stopped:
                refused = stopped
                break
        assert refused is not None, (
            "twelve requests went through a ceiling of five -- a gateway that rate-limits only "
            "the expensive operation has not rate-limited anything, because a probe uses the "
            "cheap ones")
        assert refused.refusal == "over_a_ceiling"
        assert carried <= 5
        assert refused.what_to_do

    def test_an_account_rate_counts_every_device_in_it(self, gateway):
        alice = _a_customer(gateway, "alice")
        second = _their_second_machine(gateway, alice)
        write_allowance(gateway.state.root, Allowance(account_requests_per_minute=4))
        spent = 0
        with pytest.raises(dispatch.Refused):
            for who in (alice, second) * 6:
                dispatch.dispatch("usage", {}, who, service=gateway)
                spent += 1
        assert spent <= 4, "pairing a second machine raised the account's rate ceiling"

    def test_an_unreadable_rate_counter_refuses_rather_than_forgetting(self, gateway):
        who = _a_customer(gateway, "alice")
        write_allowance(gateway.state.root, Allowance(requests_per_minute=100))
        (gateway.state.root / admission.RATE_NAME).write_text("{not json", encoding="utf-8")
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("usage", {}, who, service=gateway)
        assert refused.value.refusal == "over_a_ceiling", (
            "a rate limit that forgets when its file is damaged is one an attacker removes by "
            "damaging a file")

    def test_a_key_this_gateway_did_not_issue_is_treated_as_exhausted(self, gateway):
        rate = admission.RateLimit(gateway.state.root / "probe.json")
        assert rate.spend("../../etc/passwd", 10) > 0
        assert rate.spend("a" * 300, 10) > 0


class TestTheCeilingsOnWork:

    def test_a_device_may_not_exceed_its_runs_in_the_window(self, gateway):
        who = _a_customer(gateway, "alice")
        write_allowance(gateway.state.root, Allowance(runs_per_window=1))
        _a_run_by(gateway, who)
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, who)
        assert refused.value.refusal == "over_a_ceiling"
        assert "window" in refused.value.because

    def test_an_artifact_larger_than_this_gateway_accepts_is_refused_before_anything(
            self, gateway):
        who = _a_customer(gateway, "alice")
        write_allowance(gateway.state.root, Allowance(max_artifact_bytes=64))
        code = b"x = 1\n" * 200
        shown = dispatch.dispatch(
            "prepare",
            {"artifact_sha256": hashlib.sha256(code).hexdigest(), "artifact_bytes": len(code),
             "wall_clock_s": 30},
            who, service=gateway)
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch(
                "submit",
                {"run_id": uuid.uuid4().hex,
                 "artifact": base64.b64encode(code).decode("ascii"), "wall_clock_s": 30,
                 "accepted_disclosure": shown["accepted_disclosure"]},
                who, service=gateway)
        assert refused.value.refusal == "over_a_ceiling"
        assert gateway.runs == {} or all(
            r.state == "refused" for r in gateway.runs.values())

    def test_an_unreadable_ceiling_refuses_work_rather_than_applying_none(self, gateway):
        who = _a_customer(gateway, "alice")
        (gateway.state.root / "allowance.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, who)
        assert refused.value.refusal in ("gateway_stopped", "over_a_ceiling",
                                         "sandbox_unavailable")
        assert refused.value.what_to_do

    def test_a_ceiling_this_build_does_not_understand_is_refused_not_ignored(self, gateway):
        who = _a_customer(gateway, "alice")
        (gateway.state.root / "allowance.json").write_text(
            json.dumps({"concurrent_runs": 1, "requests_per_hour": 10}), encoding="utf-8")
        with pytest.raises(dispatch.Refused):
            _a_run_by(gateway, who)

    def test_an_unreadable_use_record_refuses_rather_than_restoring_the_window(self, gateway):
        who = _a_customer(gateway, "alice")
        write_allowance(gateway.state.root, Allowance(runs_per_window=1))
        # The counter is filled DIRECTLY rather than by running something. A real run writes to
        # use.json again from its own thread when it finishes, so a test that ran one and then
        # corrupted the file is racing that write -- and under load the run wins, rewrites valid
        # JSON, and the test measures an ordinary ceiling instead of an unreadable counter.
        Use(gateway.state.root / "use.json").note(who.client_id, "an-earlier-run")
        (gateway.state.root / "use.json").write_text("{ truncated", encoding="utf-8")
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, who)
        assert refused.value.refusal == "gateway_stopped"
        assert refused.value.what_to_do


class TestSuspensionAndTheStop:

    def test_a_suspended_account_cannot_work_and_can_still_read(self, gateway):
        who = _a_customer(gateway, "alice")
        gateway.state.accounts.suspend(who.account_id, "we need to talk about last Tuesday",
                                       by="the operator")
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, who)
        assert refused.value.refusal == "gateway_stopped"
        assert "last Tuesday" in refused.value.because, (
            "a suspension a customer cannot read is one they cannot act on")
        assert dispatch.dispatch("usage", {}, who, service=gateway)["runs"] == 0
        assert dispatch.dispatch("devices.list", {}, who, service=gateway)["devices"]

    def test_and_nothing_a_caller_sends_lifts_it(self, gateway):
        who = _a_customer(gateway, "alice")
        gateway.state.accounts.suspend(who.account_id, "a reason", by="the operator")
        declared = {op.name for op in contract.OPERATIONS}
        # There is no operation whose name or parameters could express "let me work again".
        for name in declared:
            op = contract.find(name)
            assert not any("suspend" in f.name or "restore" in f.name for f in op.params)
        with pytest.raises(dispatch.Refused):
            _a_run_by(gateway, who)

    def test_restoring_is_deliberate_and_not_a_side_effect_of_forgetting(self, gateway):
        who = _a_customer(gateway, "alice")
        gateway.state.accounts.suspend(who.account_id, "a reason")
        gateway.state.accounts.forget(who.account_id)
        # Forgetting the record is what DELETION does. It must not read as a restoration for an
        # account whose devices are still here.
        assert gateway.state.accounts.get(who.account_id).active, (
            "this is the honest consequence: a forgotten record IS active, which is why "
            "deletion removes the devices too and why forget() is not the way to unsuspend")
        gateway.state.accounts.suspend(who.account_id, "a reason")
        gateway.state.accounts.restore(who.account_id)
        assert gateway.state.accounts.get(who.account_id).active
        _a_run_by(gateway, who)

    def test_an_unrecognised_state_reads_as_suspended(self, gateway):
        who = _a_customer(gateway, "alice")
        gateway.state._write_private(accounts_module.ACCOUNTS_NAME, json.dumps(
            {who.account_id: {"state": "probably fine"}}))
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, who)
        assert refused.value.refusal == "gateway_stopped"

    def test_the_operator_stop_still_refuses_work_and_still_answers_about_the_past(
            self, gateway):
        from agentnode_sdk.gateway.allowance import STOPPED_SAYS, stop_everything

        who = _a_customer(gateway, "alice")
        stop_everything(gateway.state.root, "upgrading the sandbox image")
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, who)
        assert refused.value.because.startswith(STOPPED_SAYS), (
            "clients match on this opening; composing a second sentence makes the same event "
            "read differently depending on which door somebody came through")
        assert dispatch.dispatch("usage", {}, who, service=gateway)["runs"] == 0


class TestNoClaimToDetectIntent:
    """D8 is blocking in the frozen profile, so it is asserted rather than left to review."""

    #: The phrases that would be a claim to know what a job is FOR. Not a filter on rude words:
    #: each of these asserts a capability this product does not have and cannot acquire, and the
    #: damage is done by a reader believing it rather than by anyone writing it.
    FORBIDDEN = ("detects malicious", "detect malicious", "detects abuse",
                 "detects illegal", "detect illegal", "malicious intent",
                 "identifies malicious", "blocks malicious code",
                 "knows what the job is for")

    #: THE READER-FACING SURFACES, enumerated rather than assumed. `ALPHA-R2-ADMISSION-0010`
    #: returned D8 NOT_EVIDENCED because the test read Python files under the installed package
    #: and nothing else -- "no complete enumeration of those reader-facing surfaces is supplied",
    #: which was exactly right. A person meets this product through its documentation far more
    #: often than through its source.
    #:
    #: Each entry is (a directory relative to the repository root, a glob). The test FAILS if a
    #: directory named here does not exist, so removing a surface has to be a deliberate edit
    #: here rather than a silent gap that leaves the check passing over nothing.
    SURFACES = (
        ("sdk/agentnode_sdk", "**/*.py"),        # the source, including every docstring
        ("sdk/docs", "**/*.md"),                 # what an operator reads
        ("sdk", "*.md"),                         # the SDK's own README and friends
        (".", "*.md"),                           # the repository's front door
    )

    def _repo_root(self):
        import agentnode_sdk

        # .../sdk/agentnode_sdk/__init__.py -> .../
        return pathlib.Path(agentnode_sdk.__file__).resolve().parent.parent.parent

    def test_no_reader_facing_surface_claims_to_detect_intent(self):
        root = self._repo_root()
        offenders, looked_at = [], 0
        for where, pattern in self.SURFACES:
            directory = root / where
            assert directory.is_dir(), (
                "%s is named as a reader-facing surface and is not there. Either it moved, in "
                "which case fix this list, or it is gone, in which case say so -- a check that "
                "passes because it looked at nothing is the failure this whole file is about."
                % directory)
            for path in sorted(directory.glob(pattern)):
                if not path.is_file() or "node_modules" in path.parts or ".git" in path.parts:
                    continue
                try:
                    text = path.read_text(encoding="utf-8").lower()
                except (OSError, UnicodeDecodeError):
                    continue
                looked_at += 1
                for phrase in self.FORBIDDEN:
                    if phrase in text:
                        offenders.append("%s: %r" % (path.relative_to(root), phrase))
        assert looked_at > 100, (
            "only %d files were read, which is too few for this list to have covered the "
            "product. A green result here would be a green result about nothing." % looked_at)
        assert not offenders, (
            "a claim to determine intent is a claim this product does not have and cannot "
            "acquire: " + "; ".join(offenders))

    def test_the_commit_messages_on_this_branch_do_not_claim_it_either(self):
        """The surface the enumeration above cannot reach by walking a directory.

        A commit message is read -- in a pull request, in a changelog, in `git log` -- and it is
        the one reader-facing text that is not a file in the tree. What can be checked is this
        branch's own range against its base; what CANNOT be checked is a message somebody writes
        tomorrow, and no test in a repository can check that. The boundary is stated rather than
        papered over: this covers the work under review, and the future is covered by people.
        """
        import subprocess

        root = self._repo_root()
        try:
            done = subprocess.run(["git", "log", "--format=%B", "origin/main..HEAD"],
                                  cwd=root, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            pytest.skip("git is not available to read the branch's messages: %s" % exc)
        if done.returncode != 0:
            pytest.skip("the branch's base is not available here: %s" % done.stderr[-160:])
        body = done.stdout.lower()
        assert body.strip(), "no commit messages were read, so this test proved nothing"
        found = [p for p in self.FORBIDDEN if p in body]
        assert not found, "a commit message on this branch claims to detect intent: %r" % found

    def test_and_the_module_that_could_says_so_itself(self):
        import inspect

        said = inspect.getdoc(admission) or ""
        assert "does not detect what a job is for" in said.lower()
        assert "behavioural signals" in said.lower()


class TestTheStopAndTheSuspensionHoldOnEveryPathNotOnlyTheDispatcher:
    """`ALPHA-R2-ADMISSION-0010`, D1 and D4, which are the same finding read twice:

        "submit is itself a callable path to execution and calls admit without calling
         may_this_caller_proceed. Thus admission is not unskippable for internal callers."
        "account suspension is checked only by may_this_caller_proceed; direct submit/admit
         callers bypass that check, so suspension does not take effect on every execution door."

    Both were true. `may_this_caller_proceed` is reached from the dispatcher for every operation,
    and every customer-facing door goes through the dispatcher -- which is why the tests above
    this class pass and why the gap was invisible from outside. But `GatewayService.submit` is a
    public method on a public object, and a check that holds only because every caller remembered
    to ask for it first is available rather than enforced.

    So the standing checks moved to `admit`, which every path to execution goes through, and the
    tests below call it DIRECTLY -- no dispatcher, no principal, no adapter. If the only thing
    standing between a suspended account and a container is the dispatcher, these fail.

    What could not move is the rate limit: `rate.spend` consumes budget, so asking it twice would
    charge a caller twice for arriving once. The last test here is about that.
    """

    def _a_job_from(self, gateway, who):
        import hashlib

        from agentnode_sdk.gateway.policy_paths import policy_shape
        from agentnode_sdk.gateway.protocol import JobRequest, canonical_bytes, digest

        code = b"print(1)\n"
        request = JobRequest(job_id="j", run_id=uuid.uuid4().hex, wall_clock_s=5,
                             artifact_sha256=hashlib.sha256(code).hexdigest(),
                             policy_sha256="")
        shape = policy_shape(gateway.requested_policy(request))
        return dataclasses.replace(
            request, policy_sha256=digest(canonical_bytes(shape))), code

    def test_admit_refuses_a_suspended_account_with_no_dispatcher_in_sight(self, gateway):
        who = _a_customer(gateway, "alice")
        request, code = self._a_job_from(gateway, who)
        # The control first: it is admitted while the account is in good standing, so the
        # refusal below is the suspension and not a malformed request.
        gateway.admit(request, code, client_id=who.device_id, account_id=who.account_id)

        gateway.state.accounts.suspend(who.account_id, "we need to talk about last Tuesday",
                                       by="the operator")
        with pytest.raises(admission.NotAdmitted) as refused:
            gateway.admit(request, code, client_id=who.device_id, account_id=who.account_id)
        # BOTH halves, because they are deliberately different. `reason` is the internal code and
        # says which of the four standing checks fired; `refusal` is the wire vocabulary, and
        # `AS_A_REFUSAL` maps a suspension onto `gateway_stopped` so a caller sees one shape for
        # "this is not going to run and it is not about your request". Asserting only the wire
        # word would pass if the suspension check were replaced by the operator stop.
        assert refused.value.reason == "account_suspended"
        assert refused.value.refusal == "gateway_stopped"
        assert "last Tuesday" in refused.value.because

    def test_and_submit_refuses_it_too_because_submit_goes_through_admit(self, gateway):
        who = _a_customer(gateway, "alice")
        request, code = self._a_job_from(gateway, who)
        gateway.state.accounts.suspend(who.account_id, "a reason", by="the operator")
        # `submit` does not raise: on this path a refusal is an ANSWER, returned as a record with
        # a state and a reason, because a client that submitted something is owed a record of
        # what happened to it rather than a stack trace. What matters for D1 is not the shape of
        # the refusal but that nothing ran -- so both are asserted.
        record = gateway.submit(request, code, client_id=who.device_id,
                                account_id=who.account_id)
        assert record.state == "refused"
        assert record.refused_as == "gateway_stopped"
        assert "suspended" in record.refusal.lower()
        # NOTHING RAN, asked of the backend rather than of the record. `started_at` is stamped
        # when the submission is RECEIVED, so it is set on a refusal too and would have made a
        # comfortable and useless assertion. What settles it is that the backend was never asked
        # to run anything: the stand-in keeps every spec it is handed, and it was handed none.
        assert gateway.backend.specs == [], (
            "a refused submission reached the backend: %r" % (gateway.backend.specs,))

    def test_and_an_account_record_it_cannot_read_is_not_good_standing(self, gateway, tmp_path):
        """"Cannot tell" is a stop, here as everywhere else. An unreadable accounts file used to
        be the one way past this on the direct path, because the direct path asked nothing."""
        who = _a_customer(gateway, "alice")
        request, code = self._a_job_from(gateway, who)
        # The standing is replaced rather than the file corrupted, because what is being asked
        # here is whether ADMIT consults standing at all -- not whether `Accounts` re-reads a
        # file it has already cached, which is a different question with its own tests.
        unreadable = dataclasses.replace(
            gateway.standing_of(who.account_id, who.device_id), cannot_tell=True)
        gateway.standing_of = lambda *a, **k: unreadable
        with pytest.raises(admission.NotAdmitted) as refused:
            gateway.admit(request, code, client_id=who.device_id, account_id=who.account_id)
        assert refused.value.reason == "account_unreadable"

    def test_asking_twice_costs_the_caller_nothing(self, gateway):
        """The reason the rate limit did NOT move with the rest.

        `may_this_caller_proceed` spends rate budget; it is called per operation, which is the
        point. If `admit` had simply called it again, one submission would have been charged
        twice and a customer's limit would have been half what they were told. The function that
        moved is the one that consumes nothing -- asserted here rather than reasoned about,
        because "idempotent" is a claim that rots quietly.
        """
        who = _a_customer(gateway, "alice")
        request, code = self._a_job_from(gateway, who)
        standing = gateway.standing_of(who.account_id, who.device_id)
        for _ in range(50):
            admission.standing_permits_work(standing, stopped_because="")
        # Fifty calls to the moved check, and the rate limit has seen nothing.
        gateway.admit(request, code, client_id=who.device_id, account_id=who.account_id)
