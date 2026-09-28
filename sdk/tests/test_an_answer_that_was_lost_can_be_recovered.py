"""Asking the worker what became of a run whose answer never arrived.

The expensive case is not the worker going away. It is the worker doing the work, removing the
container, and then failing to say so -- the connection drops on the way back. Until now that
was recorded as `unverified` / `transport_lost`, billed, with the customer told that nothing was
established, and there was no way to ever find out: the protocol had no method to ask.

`remote-worker-r1` R6 asks that an outcome which is not known is never signed as a success, and
that a lost response is not the same as a job that did not run. R7 asks that billing follow
execution. Both need the same thing: a way to ask afterwards.

Four answers, and they are not interchangeable:

    the worker never heard of it      it did not run. Nothing to charge for.
    the worker has the outcome        it ran, and here is what happened.
    the worker began and cannot say   unknown, and it stays unknown.
    the worker is still running it    not finished; ask again.

NOT A HOST-ISOLATION TEST: one process, one kernel, loopback.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentnode_sdk.worker import Recovered, WorkerUnreachable
from agentnode_sdk.worker import journal as J
from agentnode_sdk.worker.remote import TlsWorker
from agentnode_sdk.worker.service import Bench
from tests.test_mtls_transport import (_one_boot, KEY, pair,  # noqa: F401  (fixtures)
                                       world)
from tests.test_the_worker_remembers_what_it_ran import a_job as a_journal_job


class TestTheWorkerCanBeAsked:

    def _bench(self, tmp_path, worker=None):
        bench = Bench(worker or SimpleNamespace(), "unix:///nowhere.sock", KEY, only_uid=None)
        bench.journal = J.Journal(tmp_path / "journal")
        return bench

    def test_a_run_it_never_heard_of(self, tmp_path):
        said = self._bench(tmp_path)._result("never-asked")
        assert said == {"known": False, "state": "", "outcome": None, "cleanup": None,
                        "ran_for": None, "never_ran": False}

    def test_a_run_it_finished(self, tmp_path):
        bench = self._bench(tmp_path)
        job = a_journal_job(run_id="did-run")
        bench.journal.claim(job.run_id, J.digest_of_job(job))
        bench.journal.note_started(job.run_id)
        bench.journal.note_finished(job.run_id, {"exit_code": 0, "stdout": "it happened"})

        said = bench._result("did-run")
        assert said["known"] and said["outcome"]["stdout"] == "it happened"
        assert not said["unknown_outcome"]

    def test_a_run_it_began_and_cannot_account_for(self, tmp_path):
        bench = self._bench(tmp_path)
        job = a_journal_job(run_id="began")
        bench.journal.claim(job.run_id, J.digest_of_job(job))
        bench.journal.note_started(job.run_id)

        said = bench._result("began")
        assert said["known"] and said["unknown_outcome"]
        assert said["outcome"] is None, "an outcome nobody has is not invented"

    def test_acknowledging_lets_the_record_go(self, tmp_path):
        bench = self._bench(tmp_path)
        job = a_journal_job(run_id="ack")
        bench.journal.claim(job.run_id, J.digest_of_job(job))
        bench.journal.note_started(job.run_id)
        bench.journal.note_finished(job.run_id, {"exit_code": 0})

        assert bench.journal.sweep() == 0
        bench._result("ack", acknowledge=True)
        assert bench.journal.sweep() == 1

    def test_a_worker_with_no_journal_says_it_cannot_say(self, tmp_path):
        from agentnode_sdk.worker import protocol as wire

        bench = Bench(SimpleNamespace(), "unix:///nowhere.sock", KEY, only_uid=None)
        with pytest.raises(wire.ProtocolError) as refused:
            bench._result("anything")
        assert refused.value.code == wire.JOURNAL_REFUSED


class TestAskingAcrossTheTransport:

    def test_the_question_and_its_answer_cross_mutual_tls(self, pair, tmp_path):
        world, gateway, _worker, door = pair
        door.bench.journal = J.Journal(tmp_path / "journal")
        job = a_journal_job(run_id="crossed")
        door.bench.journal.claim(job.run_id, J.digest_of_job(job))
        door.bench.journal.note_started(job.run_id)
        door.bench.journal.note_finished(job.run_id, {"exit_code": 7, "stdout": "recovered"})

        client = TlsWorker(door.address, KEY, world.settings(gateway, {"w1"}))
        said = client.result("crossed")

        assert isinstance(said, Recovered)
        assert said.known and said.outcome["exit_code"] == 7
        assert said.settles_it

    def test_a_run_the_worker_never_saw_settles_it_too(self, pair, tmp_path):
        """"It did not run" is a fact, not an absence of one, and it is the answer that means
        nothing may be charged for running it."""
        world, gateway, _worker, door = pair
        door.bench.journal = J.Journal(tmp_path / "journal")

        client = TlsWorker(door.address, KEY, world.settings(gateway, {"w1"}))
        said = client.result("never-reached-it")
        assert not said.known
        assert said.settles_it


class TestWhatTheRecoveredAnswerMeans:

    def test_an_unknown_outcome_does_not_settle_anything(self):
        began = Recovered(known=True, state=J.STARTED, unknown_outcome=True)
        assert not began.settles_it
        assert not began.still_running

    def test_a_run_still_going_is_not_an_outcome(self):
        going = Recovered(known=True, state=J.ACCEPTED)
        assert going.still_running
        assert not going.settles_it


class TestTheGatewayUsesIt:
    """The branch in `_run`'s transport-lost handler, exercised directly: building a whole
    gateway to drop a connection at one instant is a test about timing, not about the rule."""

    def _service(self, answer):
        from agentnode_sdk.gateway.server import GatewayService

        class Stub:
            def result(self, run_id):
                if isinstance(answer, Exception):
                    raise answer
                return answer

        return SimpleNamespace(
            worker=Stub(),
            _what_the_worker_says_became_of=(
                lambda record: GatewayService._what_the_worker_says_became_of(
                    SimpleNamespace(worker=Stub()), record)))

    def test_an_unreachable_worker_leaves_it_unresolved(self):
        service = self._service(WorkerUnreachable("still gone"))
        assert service._what_the_worker_says_became_of(SimpleNamespace(run_id="r")) is None

    def test_a_recovered_outcome_comes_back_whole(self):
        service = self._service(Recovered(known=True, state=J.FINISHED,
                                          outcome={"exit_code": 0, "stdout": "done"}))
        said = service._what_the_worker_says_became_of(SimpleNamespace(run_id="r"))
        assert said.outcome["stdout"] == "done"

    def test_a_recovered_outcome_becomes_the_same_object_a_live_run_produces(self):
        from agentnode_sdk.gateway.server import GatewayService
        from agentnode_sdk.worker import Outcome

        built = GatewayService._outcome_from({"exit_code": 4, "stdout": "x", "stderr": "y"})
        assert isinstance(built, Outcome)
        assert (built.exit_code, built.stdout, built.stderr) == (4, "x", "y")

    def test_and_an_unexpected_field_does_not_break_the_recovery(self):
        """A worker on another machine may be a build ahead. An outcome carrying a field this
        gateway does not know must not turn a recoverable answer into a crash."""
        from agentnode_sdk.gateway.server import GatewayService

        built = GatewayService._outcome_from({"exit_code": 0, "a_field_from_a_later_build": 1})
        assert built.exit_code == 0


class TestWhatIsBilledForARecoveredRun:
    """R7: billing follows execution. A connection that was broken for an hour must not be an
    hour of compute on somebody's bill."""

    def test_the_worker_reports_how_long_it_actually_ran(self, tmp_path):
        book = J.Journal(tmp_path / "journal")
        job = a_journal_job(run_id="timed")
        book.claim(job.run_id, J.digest_of_job(job))
        book.note_started(job.run_id)
        book.note_finished(job.run_id, {"exit_code": 0})

        said = book.look("timed")
        assert said.ran_for is not None and said.ran_for >= 0.0

    def test_a_duration_crosses_and_not_two_timestamps(self, tmp_path):
        """Two machines do not share a clock. The difference between their clocks is not a
        fact about either, so what crosses is how long it ran and nothing else."""
        bench = Bench(SimpleNamespace(), "unix:///nowhere.sock", KEY, only_uid=None)
        bench.journal = J.Journal(tmp_path / "journal")
        job = a_journal_job(run_id="dur")
        bench.journal.claim(job.run_id, J.digest_of_job(job))
        bench.journal.note_started(job.run_id)
        bench.journal.note_finished(job.run_id, {"exit_code": 0})

        said = bench._result("dur")
        assert "ran_for" in said
        assert "started_at" not in said and "finished_at" not in said

    def test_an_unfinished_run_reports_no_duration(self, tmp_path):
        book = J.Journal(tmp_path / "journal")
        job = a_journal_job(run_id="midway")
        book.claim(job.run_id, J.digest_of_job(job))
        book.note_started(job.run_id)
        assert book.look("midway").ran_for is None

    def test_the_end_time_of_a_recovered_run_is_not_when_the_gateway_gave_up(self):
        """A run that took 2 seconds and was recovered 3600 seconds later is billed 2 seconds,
        not 3602.

        THIS TEST USED TO DO THE ARITHMETIC ITSELF -- `began + ran_for` asserted against 2.0 --
        which tests Python's addition and could not fail whatever the product did. It now names
        the product's own line, so moving that line moves this test.
        """
        import inspect

        from agentnode_sdk.gateway import server as _server

        source = inspect.getsource(_server.GatewayService)
        assert "record.finished_at = float(record.started_at) + float(settled.ran_for)" in source, (
            "the recovered end time is this gateway's start plus the duration the WORKER "
            "measured; if that line moved, this test has to follow it")
        assert "record.billed_from_the_workers_clock = True" in source

        began, ran_for, outage = 1_000_000.0, 2.0, 3600.0
        assert (began + ran_for) - began == ran_for
        assert (began + outage) - began != ran_for, "what it would have been without that line"

    def test_the_record_says_which_clock_decided(self):
        """A reader comparing the line against this gateway's own clock would otherwise find
        them disagreeing and have no way to know why."""
        from agentnode_sdk.gateway.server import RunRecord

        assert RunRecord.billed_from_the_workers_clock is False
        assert RunRecord.recovered_after_losing_the_connection is False


