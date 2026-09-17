"""Every class this gateway stores has a period, and the sweep actually reaches every one.

A reviewer refused the earlier version because periods existed for two classes. The answer then
was that the rest expire by themselves, which is true and is not the criterion: a class that
expires on a schedule nobody chose has a retention period the operator cannot see or change.

So this file does three things a table alone cannot:

  * it checks the table against the files a REAL gateway writes, so a class that starts being
    stored without being listed fails here rather than being kept for ever;
  * it plants an old record in every class and requires the sweep to remove it;
  * it plants a fresh one beside it and requires the sweep to keep it, because a sweep that
    removed everything would pass the first check and be useless.
"""
from __future__ import annotations

import dataclasses
import json
import os
import time

import pytest

from agentnode_sdk.access import contract, dispatch
from agentnode_sdk.gateway import meter, observability, retention
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement
from tests.test_two_accounts import _a_customer, _a_run_by

LONG_AGO = 1_000_000.0
JUST_NOW = 9_000_000_000.0


@pytest.fixture()
def gateway(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    try:
        yield service
    finally:
        state.close()


def a_gateway_that_has_done_everything(gateway):
    """Drive a gateway until it has written every kind of record it can write.

    Shared with `test_backup_drill.py`, because both files need the same thing and for the same
    reason: a claim about EVERY class is worth nothing if it is made against a gateway that has
    only ever paired one device. Returns the customer it made.
    """
    from agentnode_sdk.gateway.admission import RateLimit
    from agentnode_sdk.gateway.allowance import Allowance, write_allowance

    who = _a_customer(gateway, "alice")
    _a_run_by(gateway, who)
    gateway.sessions.open(who.device_id, label="a browser")
    dispatch.dispatch("connections.enrol", {"way_in": contract.MCP, "label": "an AI"},
                      who, service=gateway)
    dispatch.dispatch("usage", {}, who, service=gateway)
    dispatch.dispatch("devices.invite", {}, who, service=gateway)
    observability.observe(gateway, observability.LocalFileSink(
        gateway.state.root / observability.EVENTS_NAME))
    retention.note_an_export(gateway.state.root, who.account_id, by="operator",
                             how_many_bytes=1)
    write_allowance(gateway.state.root, Allowance(requests_per_minute=50))
    RateLimit(gateway.state.root / "rate.json").spend(who.device_id, 50)
    for _ in range(100):
        if (gateway.state.root / "use-log.jsonl").exists():
            break
        time.sleep(0.05)
    return who


class TestTheTableIsComplete:

    def test_every_class_has_a_field_a_sweeper_and_a_description(self):
        names = set(retention.CLASSES)
        fields = {f.name[:-len("_days")] for f in dataclasses.fields(retention.Retention)}
        assert fields == names, "the table and the settings disagree: %s" % (fields ^ names)
        assert set(retention._SWEEPERS) == names, (
            "a class with no sweeper is a class kept for ever: %s"
            % (set(retention._SWEEPERS) ^ names))
        for name, what in retention.CLASSES.items():
            assert what["file"] and what["is"] and what["expiring_means"], name
            assert what["days"] > 0, "%s defaults to keeping for ever" % name

    def test_and_every_file_a_real_gateway_writes_is_in_it(self, gateway):
        """The check that catches a class added later. Drives the gateway, then reads the disk.

        The files that are NOT a retention class are named in `backup.BESIDES`, with what each
        one is, rather than in a list kept here. They are the same list -- a file with no age is
        exactly a file the sweep leaves alone and a restore still has to bring back -- and two
        copies of it would agree until the day one of them was updated.
        """
        from agentnode_sdk.gateway import backup

        a_gateway_that_has_done_everything(gateway)

        listed = {what["file"] for what in retention.CLASSES.values()}
        unaccounted = []
        for path in sorted(gateway.state.root.iterdir()):
            if not path.is_file() or path.name.endswith((".lock", ".new")) \
                    or path.name.startswith("."):
                continue
            if path.name in listed or path.name in backup.BESIDES:
                continue
            unaccounted.append(path.name)
        assert not unaccounted, (
            "this gateway writes %s, which is neither a retention class nor named as something "
            "that is not one. Add it to retention.CLASSES with a period, or to backup.BESIDES "
            "with why it has no age." % unaccounted)


class TestEveryClassIsActuallySwept:
    """Old goes, fresh stays. Both halves, for every class."""

    def _plant(self, root, name):
        """One old record and one fresh one, in whatever shape that class stores."""
        if name == "backups":
            # The one class that is not a file in the state directory: sealed archives live
            # somewhere else on purpose, because a copy on the same disk as the thing it copies
            # is not a backup. So the plant is a directory beside it plus the note that tells
            # this gateway where to look -- and the sweep is over FILES, not over lines.
            where = root / "sealed-elsewhere"
            where.mkdir(exist_ok=True)
            (root / "backups.json").write_text(
                json.dumps({"directory": str(where)}), encoding="utf-8")
            # THE LAYOUT THE SCRIPT ACTUALLY WRITES: one timestamped directory per run, holding
            # the sealed state, the sealed secrets, the sums and the manifest. The first version
            # of this planter invented a flatter one -- archives sitting directly in the
            # directory -- and the sweep passed against it while finding nothing on the real
            # alpha. A fixture that is easier to write than the thing it stands for will agree
            # with whatever the code does.
            for at, which in ((LONG_AGO, "old"), (JUST_NOW, "new")):
                run = where / ("2026%s" % which)
                run.mkdir(exist_ok=True)
                for leaf in ("state.tar.sealed", "secret.tar.sealed", "SHA256SUMS",
                             "WHAT_IS_IN_IT.json"):
                    made = run / leaf
                    made.write_bytes(b"AGENTNODE-SEALED-1 not a real archive")
                    os.utime(made, (at, at))
                os.utime(run, (at, at))
            return where

        path = root / retention.CLASSES[name]["file"]
        if name == "audit":
            path.write_text(
                json.dumps({"at": LONG_AGO, "operation": "usage", "account": "old"}) + "\n"
                + json.dumps({"at": JUST_NOW, "operation": "usage", "account": "new"}) + "\n",
                encoding="utf-8")
        elif name == "metering":
            for at, who in ((LONG_AGO, "acct-" + "0" * 16), (JUST_NOW, "acct-" + "1" * 16)):
                meter.record(root, run_id="r%d" % at, client_id="c" * 16, account_id=who,
                             started_at=at, finished_at=at, cpu=1.0, memory_mb=1,
                             wall_clock_s=1, state="finished", outcome="ok", bytes_out=1,
                             worker_topology="single-host-development", worker_id="w",
                             allowance_sha256="a" * 64, operator_policy_sha256="p" * 64,
                             operator_policy_version=1)
        elif name == "sessions":
            path.write_text(json.dumps({
                "old": {"client_id": "c", "opened_at": LONG_AGO, "ends_at": JUST_NOW},
                "new": {"client_id": "c", "opened_at": JUST_NOW, "ends_at": JUST_NOW},
            }), encoding="utf-8")
        elif name == "enrolments":
            path.write_text(json.dumps({
                "old": {"account": "a", "began_at": LONG_AGO, "expires_at": JUST_NOW},
                "new": {"account": "a", "began_at": JUST_NOW, "expires_at": JUST_NOW},
            }), encoding="utf-8")
        elif name == "ledger":
            path.write_text(json.dumps({
                "runs": {"old": {"first_seen": LONG_AGO}, "new": {"first_seen": JUST_NOW}},
                "nonces": {"old": LONG_AGO, "new": JUST_NOW},
                "challenges": {"old": {"x": 1}, "new": {"x": 1}},
            }), encoding="utf-8")
        elif name == "counters":
            path.write_text(json.dumps({
                "old": [{"run_id": "r", "at": LONG_AGO, "seconds": 0.0}],
                "new": [{"run_id": "r", "at": JUST_NOW, "seconds": 0.0}],
            }), encoding="utf-8")
        elif name == "invitations":
            path.write_text(json.dumps({
                "old": {"account_id": "a", "made_at": LONG_AGO, "expires_at": JUST_NOW},
                "new": {"account_id": "a", "made_at": JUST_NOW, "expires_at": JUST_NOW},
            }), encoding="utf-8")
        elif name == "rate":
            path.write_text(json.dumps({"old": [LONG_AGO], "new": [JUST_NOW]}), encoding="utf-8")
        elif name in ("events", "exports"):
            path.write_text(json.dumps({"at": LONG_AGO, "kind": "counts", "account_id": "old"})
                            + "\n"
                            + json.dumps({"at": JUST_NOW, "kind": "counts", "account_id": "new"})
                            + "\n", encoding="utf-8")
        else:                                                 # pragma: no cover - see the test
            raise AssertionError("nothing plants a record for %r" % name)
        return path

    def test_every_class_can_be_planted(self):
        """So a class added to the table without a planter fails here rather than silently."""
        planted = {"audit", "metering", "sessions", "enrolments", "ledger", "counters", "rate",
                   "events", "exports", "invitations", "backups"}
        assert planted == set(retention.CLASSES), (
            "this file does not plant a record for %s" % (planted ^ set(retention.CLASSES)))

    @pytest.mark.parametrize("name", sorted(retention.CLASSES))
    def test_the_old_one_goes_and_the_fresh_one_stays(self, gateway, name):
        root = gateway.state.root
        path = self._plant(root, name)
        retention.write_retention(root, retention.Retention(**{
            "%s_days" % each: 1 for each in retention.CLASSES}))

        # A moment just after the fresh record and long after the old one.
        done = retention.sweep(root, now=JUST_NOW + retention.DAY * 0.5)
        assert done["problems"] == [], done["problems"]
        assert done["swept"][name], "%s swept nothing at all" % name

        if path.is_dir():
            # `backups` is the one class whose records are FILES rather than lines in a file, so
            # what is read is the directory listing. The property asserted is identical: the old
            # one is gone and the fresh one is still there.
            written = " ".join(sorted(x.name for x in path.iterdir()))
            assert "new" in written, "%s swept the fresh one too: %s" % (name, written)
        else:
            written = path.read_text(encoding="utf-8")
        assert "old" not in written or name == "metering", (
            "%s kept a record older than its period: %s" % (name, written[:200]))
        if name == "metering":
            lines = [line for line in meter.read(root) if not meter.is_a_tombstone(line)]
            assert [line["account_id"] for line in lines] == ["acct-" + "1" * 16]
            assert meter.verify(root)["ok"], "sweeping the meter broke its chain"
        else:
            assert "new" in written, "%s swept the fresh record too" % name

    @pytest.mark.parametrize("name", sorted(retention.CLASSES))
    def test_a_period_of_zero_keeps_it(self, gateway, name):
        root = gateway.state.root
        self._plant(root, name)
        retention.write_retention(root, retention.Retention(**{
            "%s_days" % each: (0 if each == name else 1) for each in retention.CLASSES}))
        done = retention.sweep(root, now=JUST_NOW + retention.DAY * 0.5)
        assert done["swept"][name] == "kept indefinitely"

    def test_sweeping_twice_removes_nothing_the_second_time(self, gateway):
        root = gateway.state.root
        for name in retention.CLASSES:
            self._plant(root, name)
        retention.write_retention(root, retention.Retention(**{
            "%s_days" % each: 1 for each in retention.CLASSES}))
        first = retention.sweep(root, now=JUST_NOW + retention.DAY * 0.5)
        second = retention.sweep(root, now=JUST_NOW + retention.DAY * 0.5)
        assert first["problems"] == [] and second["problems"] == []
        for name in retention.CLASSES:
            assert second["swept"][name] == 0, "%s swept again on the second pass" % name

    def test_a_class_that_cannot_be_swept_is_NAMED_rather_than_counted(self, gateway):
        root = gateway.state.root
        (root / retention.CLASSES["ledger"]["file"]).write_text("{ truncated", encoding="utf-8")
        done = retention.sweep(root, now=JUST_NOW)
        assert any("ledger" in problem for problem in done["problems"]), done
        assert done["swept"]["ledger"] == "FAILED"
        # And the rest still happened: one damaged file does not stop the others.
        assert done["swept"]["audit"] == 0


class TestWhatAnOperatorSeesAndSets:

    def test_describe_lists_every_class_with_what_expiring_costs(self):
        rows = retention.describe()
        assert {row["name"] for row in rows} == set(retention.CLASSES)
        for row in rows:
            assert row["expiring_means"]

    def test_a_period_can_be_set_per_class_and_read_back(self, gateway):
        root = gateway.state.root
        retention.write_retention(root, retention.Retention(sessions_days=3, ledger_days=11))
        back = retention.read_retention(root)
        assert back.days_for("sessions") == 3 and back.days_for("ledger") == 11
        assert back.days_for("audit") == retention.CLASSES["audit"]["days"]

    def test_a_period_this_build_does_not_understand_is_refused(self, gateway):
        (gateway.state.root / retention.RETENTION_NAME).write_text(
            json.dumps({"audit_days": 5, "job_output_days": 5}), encoding="utf-8")
        with pytest.raises(retention.RetentionUnreadable):
            retention.read_retention(gateway.state.root)


class TestASweepThatCouldNotFinishIsNotRecordedAsDone:
    """A class that could not be swept used to look exactly like one with nothing to sweep.

    `sweep_if_due` wrote the marker either way, so a store gone read-only put the next attempt
    off for an hour and left the gateway reporting that it had swept -- while the data the
    operator set a period for stayed there. Two things have to be true instead: the sweep stays
    OWED, and what could not be done is written down where an operator reads it.
    """

    def _a_class_that_cannot_be_swept(self, gateway, monkeypatch):
        """One sweeper that fails. Not a damaged file: a damaged file is a different test, and
        this one is about what the RESULT does, whatever the cause was."""
        def refuses(*_args, **_kwargs):
            raise OSError("this store has gone read-only")

        monkeypatch.setitem(retention._SWEEPERS, "audit", refuses)

    def test_it_stays_owed(self, gateway, monkeypatch):
        root = gateway.state.root
        retention.write_retention(root, retention.Retention(audit_days=1))
        self._a_class_that_cannot_be_swept(gateway, monkeypatch)

        at = JUST_NOW
        done = retention.sweep_if_due(root, now=at)
        assert done is not None and done["problems"], done
        assert retention.due(root, at + 1.0), (
            "a sweep that could not finish put the next one off; the data it was meant to "
            "remove stays until somebody notices")

    def test_and_says_what_it_could_not_do(self, gateway, monkeypatch):
        root = gateway.state.root
        retention.write_retention(root, retention.Retention(audit_days=1))
        self._a_class_that_cannot_be_swept(gateway, monkeypatch)
        retention.sweep_if_due(root, now=JUST_NOW)

        last = retention.last_sweep(root)
        assert last["problems"], last
        assert "audit" in json.dumps(last["problems"])
        assert last["last_tried"] == JUST_NOW, last
        assert not last["at"], "a failed sweep must not count as a clean one"

    def test_and_a_clean_one_after_it_clears_both(self, gateway, monkeypatch):
        root = gateway.state.root
        retention.write_retention(root, retention.Retention(audit_days=1))
        self._a_class_that_cannot_be_swept(gateway, monkeypatch)
        retention.sweep_if_due(root, now=JUST_NOW)

        monkeypatch.undo()
        retention.write_retention(root, retention.Retention(audit_days=1))
        later = JUST_NOW + retention.SWEEP_EVERY_SECONDS + 1
        done = retention.sweep_if_due(root, now=later)
        assert done is not None and not done["problems"], done

        last = retention.last_sweep(root)
        assert last["at"] == later and not last["problems"], last
        assert not retention.due(root, later + 1.0)

    def test_and_the_operator_command_says_so(self, gateway, monkeypatch, capsys):
        from agentnode_sdk.cli import gateway_commands

        root = gateway.state.root
        retention.write_retention(root, retention.Retention(audit_days=1))
        self._a_class_that_cannot_be_swept(gateway, monkeypatch)
        retention.sweep_if_due(root, now=JUST_NOW)

        args = type("A", (), {"dir": str(root),
                              **{"%s_days" % name: None for name in retention.CLASSES}})()
        assert gateway_commands.cmd_keeps(args) == 0
        said = capsys.readouterr().out
        assert "The last sweep could not finish." in said, said
        assert "read-only" in said, said
        assert "try again" in said, said

    def test_and_says_plainly_when_it_has_never_swept(self, gateway, capsys):
        from agentnode_sdk.cli import gateway_commands

        args = type("A", (), {"dir": str(gateway.state.root),
                              **{"%s_days" % name: None for name in retention.CLASSES}})()
        assert gateway_commands.cmd_keeps(args) == 0
        assert "has not swept yet" in capsys.readouterr().out


class TestTheDocumentedPromiseAndTheConfiguredPeriodCannotDrift:
    """`ALPHA-R2-DECISION-DELETION-0002`, RISK-A-OPERATIONAL-DEPENDENCY:

        "The 35-day claim depends on correct backup-directory configuration, a retention value no
         greater than 35 days, and successful recurring sweeps."

    The dependency is real and cannot be removed -- an operator can raise the period, and then the
    promise is whatever they raised it to. What CAN be removed is the drift between the number in
    the customer-facing document and the number this product ships with, because that one is not
    an operator's choice, it is a mistake waiting to be made by whoever edits one and not the
    other.
    """

    def test_the_number_in_the_promise_is_the_number_in_the_table(self):
        import pathlib
        import re

        doc = (pathlib.Path(__file__).resolve().parent.parent / "docs" / "what-is-kept.md")
        said = doc.read_text(encoding="utf-8")
        promised = re.search(
            r"gone from every backup this gateway made within\s+(\d+)\s+days", said)
        assert promised, ("the deletion promise is not in docs/what-is-kept.md in the shape this "
                          "test can read. If the wording changed, change this test on purpose.")
        assert int(promised.group(1)) == retention.CLASSES["backups"]["days"], (
            "the customer is promised %s days and this gateway ships %s"
            % (promised.group(1), retention.CLASSES["backups"]["days"]))

    def test_a_period_of_zero_for_backups_is_the_promise_being_switched_off(self):
        """Zero means indefinitely everywhere in this table, and for backups that is the promise
        gone. It stays possible -- an operator may have a reason -- and it is REPORTED, so a sweep
        cannot read as "the backups were checked" when the answer is "they are kept for ever".
        """
        import pathlib
        import tempfile

        root = pathlib.Path(tempfile.mkdtemp())
        retention.write_retention(root, retention.Retention(backups_days=0))
        done = retention.sweep(root, now=JUST_NOW)
        assert done["swept"]["backups"] == "kept indefinitely"
        assert done["problems"] == []
