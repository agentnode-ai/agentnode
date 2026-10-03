"""Two things shared a directory, and something enumerated it.

The lease counter was written into the run journal's directory. The journal lists that
directory and asks only whether a name ends in `.json`, so it read `lease-epoch.json` as a
run record: no `state`, therefore "unsettled"; no `run_id`, therefore a run id of `""`. At
startup the worker tries to settle everything unsettled, `_amend("")` looks for the record
whose name is the sha256 of the empty string, does not find it, and refuses.

So a worker that had ever been given a lease could not be started again. On the two test
machines that is what a reboot of the worker host meant: six documented recovery attempts,
and only a complete wipe and reinstall worked.

Two repairs, because there are two faults. The counter moves out of the journal -- carrying
its number, since a counter that restarts at zero re-issues epoch 1 and makes a retired
gateway's instructions valid again -- and the journal stops believing that anything ending
in `.json` is one of its records.

NOT A HOST-ISOLATION TEST: one process, one kernel.
"""
from __future__ import annotations

import json
import os

import pytest

from agentnode_sdk.worker import journal as J
from agentnode_sdk.worker import lease as L


class Clock:
    def __init__(self, at=1000.0):
        self.t = at

    def __call__(self):
        return self.t

    def tick(self, seconds):
        self.t += seconds


def write_counter(path, epoch):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_epoch": epoch}), encoding="utf-8")


class TestTheCounterIsCarried:
    """All four states the profile names: old only, new only, both, neither."""

    def new_and_old(self, tmp_path):
        return tmp_path / "lease-epoch.json", tmp_path / "journal" / "lease-epoch.json"

    def test_neither_starts_at_zero(self, tmp_path):
        new, old = self.new_and_old(tmp_path)
        book = L.Leases(new, legacy=old, clock=Clock())
        assert book.take("g1").epoch == 1

    def test_new_only_is_read_as_it_stands(self, tmp_path):
        new, old = self.new_and_old(tmp_path)
        write_counter(new, 41)
        book = L.Leases(new, legacy=old, clock=Clock())
        assert book.take("g1").epoch == 42

    def test_old_only_is_carried_not_restarted(self, tmp_path):
        """THE ONE THAT MATTERS. Reading a missing file as zero would hand out epoch 1
        again, and an epoch handed out twice makes a retired gateway's orders valid."""
        new, old = self.new_and_old(tmp_path)
        write_counter(old, 41)
        book = L.Leases(new, legacy=old, clock=Clock())
        assert book.take("g1").epoch == 42
        assert book.carried_from == str(old)

    def test_and_the_old_file_is_taken_out_of_the_journal(self, tmp_path):
        new, old = self.new_and_old(tmp_path)
        write_counter(old, 41)
        L.Leases(new, legacy=old, clock=Clock())
        assert not old.exists()
        assert new.exists()

    def test_both_takes_the_higher_and_never_the_lower(self, tmp_path):
        new, old = self.new_and_old(tmp_path)
        write_counter(new, 7)
        write_counter(old, 41)
        book = L.Leases(new, legacy=old, clock=Clock())
        assert book.take("g1").epoch == 42

    def test_both_the_other_way_round_too(self, tmp_path):
        new, old = self.new_and_old(tmp_path)
        write_counter(new, 41)
        write_counter(old, 7)
        book = L.Leases(new, legacy=old, clock=Clock())
        assert book.take("g1").epoch == 42

    def test_an_unreadable_old_counter_refuses_rather_than_guesses(self, tmp_path):
        new, old = self.new_and_old(tmp_path)
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("{not json", encoding="utf-8")
        with pytest.raises(L.LeaseRefused) as refused:
            L.Leases(new, legacy=old, clock=Clock())
        assert refused.value.cause == "lease_counter_unreadable"

    def test_an_unreadable_new_counter_still_refuses(self, tmp_path):
        new, old = self.new_and_old(tmp_path)
        new.write_text("{not json", encoding="utf-8")
        with pytest.raises(L.LeaseRefused):
            L.Leases(new, legacy=old, clock=Clock())

    def test_carrying_is_idempotent(self, tmp_path):
        new, old = self.new_and_old(tmp_path)
        write_counter(old, 41)
        L.Leases(new, legacy=old, clock=Clock())
        again = L.Leases(new, legacy=old, clock=Clock())
        assert again.take("g1").epoch == 42
        assert again.carried_from == ""