class TestRecoveringAfterTheGatewayItselfRestarted:
    """The other half of R6. A restarted gateway was not there when the run ended -- the worker
    was -- and before this, every interrupted run was written as `interrupted` with whatever
    this side last knew, even when the other side could say it had finished or never started."""

    def test_the_four_answers_are_distinguishable_on_the_wire(self, tmp_path):
        """One is billed, one is not, one is unresolved and one means it never reached the
        worker. A single boolean could not carry that."""
        bench = Bench(SimpleNamespace(), "unix:///nowhere.sock", KEY, only_uid=None)
        bench.journal = J.Journal(tmp_path / "journal")

        bench.journal.claim("ran", "d" * 64, container="c-ran")
        bench.journal.note_started("ran")
        bench.journal.note_finished("ran", {"exit_code": 0, "stdout": "ok"})

        bench.journal.claim("began", "d" * 64, container="c-began")
        bench.journal.note_started("began")

        bench.journal.claim("claimed", "d" * 64, container="c-claimed")
        bench.journal.note_never_started("claimed")

        ran = bench._result("ran")
        began = bench._result("began")
        claimed = bench._result("claimed")
        never = bench._result("not-this-one")

        assert ran["outcome"] and not ran["never_ran"]
        assert began["unknown_outcome"] and began["outcome"] is None
        assert claimed["never_ran"] and not claimed["unknown_outcome"]
        assert not never["known"]

    def test_a_run_that_never_started_settles_and_costs_nothing(self):
        """`settles_it` is what the recovery branches on, and "it did not run" settles it just
        as firmly as an outcome does."""
        claimed = Recovered(known=True, state=J.NEVER_STARTED, never_ran=True)
        assert claimed.settles_it
        assert not claimed.still_running
        assert claimed.outcome is None

    def test_the_recovery_path_reads_the_workers_answer(self):
        import inspect

        from agentnode_sdk.gateway.server import GatewayService

        source = inspect.getsource(GatewayService._close_an_interrupted_run)
        assert "_what_the_worker_says_became_of" in source
        assert "never_ran" in source
        assert "started = 0.0" in source, (
            "a run that never started has no billed clock to have begun")


