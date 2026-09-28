"""At most once, across a retry, a reconnect and the worker's own restart.

The transport already refuses a replayed MESSAGE. It cannot refuse a replayed JOB: a retry
carries a fresh nonce -- it must, or the nonce cache would refuse it -- so the same `run_id`
delivered twice is two legitimate messages. On one machine nothing exercised that, because the
gateway opens one connection per request and never retries. A network removes those accidents.

`remote-worker-r1` R6 asks that a job cannot run twice and cannot vanish, judged across a
connection dropped before, during and after execution, either side restarting, a lost response,
and the same job delivered twice. These are those cases, against `worker/journal.py`.

`unknown` is a first-class answer here and is never resolved by running the job again. Running
it again is the one move that could turn "nobody knows" into "it happened twice", and of the
two, twice is worse.

NOT A HOST-ISOLATION TEST: one process, one kernel.
"""
from __future__ import annotations

import json
import os
import threading

import pytest

from agentnode_sdk.worker import Job, Limits
from agentnode_sdk.worker import journal as J


def a_job(run_id="r1", command=("python", "-c", "pass"), artifact=b"code", stdin="in",
          network="none", domains=(), memory=64):
    return Job(run_id=run_id, container_name="c-" + run_id, command=list(command),
               artifact=artifact, stdin=stdin, network=network,
               allowed_domains=list(domains),
               limits=Limits(cpu=1.0, memory_mb=memory, processes=8, wall_clock_s=30))


@pytest.fixture()
def book(tmp_path):
    return J.Journal(tmp_path / "journal")


class TestTheOrdinaryRun:

    def test_a_fresh_run_is_claimed_and_settles(self, book):
        job = a_job()
        claim = book.claim(job.run_id, J.digest_of_job(job))
        assert claim.verdict == J.FRESH and claim.may_execute
        book.note_started(job.run_id)
        book.note_finished(job.run_id, {"exit_code": 0, "stdout": "ok"})
        book.note_cleanup(job.run_id, True)
        after = book.look(job.run_id)
        assert after.verdict == J.DONE
        assert after.state == J.CLEANED
        assert after.outcome["stdout"] == "ok"

    def test_exactly_one_verdict_permits_execution(self):
        """`may_execute` is the single gate, and only the verdict that CREATED the record
        opens it. A reader of this code should not have to compare strings to know that."""
        for verdict in (J.FRESH, J.IN_FLIGHT, J.DONE, J.UNKNOWN, J.CONFLICT, J.UNREADABLE):
            assert J.Claim(verdict, "r").may_execute is (verdict == J.FRESH)


class TestTheSameJobTwice:

    def test_an_identical_retry_does_not_run_again(self, book):
        job = a_job()
        first = book.claim(job.run_id, J.digest_of_job(job))
        book.note_started(job.run_id)
        book.note_finished(job.run_id, {"exit_code": 0, "stdout": "once"})

        again = book.claim(job.run_id, J.digest_of_job(job))
        assert first.may_execute and not again.may_execute
        assert again.verdict == J.DONE
        assert again.outcome["stdout"] == "once", "the retry is answered, not re-run"

    def test_a_retry_while_it_is_still_running_is_told_so(self, book):
        job = a_job()
        book.claim(job.run_id, J.digest_of_job(job))
        book.note_started(job.run_id)
        again = book.claim(job.run_id, J.digest_of_job(job))
        assert again.verdict == J.UNKNOWN or again.verdict == J.IN_FLIGHT
        assert not again.may_execute

    def test_a_new_transport_nonce_changes_nothing(self, book):
        """The point of the whole file: the message layer cannot see this, because a retry is
        SUPPOSED to carry a fresh nonce."""
        job = a_job()
        assert book.claim(job.run_id, J.digest_of_job(job)).may_execute
        for _ in range(5):
            assert not book.claim(job.run_id, J.digest_of_job(job)).may_execute


class TestTheSameNameForDifferentWork:

    @pytest.mark.parametrize("different", [
        {"command": ("python", "-c", "other")},
        {"artifact": b"different code"},
        {"stdin": "something else"},
        {"network": "restricted"},
        {"domains": ("example.com",)},
        {"memory": 512},
    ])
    def test_it_is_refused_rather_than_resolved(self, book, different):
        """Refused, not resolved: picking either delivery would be choosing which of two
        callers to be wrong about."""
        first = a_job()
        book.claim(first.run_id, J.digest_of_job(first))
        second = a_job(**different)
        with pytest.raises(J.JournalRefused) as refused:
            book.claim(second.run_id, J.digest_of_job(second))
        assert refused.value.cause == "run_id_reused_for_different_work"

    def test_and_the_digest_ignores_what_does_not_change_what_runs(self):
        """Two deliveries of the same work differ in nonce, request id and deadline. None of
        those changes what would execute, so none of them may make a retry look like a
        conflict."""
        assert J.digest_of_job(a_job()) == J.digest_of_job(a_job())


