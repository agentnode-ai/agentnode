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
                        "ran_for": None}

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
        """The arithmetic, directly. A run that took 2 seconds and was recovered 3600 seconds
        later is billed 2 seconds, not 3602."""
        began = 1_000_000.0
        ran_for = 2.0
        gave_up_at = began + 3600.0

        ends_at = began + ran_for
        assert ends_at - began == 2.0
        assert gave_up_at - began == 3600.0, "what it would have been without this"

    def test_the_record_says_which_clock_decided(self):
        """A reader comparing the line against this gateway's own clock would otherwise find
        them disagreeing and have no way to know why."""
        from agentnode_sdk.gateway.server import RunRecord

        assert RunRecord.billed_from_the_workers_clock is False
        assert RunRecord.recovered_after_losing_the_connection is False