class TestARunThatNeverReachedTheWorkerIsNotBilledForTheOutage:
    """The defect producing the usage records found, and it was a real invoice.

    `scripts/the_usage_records.py` case 5: a run interrupted by a restart, which the worker has
    no record of at all, was written into the signed log with `seconds` equal to the WHOLE
    OUTAGE -- an hour, in the exercise. The transport-lost branch in `_run` had this right and
    said so in as many words; the restart path had only the `never_ran` case, which is the
    DIFFERENT one where the worker claimed the run and never started it.

    There are two ways nothing ran, and a bill must survive both.
    """

    def _a_gateway(self, tmp_path):
        import json as _json

        from agentnode_sdk.gateway.identity import GatewayState
        from agentnode_sdk.gateway.server import GatewayService
        from tests.test_em3c_gateway import StandInBackend, _store_measurement

        root = tmp_path / "state"
        root.mkdir(parents=True, exist_ok=True)
        (root / "allowance.json").write_text(
            _json.dumps({"machine_concurrent_runs": 1, "queue_depth": 2}), encoding="utf-8")
        state = GatewayState(str(root), version="test")
        service = GatewayService(state, backend=StandInBackend())
        _store_measurement(service)
        return service, state

    def _closed(self, tmp_path, answer, *, outage):
        import json as _json
        import pathlib
        import time as _time

        from agentnode_sdk.gateway import meter
        from agentnode_sdk.gateway.server import GatewayService, RunRecord
        from tests.test_two_accounts import _a_customer

        service, state = self._a_gateway(tmp_path)
        try:
            who = _a_customer(service, "somebody-real")
            record = RunRecord(run_id="lost", job_id="j", owner_client_id=who.client_id,
                               owner_account_id=who.account_id)
            service._worker = SimpleNamespace(
                result=lambda _id: answer,
                who_ran=lambda _id: ("stood-in", "", "a-stood-in-worker"))
            GatewayService._close_an_interrupted_run(
                service, record,
                {"first_seen": _time.time() - outage - 5.0,
                 "started_at": _time.time() - outage, "admitted": {}},
                reason="the connection was lost")
            written = pathlib.Path(state.root) / meter.METER_NAME
            lines = [_json.loads(x) for x in written.read_text(encoding="utf-8").splitlines()
                     if x.strip()]
            return next(x for x in lines if x["run_id"] == "lost")
        finally:
            service.close()
            state.close()

    def test_a_run_the_worker_has_no_record_of_is_billed_nothing(self, tmp_path):
        line = self._closed(tmp_path, Recovered(known=False), outage=3600.0)
        assert line["seconds"] == 0.0, (
            "billed %s seconds for a run that never reached the worker" % line["seconds"])

    def test_a_run_it_claimed_and_never_started_is_billed_nothing_either(self, tmp_path):
        line = self._closed(tmp_path, Recovered(known=True, state=J.NEVER_STARTED,
                                                never_ran=True), outage=3600.0)
        assert line["seconds"] == 0.0

    def test_and_one_that_did_run_is_billed_what_it_ran(self, tmp_path):
        """The control: the same path, with a worker that has an outcome, still bills the
        duration -- so the two above are not passing because nothing is ever billed here."""
        line = self._closed(tmp_path, Recovered(known=True, state=J.FINISHED,
                                                outcome={"exit_code": 0}, ran_for=2.0),
                            outage=3600.0)
        assert line["seconds"] == 2.0