class TestTwoDeliveriesAtOnce:

    def test_only_one_of_many_simultaneous_identical_deliveries_may_run(self, book):
        job = a_job()
        digest = J.digest_of_job(job)
        verdicts: list[str] = []
        barrier = threading.Barrier(8)

        def deliver():
            barrier.wait()
            verdicts.append(book.claim(job.run_id, digest).verdict)

        threads = [threading.Thread(target=deliver) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert verdicts.count(J.FRESH) == 1, verdicts
        assert len(verdicts) == 8

    def test_and_simultaneous_conflicting_deliveries_do_not_quietly_pick_one(self, book):
        """One of them may win the race to create the record. The other must be REFUSED, not
        served with the winner's work under its own run id."""
        one, two = a_job(), a_job(command=("python", "-c", "different"))
        outcomes: list[str] = []
        barrier = threading.Barrier(2)

        def deliver(job):
            barrier.wait()
            try:
                outcomes.append(book.claim(job.run_id, J.digest_of_job(job)).verdict)
            except J.JournalRefused as refused:
                outcomes.append(refused.cause)

        threads = [threading.Thread(target=deliver, args=(j,)) for j in (one, two)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert outcomes.count(J.FRESH) == 1
        assert "run_id_reused_for_different_work" in outcomes


class TestCrashesAtEveryPoint:

    def test_a_crash_before_it_started_leaves_it_claimable_by_nobody_else(self, book, tmp_path):
        job = a_job()
        book.claim(job.run_id, J.digest_of_job(job))
        # A new Journal object is a restarted worker reading the same directory.
        after = J.Journal(tmp_path / "journal")
        again = after.claim(job.run_id, J.digest_of_job(job))
        assert not again.may_execute
        assert again.verdict == J.IN_FLIGHT

    def test_a_crash_while_it_ran_yields_unknown_and_never_a_second_run(self, book, tmp_path):
        """The case that decides the whole design. Something happened in the world; this
        worker cannot say what; running it again could make it happen twice."""
        job = a_job()
        book.claim(job.run_id, J.digest_of_job(job))
        book.note_started(job.run_id)          # and then the process dies

        restarted = J.Journal(tmp_path / "journal")
        again = restarted.claim(job.run_id, J.digest_of_job(job))
        assert again.verdict == J.UNKNOWN
        assert not again.may_execute

    def test_a_crash_after_it_ran_but_before_the_answer_is_recoverable(self, book, tmp_path):
        """The expensive case: the job ran, the container is gone, and the connection dropped
        before the answer. The outcome is on disk, so the next delivery is ANSWERED."""
        job = a_job()
        book.claim(job.run_id, J.digest_of_job(job))
        book.note_started(job.run_id)
        book.note_finished(job.run_id, {"exit_code": 0, "stdout": "the work happened"})

        restarted = J.Journal(tmp_path / "journal")
        again = restarted.claim(job.run_id, J.digest_of_job(job))
        assert again.verdict == J.DONE
        assert again.outcome["stdout"] == "the work happened"
        assert restarted.look(job.run_id).outcome["exit_code"] == 0

    def test_the_record_survives_a_restart(self, book, tmp_path):
        job = a_job()
        book.claim(job.run_id, J.digest_of_job(job))
        book.note_started(job.run_id)
        book.note_finished(job.run_id, {"exit_code": 3, "stdout": "x"})
        book.note_cleanup(job.run_id, None)
        assert J.Journal(tmp_path / "journal").look(job.run_id).state == J.CLEANUP_UNPROVEN


class TestADamagedRecordIsNotPermission:

    def _damage(self, book, run_id, text):
        path = book._path(run_id)
        with open(path, "wb") as handle:
            handle.write(text)

    def test_a_record_that_is_not_json_refuses(self, book):
        job = a_job()
        book.claim(job.run_id, J.digest_of_job(job))
        self._damage(book, job.run_id, b"{not json")
        with pytest.raises(J.JournalRefused) as refused:
            book.claim(job.run_id, J.digest_of_job(job))
        assert refused.value.cause == "journal_damaged"

    def test_a_record_whose_checksum_does_not_match_refuses(self, book):
        job = a_job()
        book.claim(job.run_id, J.digest_of_job(job))
        record = json.loads(open(book._path(job.run_id), "rb").read())
        record["state"] = J.FINISHED                     # edited without redoing the checksum
        self._damage(book, job.run_id, json.dumps(record).encode())
        with pytest.raises(J.JournalRefused) as refused:
            book.claim(job.run_id, J.digest_of_job(job))
        assert refused.value.cause == "journal_damaged"

    def test_a_damaged_record_is_never_read_as_finished(self, book):
        """The failure that matters: a damaged record must not be mistaken for a run that
        completed, and must not be mistaken for a run that never happened either."""
        job = a_job()
        book.claim(job.run_id, J.digest_of_job(job))
        self._damage(book, job.run_id, b'{"format": 1, "state": "finished"}')
        with pytest.raises(J.JournalRefused):
            book.claim(job.run_id, J.digest_of_job(job))

    def test_a_journal_that_cannot_be_written_refuses_to_let_anything_run(self, tmp_path):
        at = tmp_path / "not-a-directory"
        at.write_text("I am a file", encoding="utf-8")
        with pytest.raises(J.JournalRefused) as refused:
            J.Journal(at)
        assert refused.value.cause == "journal_unwritable"


class TestWhatIsWrittenDown:

    def test_no_artefact_no_stdin_no_command_payload_reaches_the_disk(self, book):
        job = a_job(artifact=b"SECRET-ARTEFACT-BYTES", stdin="SECRET-STDIN-VALUE")
        book.claim(job.run_id, J.digest_of_job(job))
        raw = open(book._path(job.run_id), "rb").read()
        assert b"SECRET-ARTEFACT-BYTES" not in raw
        assert b"SECRET-STDIN-VALUE" not in raw
        assert J.digest_of_job(job).encode() in raw, "the digest is kept, the work is not"

    def test_an_enormous_outcome_is_kept_short(self, book):
        job = a_job()
        book.claim(job.run_id, J.digest_of_job(job))
        book.note_started(job.run_id)
        book.note_finished(job.run_id, {"stdout": "x" * (J.MOST_TEXT * 3)})
        kept = book.look(job.run_id).outcome["stdout"]
        assert len(kept) < J.MOST_TEXT * 2

    @pytest.mark.skipif(os.name == "nt", reason="POSIX modes; Windows has no group/other bits")
    def test_records_are_written_narrow(self, book):
        job = a_job()
        book.claim(job.run_id, J.digest_of_job(job))
        assert os.stat(book._path(job.run_id)).st_mode & 0o777 == J.FILE_MODE

    def test_a_run_id_from_another_machine_cannot_choose_a_path(self, book, tmp_path):
        """A run id is a string somebody else sent. `../` is a string too."""
        book.claim("../../escaped", "d" * 64)
        assert not (tmp_path / "escaped").exists()
        assert not (tmp_path / "escaped.json").exists()
        assert book.count() == 1


class TestRetention:

    def test_an_unfinished_run_is_never_swept(self, book):
        book.claim("live", "d" * 64)
        book.note_started("live")
        assert book.sweep(now=1e12) == 0
        assert book.look("live") is not None

    def test_a_settled_acknowledged_run_is_swept(self, book):
        book.claim("done", "d" * 64)
        book.note_started("done")
        book.note_finished("done", {"exit_code": 0})
        book.acknowledge("done")
        assert book.sweep() == 1
        assert book.look("done") is None

    def test_a_settled_unacknowledged_run_is_kept_until_the_window_passes(self, book):
        book.claim("waiting", "d" * 64)
        book.note_started("waiting")
        book.note_finished("waiting", {"exit_code": 0})
        assert book.sweep() == 0, "the control plane may still legitimately come back for it"
        assert book.sweep(now=book._now() + J.KEEP_UNACKNOWLEDGED_SECONDS + 1) == 1


class TestAcrossTheBoundaryAJournalIsRequired:

    def test_a_worker_for_its_own_machine_refuses_without_one(self, tmp_path):
        """R6 is not satisfiable without it: over a network, retries and reconnects are
        ordinary and each carries a fresh nonce, so nothing else would stop a second run."""
        from agentnode_sdk.worker import SEPARATE_WORKER_HOST
        from agentnode_sdk.worker.service import serve

        with pytest.raises(J.JournalRefused) as refused:
            serve("", str(tmp_path / "k"), None, worker=object(),
                  tls_address="tcps://10.0.0.9:8443", tls=object(),
                  topology=SEPARATE_WORKER_HOST)
        assert refused.value.cause == "journal_not_configured"
        assert "--journal" in refused.value.what_to_do

    def test_and_a_worker_on_this_machine_still_needs_none(self):
        """The single-host arrangement keeps the behaviour it has always had."""
        from agentnode_sdk.worker.service import Bench

        assert Bench.journal is None