class TestTheCounterIsNotInTheJournalAnyMore:

    def test_the_wiring_puts_it_beside_the_journal(self):
        import inspect

        from agentnode_sdk.worker import service

        source = inspect.getsource(service.serve)
        assert "legacy=legacy" in source
        assert "os.path.join(journal_at, _lease.COUNTER_NAME)\n" not in source.replace(
            "legacy = os.path.join(journal_at, _lease.COUNTER_NAME)\n", "")


class TestTheJournalKnowsItsOwn:
    """The second fault, kept separate: even with the counter moved, anything else that ever
    lands in this directory would be read as a run."""

    def journal_with_a_stray(self, tmp_path):
        book = J.Journal(str(tmp_path / "journal"))
        book.claim("r1", "d1", container="c1")
        stray = tmp_path / "journal" / "lease-epoch.json"
        stray.write_text(json.dumps({"last_epoch": 3}), encoding="utf-8")
        return book, stray

    def test_a_stray_file_is_not_unsettled_work(self, tmp_path):
        book, _stray = self.journal_with_a_stray(tmp_path)
        assert [run for run, _s, _c in book.unsettled()] == ["r1"]

    def test_and_never_produces_a_nameless_run(self, tmp_path):
        book, _stray = self.journal_with_a_stray(tmp_path)
        assert all(run for run, _s, _c in book.unsettled())

    def test_the_count_is_records_and_not_files(self, tmp_path):
        book, _stray = self.journal_with_a_stray(tmp_path)
        assert book.count() == 1

    def test_a_file_named_like_a_record_but_empty_of_one_is_refused(self, tmp_path):
        book = J.Journal(str(tmp_path / "journal"))
        book.claim("r1", "d1", container="c1")
        (tmp_path / "journal" / ("a" * 64 + ".json")).write_text(
            json.dumps({"hello": "world"}), encoding="utf-8")
        assert book.count() == 1
        assert [run for run, _s, _c in book.unsettled()] == ["r1"]

    def test_sweep_does_not_touch_what_is_not_ours(self, tmp_path):
        book, stray = self.journal_with_a_stray(tmp_path)
        book.sweep(now=1e12)
        assert stray.exists(), "sweep deleted a file it does not own"

    def test_a_real_record_is_still_found(self, tmp_path):
        """The guard must not be so tight that it excludes the records it is protecting."""
        book = J.Journal(str(tmp_path / "journal"))
        book.claim("r1", "d1", container="c1")
        book.note_started("r1")
        assert [(r, s) for r, s, _c in book.unsettled()] == [("r1", J.STARTED)]


class TestAWorkerCanStartAfterALease:
    """The whole point, end to end at the reconciliation that actually died."""

    def test_reconciliation_survives_a_stray_file(self, tmp_path):
        from types import SimpleNamespace

        from agentnode_sdk.worker.service import reconcile_what_was_left

        book = J.Journal(str(tmp_path / "journal"))
        (tmp_path / "journal" / "lease-epoch.json").write_text(
            json.dumps({"last_epoch": 3}), encoding="utf-8")
        bench = SimpleNamespace(journal=book, worker=None)
        # Against the unfixed code this raises JournalRefused("journal_missing") and the
        # worker never opens its door.
        assert reconcile_what_was_left(bench)["considered"] == 0

    def test_a_nameless_entry_is_ignored_rather_than_settled(self, tmp_path):
        """Belt and braces: the enumerator no longer emits one, and the loop no longer
        trusts that it never will."""
        from types import SimpleNamespace

        from agentnode_sdk.worker.service import reconcile_what_was_left

        said = []
        bench = SimpleNamespace(
            journal=SimpleNamespace(unsettled=lambda: [("", "", "")]), worker=None)
        out = reconcile_what_was_left(bench, say=said.append)
        assert out["considered"] == 0
        assert any("no run id" in line for line in said)
