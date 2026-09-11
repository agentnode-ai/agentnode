"""What a client may consume, and the one thing that stops everything.

A gateway anybody can reach is a gateway anybody can exhaust. Until this existed the only thing
bounding a paired client was the wall clock of a single job, and the only way to stop everything
was to kill the process -- which loses the runs in flight rather than ending them.

The gateway here is real: a real service, a real HTTP server, real pairing, real signatures, and
the client library's own verification. What is replaced is the sandbox, because what is being
established is what is admitted and what is refused, and that is decided before anything runs.
"""
from __future__ import annotations

import json
import threading
import time

import os
from pathlib import Path
import pytest

from agentnode_sdk.gateway import client as gc
from agentnode_sdk.gateway import meter
from agentnode_sdk.gateway.allowance import (
    CEILING_SAYS,
    STOP_NAME,
    STOPPED_SAYS,
    Allowance,
    OverTheCeiling,
    Stopped,
    Use,
    read_allowance,
    start_again,
    stop_everything,
    why_it_is_stopped,
    write_allowance,
)
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService, make_server

from tests.test_em3c_gateway import StandInBackend, _granted, _paired, _store_measurement


@pytest.fixture()
def a_gateway(tmp_path):
    """A real gateway whose sandbox returns at once."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        state = GatewayState(td, version="test")
        backend = StandInBackend()
        service = GatewayService(state, backend=backend)
        _store_measurement(service)
        service.CONTAINER_APPEAR_SECONDS = 0.5
        server = make_server(service, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            yield base, state, service, backend
        finally:
            server.shutdown()
            thread.join(timeout=10)
            from agentnode_sdk.gateway.protocol import is_terminal

            for _ in range(300):
                if all(is_terminal(r.state) for r in list(service.runs.values())):
                    break
                time.sleep(0.05)


def a_run(conn, service, run_id, seconds=60):
    return gc.submit(conn, b"print('x')", granted=_granted(service, wall_clock_s=seconds),
                     run_id=run_id, wall_clock_s=seconds)


# ------------------------------------------------------------- the operator sets the ceilings


class TestTheOperatorSetsTheCeilings:

    def test_an_alpha_starts_with_none(self, tmp_path):
        assert read_allowance(tmp_path) == Allowance()
        assert read_allowance(tmp_path).concurrent_runs == 0

    def test_and_what_is_written_is_what_is_read(self, tmp_path):
        write_allowance(tmp_path, Allowance(concurrent_runs=2, runs_per_window=10,
                                            seconds_per_window=600))
        got = read_allowance(tmp_path)
        assert (got.concurrent_runs, got.runs_per_window, got.seconds_per_window) == (2, 10, 600)

    def test_a_file_that_is_not_one_does_not_invent_limits(self, tmp_path):
        (tmp_path / "allowance.json").write_text("not json", encoding="utf-8")
        assert read_allowance(tmp_path) == Allowance()

    def test_nothing_a_client_sends_reaches_it(self):
        """The limits come from a file in the gateway's own directory. A job cannot name one."""
        import inspect

        from agentnode_sdk.gateway.protocol import JobRequest

        fields = set(JobRequest.__dataclass_fields__)
        for name in ("concurrent_runs", "runs_per_window", "seconds_per_window", "allowance"):
            assert name not in fields, name
        source = inspect.getsource(GatewayService.allowance)
        assert "self.state.root" in source
        assert "request" not in source

    def test_it_is_read_at_admission_and_never_held(self, a_gateway):
        """A ceiling lowered while the gateway runs applies to the next job."""
        base, state, service, _backend = a_gateway
        assert service.allowance().runs_per_window == 0
        write_allowance(state.root, Allowance(runs_per_window=1))
        assert service.allowance().runs_per_window == 1

    def test_the_record_says_which_limits_were_in_force(self, a_gateway):
        base, state, service, _backend = a_gateway
        write_allowance(state.root, Allowance(runs_per_window=5))
        conn = _paired(base, state)
        a_run(conn, service, "bound")
        gc.wait_for(conn, "bound", timeout=20)
        lines = meter.read(state.root)
        assert lines and lines[-1]["allowance_sha256"] == service.allowance().digest()


# ------------------------------------------------------- use is counted and survives a restart


class TestUseIsCountedAndSurvives:

    def test_a_run_is_counted_before_it_runs(self, a_gateway):
        """A run nobody counted is one a client could have for free by crashing the gateway."""
        base, state, service, _backend = a_gateway
        conn = _paired(base, state)
        a_run(conn, service, "counted")
        who = state.client_id_for(conn.token)
        runs, _seconds = service.use.so_far(who)
        assert runs == 1

    def test_and_what_it_took_is_added_when_it_ends(self, a_gateway):
        base, state, service, _backend = a_gateway
        conn = _paired(base, state)
        a_run(conn, service, "timed")
        gc.wait_for(conn, "timed", timeout=20)
        who = state.client_id_for(conn.token)
        runs, seconds = service.use.so_far(who)
        assert runs == 1 and seconds >= 0.0

    def test_a_restart_does_not_forget(self, tmp_path):
        """It is on disk, so a new process sees what the old one counted."""
        first = Use(tmp_path / "use.json")
        first.note("client-a", "one")
        first.finished("client-a", "one", 12.0)
        again = Use(tmp_path / "use.json")
        assert again.so_far("client-a") == (1, 12.0)

    def test_use_is_forgotten_by_time(self, tmp_path):
        counting = Use(tmp_path / "use.json", window=100.0)
        counting.note("client-a", "old", now=1000.0)
        assert counting.so_far("client-a", now=1050.0)[0] == 1
        assert counting.so_far("client-a", now=1200.0)[0] == 0

    def test_and_never_by_how_many_arrived(self, tmp_path):
        """A counter an attacker could empty by sending enough is one they walk through."""
        counting = Use(tmp_path / "use.json", window=10_000.0)
        counting.note("client-a", "first", now=1000.0)
        for i in range(200):
            counting.note("client-a", "later-%d" % i, now=1000.0 + i)
        assert counting.so_far("client-a", now=1300.0)[0] == 201

    def test_two_clients_are_counted_apart(self, tmp_path):
        counting = Use(tmp_path / "use.json")
        counting.note("client-a", "one")
        counting.note("client-b", "two")
        counting.finished("client-b", "two", 30.0)
        assert counting.so_far("client-a")[1] == 0.0
        assert counting.so_far("client-b")[1] == 30.0

    def test_counting_is_under_a_lock_so_two_at_once_cannot_both_slip_through(self, tmp_path):
        counting = Use(tmp_path / "use.json")
        done = []

        def sending(n):
            counting.note("client-a", "run-%d" % n)
            done.append(n)

        threads = [threading.Thread(target=sending, args=(n,)) for n in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert len(done) == 20
        assert counting.so_far("client-a")[0] == 20, "a count was lost to a concurrent write"


# --------------------------------------------------------------- every ceiling refuses


class TestEveryCeilingRefuses:

    def test_more_runs_at_once_than_allowed(self, a_gateway):
        base, state, service, _backend = a_gateway
        write_allowance(state.root, Allowance(concurrent_runs=1))
        conn = _paired(base, state)
        # One that is still going, held by never letting the backend return.
        held = threading.Event()
        service.runs.clear()
        original = service.worker.run

        def waiting(job):
            held.wait(timeout=30)
            return original(job)

        service.worker.run = waiting
        try:
            a_run(conn, service, "going")
            for _ in range(200):
                if service.runs.get("going") and service.runs["going"].state == "running":
                    break
                time.sleep(0.05)
            second = a_run(conn, service, "one-too-many")
            assert second["state"] == "refused"
            assert CEILING_SAYS in second["refusal"]
            assert "concurrent_runs" in second["refusal"]
        finally:
            held.set()
            service.worker.run = original

    def test_more_runs_in_the_window_than_allowed(self, a_gateway):
        base, state, service, _backend = a_gateway
        write_allowance(state.root, Allowance(runs_per_window=2))
        conn = _paired(base, state)
        for n in range(2):
            answer = a_run(conn, service, "within-%d" % n)
            assert answer["state"] != "refused", answer.get("refusal")
            gc.wait_for(conn, "within-%d" % n, timeout=20)
        over = a_run(conn, service, "over")
        assert over["state"] == "refused"
        assert "runs_per_window" in over["refusal"]

    def test_more_seconds_than_allowed(self, a_gateway):
        base, state, service, _backend = a_gateway
        write_allowance(state.root, Allowance(seconds_per_window=30))
        conn = _paired(base, state)
        over = a_run(conn, service, "too-long", seconds=60)
        assert over["state"] == "refused"
        assert "seconds_per_window" in over["refusal"]

    def test_a_refusal_over_a_ceiling_is_told_apart_from_any_other(self, a_gateway):
        base, state, service, _backend = a_gateway
        write_allowance(state.root, Allowance(runs_per_window=1))
        conn = _paired(base, state)
        a_run(conn, service, "first")
        gc.wait_for(conn, "first", timeout=20)
        over = a_run(conn, service, "second")
        assert over["refusal"].startswith(CEILING_SAYS)

        # The other kind, from a gateway that is NOT over a ceiling -- otherwise every refusal
        # after the first would be the ceiling one and the test would be comparing it with itself.
        write_allowance(state.root, Allowance())
        malformed = gc.submit(conn, b"x", granted=_granted(service), run_id="third",
                              required_properties=("a_property_nobody_measured",))
        assert malformed["state"] == "refused"
        assert not malformed["refusal"].startswith(CEILING_SAYS)
        assert not malformed["refusal"].startswith(STOPPED_SAYS)

    def test_and_it_is_signed_like_every_other_answer(self, a_gateway):
        base, state, service, _backend = a_gateway
        write_allowance(state.root, Allowance(runs_per_window=1))
        conn = _paired(base, state)
        a_run(conn, service, "one")
        gc.wait_for(conn, "one", timeout=20)
        over = a_run(conn, service, "two")
        # `submit` verifies what comes back; an unsigned refusal would not have got here.
        assert over["state"] == "refused" and over.get("signature")

    def test_a_client_with_nothing_counted_against_it_is_not_pooled_with_everyone(self, tmp_path):
        """Counting an unidentified caller against "" would make every such caller share one
        allowance, and any of them could exhaust it for the rest."""
        import inspect

        source = inspect.getsource(GatewayService.within_its_allowance)
        assert "if not client_id" in source
        assert "return" in source


# ------------------------------------------------------------------ one thing stops everything


class TestOneThingStopsEverything:

    def test_a_stopped_gateway_refuses_everything(self, a_gateway):
        base, state, service, _backend = a_gateway
        conn = _paired(base, state)
        stop_everything(state.root, "upgrading the sandbox image")
        answer = a_run(conn, service, "while-stopped")
        assert answer["state"] == "refused"
        assert "upgrading the sandbox image" in answer["refusal"]
        assert answer["refusal"].startswith(STOPPED_SAYS)

    def test_and_takes_work_again_when_it_is_lifted(self, a_gateway):
        base, state, service, _backend = a_gateway
        conn = _paired(base, state)
        stop_everything(state.root, "briefly")
        assert a_run(conn, service, "no")["state"] == "refused"
        assert start_again(state.root) is True
        assert a_run(conn, service, "yes")["state"] != "refused"

    def test_lifting_something_that_was_not_stopped(self, tmp_path):
        assert start_again(tmp_path) is False

    def test_a_gateway_that_cannot_tell_treats_itself_as_stopped(self, tmp_path, monkeypatch):
        """Fail-closed. One that answered "not stopped" to a question it could not read would be
        answering a question nobody asked it."""
        stop_everything(tmp_path, "whatever")
        import pathlib

        real = pathlib.Path.read_text

        def refusing(self, *a, **k):
            if self.name == STOP_NAME:
                raise PermissionError("this file cannot be read")
            return real(self, *a, **k)

        monkeypatch.setattr(pathlib.Path, "read_text", refusing)
        said = why_it_is_stopped(tmp_path)
        assert said, "a gateway that cannot tell whether it is stopped carried on"
        assert "cannot tell" in said

    def test_a_stop_file_that_says_nothing_still_stops(self, tmp_path):
        (tmp_path / STOP_NAME).write_text("{}", encoding="utf-8")
        assert why_it_is_stopped(tmp_path)

    def test_nothing_a_client_sends_can_lift_it(self):
        import inspect

        from agentnode_sdk.gateway import allowance

        lifting = inspect.getsource(allowance.start_again)
        assert "request" not in lifting and "token" not in lifting
        # And the only caller is the operator's command.
        from agentnode_sdk.cli import gateway_commands

        assert "start_again" in inspect.getsource(gateway_commands.cmd_resume)
        assert "start_again" not in inspect.getsource(GatewayService)

    def test_runs_already_going_are_left_to_finish(self, a_gateway):
        """Stopping one of those is a cancellation, and a stop is not one."""
        base, state, service, _backend = a_gateway
        conn = _paired(base, state)
        a_run(conn, service, "already-going")
        stop_everything(state.root, "stopped mid-flight")
        final = gc.wait_for(conn, "already-going", timeout=20)
        assert final["state"] == "finished"


# ----------------------------------------------------------------- a record that keeps no secret


class TestARecordOfUseCarriesNoSecret:

    def test_one_line_per_run(self, a_gateway):
        base, state, service, _backend = a_gateway
        conn = _paired(base, state)
        for n in range(3):
            a_run(conn, service, "run-%d" % n)
            gc.wait_for(conn, "run-%d" % n, timeout=20)
        assert len(meter.read(state.root)) == 3

    def test_it_says_who_used_what(self, a_gateway):
        base, state, service, _backend = a_gateway
        conn = _paired(base, state)
        a_run(conn, service, "measured")
        gc.wait_for(conn, "measured", timeout=20)
        line = meter.read(state.root)[-1]
        assert line["client_id"] == state.client_id_for(conn.token)
        assert line["run_id"] == "measured"
        assert line["seconds"] >= 0.0
        assert line["state"] == "finished"

    def test_and_carries_no_secret_this_gateway_holds(self, a_gateway):
        """Walked against the real secrets of a real gateway, not against a list of names."""
        base, state, service, backend = a_gateway
        conn = _paired(base, state)
        a_run(conn, service, "no-secrets")
        gc.wait_for(conn, "no-secrets", timeout=20)
        written = (state.root / meter.METER_NAME).read_text(encoding="utf-8")

        # The client id is NOT a secret -- naming who used what is the whole point, and a meter
        # that could not would be one nobody could read. Everything else this gateway holds is.
        whose = state.client_id_for(conn.token)
        assert whose and whose in written, "the meter does not say who used it"

        secrets = {conn.token}
        for name in ("tokens.json", "identity.json"):
            try:
                body = json.loads((state.root / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            # Both the keys (which are token digests) and the values (which are everything else
            # a gateway keeps about a client and about itself).
            def gather(thing):
                if isinstance(thing, dict):
                    for key, value in thing.items():
                        secrets.add(str(key))
                        gather(value)
                elif isinstance(thing, list):
                    for item in thing:
                        gather(item)
                elif isinstance(thing, str):
                    secrets.add(thing)

            gather(body)
        looked_at = 0
        for secret in secrets:
            if len(secret) < 16 or secret == whose:
                continue
            looked_at += 1
            assert secret not in written, secret[:24]
        assert looked_at >= 2, "this test looked at almost nothing and would pass on anything"

    def test_nor_anything_the_job_wrote(self, a_gateway):
        base, state, service, backend = a_gateway
        conn = _paired(base, state)
        a_run(conn, service, "quiet")
        gc.wait_for(conn, "quiet", timeout=20)
        written = (state.root / meter.METER_NAME).read_text(encoding="utf-8")
        assert "RAN" not in written and "print(" not in written

    def test_there_is_nowhere_to_put_anything_else(self):
        """A meter with somewhere for "anything else" is one that will hold a secret."""
        import inspect

        taken = inspect.signature(meter.record).parameters
        assert set(taken) - {"root"} == set(meter.FIELDS) - {"seconds"}
        for name, parameter in taken.items():
            assert parameter.kind is not parameter.VAR_KEYWORD, name

    def test_it_is_written_for_its_owner_and_nobody_else(self, a_gateway):
        import os
        import sys

        base, state, service, _backend = a_gateway
        conn = _paired(base, state)
        a_run(conn, service, "private")
        gc.wait_for(conn, "private", timeout=20)
        if sys.platform == "win32":
            pytest.skip("this platform's file modes are advisory; the Linux lane checks them")
        assert (os.stat(state.root / meter.METER_NAME).st_mode & 0o077) == 0

    def test_what_an_operator_asks_of_it(self, a_gateway):
        base, state, service, _backend = a_gateway
        conn = _paired(base, state)
        for n in range(2):
            a_run(conn, service, "sum-%d" % n)
            gc.wait_for(conn, "sum-%d" % n, timeout=20)
        totals = meter.summarise(state.root)
        who = state.client_id_for(conn.token)
        assert totals[who]["runs"] == 2


class TestWhatThisDoesNotEstablish:

    def test_the_limits_are_where_a_reader_will_meet_them(self):
        import inspect

        from agentnode_sdk.gateway import allowance

        said = " ".join((inspect.getdoc(allowance) or "").split())
        for phrase in ("Counting use is not billing", "bounds a PAIRED client",
                       "protect the MACHINE from a client",
                       "do not protect clients from each other"):
            assert phrase in said, phrase

    def test_and_the_meter_says_it_is_not_charging(self):
        import inspect

        said = " ".join((inspect.getdoc(meter) or "").split())
        assert "it is not billing" in said
        assert "prices nothing" in said


def _a_running_record(service, run_id):
    """A run this gateway believes is going, without a container behind it."""
    from agentnode_sdk.gateway.server import RunRecord

    record = RunRecord(run_id=run_id, job_id="j-" + run_id)
    record.state = "running"
    record.container_name = "agentnode-em3c-" + run_id
    service.runs[run_id] = record
    return record


class _WorkerThatNeverSettles:
    """Asked to stop, it says it asked. The run does not become terminal."""

    def stop(self, run_id, container_name, appear_seconds):
        return True


class TestTheStopReachesWhatIsAlreadyRunning:
    """A switch that leaves the current job running is not the switch an operator reached for.

    The stop used to mean "admit nothing new". But the reason to stop a gateway AT ONCE is
    usually what is running on it at that moment -- an image being replaced underneath it, a
    client doing something that must not continue, a host that has to be freed. Leaving that
    going is the one case the operator was trying to prevent.
    """

    @pytest.fixture()
    def service(self, tmp_path):
        state = GatewayState(str(tmp_path), version="test")
        made = GatewayService(state, backend=StandInBackend())
        made._worker = _WorkerThatNeverSettles()
        return made

    def test_every_run_that_had_not_ended_is_ended(self, service):
        going = [_a_running_record(service, "r%d" % i) for i in range(3)]
        done = service.stop_what_is_running("upgrading the sandbox image", settle=0.05)
        assert len(done) == 3
        for record in going:
            assert record.cancel_requested.is_set(), "this one was left running"

    def test_and_each_is_told_what_ended_it(self, service):
        record = _a_running_record(service, "why")
        service.stop_what_is_running("the operator stopped this gateway", settle=0.05)
        assert record.halted_by == "the operator stopped this gateway"
        assert record.public()["halted_by"] == "the operator stopped this gateway"

    def test_a_run_that_had_already_ended_is_left_alone(self, service):
        """Idempotent: stopping twice must not re-end anything, or count it twice."""
        finished = _a_running_record(service, "done")
        finished.state = "finished"
        assert service.stop_what_is_running("x", settle=0.05) == []
        assert not finished.cancel_requested.is_set()

    def test_one_that_will_not_settle_is_not_counted_as_stopped(self, service):
        """Claiming more than happened is exactly what a fail-closed switch must not do."""
        _a_running_record(service, "stuck")
        done = service.stop_what_is_running("x", settle=0.05)
        assert done and done[0]["stopped"] is False

    def test_and_one_that_settles_is(self, service):
        record = _a_running_record(service, "quick")

        class Settles:
            def stop(self, run_id, container_name, appear_seconds):
                record.state = "cancelled"
                return True

        service._worker = Settles()
        done = service.stop_what_is_running("x", settle=2.0)
        assert done and done[0]["stopped"] is True

    def test_one_that_raises_does_not_stop_the_others(self, service):
        """One run that cannot be reached must not leave the rest running."""
        _a_running_record(service, "first")
        second = _a_running_record(service, "second")

        class Awkward:
            def __init__(self):
                self.calls = 0

            def stop(self, run_id, container_name, appear_seconds):
                self.calls += 1
                if self.calls == 1:
                    raise OSError("the runtime is not answering")
                return True

        service._worker = Awkward()
        done = service.stop_what_is_running("x", settle=0.05)
        assert len(done) == 2
        assert any("error" in r for r in done), done
        assert second.cancel_requested.is_set()

    def test_the_gateway_acts_on_the_file_and_not_on_a_call(self):
        """The CLI and the gateway are different processes; the file is all they share."""
        import inspect

        from agentnode_sdk.gateway import server as server_module

        text = inspect.getsource(server_module.make_server)
        assert "_watch_the_stop" in text
        assert "why_it_is_stopped" in text


class TestTheRecordOfUseCanBeShownNotToHaveChanged:
    """A record that can be edited without trace is not a record, it is a note.

    Each line carries the digest of the line before it and a signature over both, so removing a
    line, reordering two, or changing a number in one shows up -- and `verify` says where it
    first stops agreeing, because a reader told "40 and 700 are wrong" cannot tell whether the
    second is a consequence of the first.
    """

    def _log(self, root, how_many=4):
        from agentnode_sdk.gateway import meter

        for i in range(how_many):
            meter.record(root, run_id="run%d" % i, client_id="c1", started_at=1.0,
                         finished_at=2.0, cpu=1.0, memory_mb=512, wall_clock_s=60,
                         state="finished", outcome="succeeded", bytes_out=10,
                         worker_topology="single-host-development", allowance_sha256="a" * 64)
        return meter

    def _rows(self, meter, root):
        return [json.loads(l) for l in
                (Path(root) / meter.METER_NAME).read_text(encoding="utf-8").splitlines()
                if l.strip()]

    def _put(self, meter, root, rows):
        (Path(root) / meter.METER_NAME).write_text(
            "\n".join(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in rows) + "\n",
            encoding="utf-8")

    def test_a_log_nobody_touched_verifies(self, tmp_path):
        meter = self._log(tmp_path)
        held = meter.verify(tmp_path)
        assert held["ok"] is True
        assert held["lines"] == 4

    def test_changing_one_number_is_seen(self, tmp_path):
        meter = self._log(tmp_path)
        rows = self._rows(meter, tmp_path)
        rows[1]["bytes_out"] = 999999
        self._put(meter, tmp_path, rows)
        held = meter.verify(tmp_path)
        assert held["ok"] is False
        assert held["at"] == 2

    def test_taking_a_line_out_is_seen(self, tmp_path):
        meter = self._log(tmp_path)
        rows = self._rows(meter, tmp_path)
        self._put(meter, tmp_path, rows[:1] + rows[2:])
        assert meter.verify(tmp_path)["ok"] is False

    def test_putting_two_in_the_other_order_is_seen(self, tmp_path):
        meter = self._log(tmp_path)
        rows = self._rows(meter, tmp_path)
        self._put(meter, tmp_path, rows[:2][::-1] + rows[2:])
        assert meter.verify(tmp_path)["ok"] is False

    def test_and_putting_it_back_exactly_verifies_again(self, tmp_path):
        """So the check is about the bytes, not about having been touched."""
        meter = self._log(tmp_path)
        rows = self._rows(meter, tmp_path)
        self._put(meter, tmp_path, rows[:2])
        assert meter.verify(tmp_path)["ok"] is False
        self._put(meter, tmp_path, rows)
        assert meter.verify(tmp_path)["ok"] is True

    def test_cutting_the_tail_off_is_seen(self, tmp_path):
        """The one a chain alone cannot catch, and the obvious way to hide recent use.

        Every prefix of a hash chain is itself a valid chain, so a truncated log verifies
        perfectly against itself. Nothing inside a file can say how long that file should be,
        which is why where it ends is written down beside it and signed.
        """
        meter = self._log(tmp_path)
        rows = self._rows(meter, tmp_path)
        self._put(meter, tmp_path, rows[:1])
        held = meter.verify(tmp_path)
        assert held["ok"] is False
        assert "taken off the end" in held["detail"]
        assert "3 line(s)" in held["detail"], held["detail"]

    def test_and_the_note_saying_where_it_ends_cannot_be_forged(self, tmp_path):
        """Otherwise whoever cut the tail off would simply rewrite it to match."""
        meter = self._log(tmp_path)
        rows = self._rows(meter, tmp_path)
        self._put(meter, tmp_path, rows[:1])
        (Path(tmp_path) / meter.HEAD_NAME).write_text(
            json.dumps({"seq": 1, "digest": "0" * 64, "signature": "aa" * 64}), encoding="utf-8")
        held = meter.verify(tmp_path)
        assert held["ok"] is False
        assert "not signed by this gateway" in held["detail"]

    def test_a_log_with_no_note_at_all_is_not_called_whole(self, tmp_path):
        meter = self._log(tmp_path)
        (Path(tmp_path) / meter.HEAD_NAME).unlink()
        held = meter.verify(tmp_path)
        assert held["ok"] is False
        assert "taken off the end would not show" in held["detail"]

    def test_a_line_appended_by_something_without_the_key_is_seen(self, tmp_path):
        """The case that matters: somebody adding use that never happened."""
        meter = self._log(tmp_path)
        rows = self._rows(meter, tmp_path)
        forged = dict(rows[-1])
        forged.update(seq=len(rows) + 1, run_id="never-ran", prev="0" * 64)
        self._put(meter, tmp_path, rows + [forged])
        held = meter.verify(tmp_path)
        assert held["ok"] is False
        assert held["at"] == len(rows) + 1

    def test_a_log_with_no_public_key_beside_it_is_not_evidence(self, tmp_path):
        """Unverifiable must not read the same as verified."""
        meter = self._log(tmp_path)
        (Path(tmp_path) / meter.METER_PUBLIC_NAME).unlink()
        held = meter.verify(tmp_path)
        assert held["ok"] is False
        assert "cannot be checked" in held["detail"]

    def test_the_private_half_is_never_beside_the_public_one_in_the_open(self, tmp_path):
        meter = self._log(tmp_path)
        key = Path(tmp_path) / meter.METER_KEY_NAME
        assert key.exists()
        if os.name != "nt":
            assert (key.stat().st_mode & 0o077) == 0, "the meter key is readable by others"

    def test_nothing_in_a_line_is_a_secret(self, tmp_path):
        """The chain must not have smuggled anything in beside the counts."""
        meter = self._log(tmp_path)
        blob = json.dumps(self._rows(meter, tmp_path))
        for bad in ("token", "PRIVATE", "BEGIN", "code"):
            assert bad not in blob

    def test_what_it_does_not_establish_is_written_down(self):
        """A log cannot be evidence against the thing that writes it, and this says so."""
        from agentnode_sdk.gateway import meter

        assert "tamper-EVIDENT" in meter.__doc__
        assert "does NOT establish" in meter.__doc__


class TestAClientIsToldTheSandboxWasStoppedRatherThanBlamingItsCode:
    """"cancelled" on its own reads as something the person did.

    A person told their run was cancelled goes and looks at their code. A person told the sandbox
    was stopped by whoever runs it goes and asks them. The difference is one field, and it only
    helps if it reaches the client rather than staying in the gateway.
    """

    def test_the_reason_reaches_the_client(self):
        from agentnode_sdk.gateway.server import RunRecord

        record = RunRecord(run_id="r" * 32, job_id="j")
        record.state = "cancelled"
        record.halted_by = "replacing the sandbox image"
        assert record.public()["halted_by"] == "replacing the sandbox image"

    def test_and_the_client_says_it_instead_of_the_bare_state(self, capsys):
        import inspect

        from agentnode_sdk.cli import remote_commands

        text = inspect.getsource(remote_commands)
        assert 'halted_by' in text
        assert "stopped by whoever runs it" in text
        assert "Nothing about your code is known from this" in text

    def test_an_ordinary_cancellation_still_reads_as_one(self):
        """The counter-case: this must not turn every cancellation into an operator's doing."""
        from agentnode_sdk.gateway.server import RunRecord

        record = RunRecord(run_id="r" * 32, job_id="j")
        record.state = "cancelled"
        assert record.public()["halted_by"] == ""


class TestALogThatStartedBeforeTheChainDid:
    """Signing old lines now would be this gateway vouching for what it did not record then.

    So they are not signed, they are named, and the part of the file that is evidence is
    separated from the part that is not. What must not happen is that an unchained line becomes a
    way around the chain.
    """

    def _mixed(self, tmp_path, before=2, after=3):
        from agentnode_sdk.gateway import meter

        path = Path(tmp_path) / meter.METER_NAME
        old = [{"run_id": "old%d" % i, "client_id": "c", "started_at": 1.0, "finished_at": 2.0,
                "seconds": 1.0, "cpu": 1.0, "memory_mb": 512, "wall_clock_s": 60,
                "state": "finished", "outcome": "succeeded", "bytes_out": 1,
                "worker_topology": "x", "allowance_sha256": "a" * 64} for i in range(before)]
        path.write_text("\n".join(json.dumps(o, sort_keys=True, separators=(",", ":"))
                                  for o in old) + "\n", encoding="utf-8")
        for i in range(after):
            meter.record(tmp_path, run_id="new%d" % i, client_id="c", started_at=1.0,
                         finished_at=2.0, cpu=1.0, memory_mb=512, wall_clock_s=60,
                         state="finished", outcome="succeeded", bytes_out=1,
                         worker_topology="x", allowance_sha256="a" * 64)
        return meter

    def test_the_chained_part_checks_out_and_the_rest_is_named(self, tmp_path):
        meter = self._mixed(tmp_path)
        held = meter.verify(tmp_path)
        assert held["ok"] is True
        assert held["unchecked"] == 2
        assert "cannot be checked at all" in held["detail"]

    def test_a_log_whose_every_signature_was_stripped_is_not_called_verified(self, tmp_path):
        """The attack this shape invites: if unchained lines are tolerated, strip them all.

        Two separate things refuse it, and they are worth telling apart. The head says the log is
        supposed to end at line N, which no amount of stripping changes -- that is the
        PROTECTION. The check for "nothing here is chained at all" is what makes the ANSWER say
        so, instead of reporting it as lines taken off the end. This asserts the answer, because
        the protection is asserted by the truncation tests.
        """
        meter = self._mixed(tmp_path, before=0, after=3)
        path = Path(tmp_path) / meter.METER_NAME
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        bare = [{k: v for k, v in r.items() if k not in ("seq", "prev", "signature")}
                for r in rows]
        path.write_text(
            "\n".join(json.dumps(r, sort_keys=True, separators=(",", ":"))
                      for r in bare) + "\n", encoding="utf-8")
        held = meter.verify(tmp_path)
        assert held["ok"] is False
        assert "before this gateway kept a chain" in held["detail"], held["detail"]

    def test_a_log_that_is_all_from_before_is_not_called_verified(self, tmp_path):
        """A genuinely old log, with no key and no head, is also not evidence."""
        meter = self._mixed(tmp_path, before=3, after=0)
        held = meter.verify(tmp_path)
        assert held["ok"] is False

    def test_an_unchained_line_in_the_middle_is_not_allowed(self, tmp_path):
        """They are tolerated at the FRONT only; later on, one means a line was replaced."""
        meter = self._mixed(tmp_path, before=1, after=3)
        path = Path(tmp_path) / meter.METER_NAME
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        stripped = {k: v for k, v in rows[2].items() if k not in ("seq", "prev", "signature")}
        rows[2] = stripped
        path.write_text("\n".join(json.dumps(r, sort_keys=True, separators=(",", ":"))
                                  for r in rows) + "\n", encoding="utf-8")
        assert meter.verify(tmp_path)["ok"] is False

    def test_and_the_count_of_what_is_evidence_is_honest(self, tmp_path):
        meter = self._mixed(tmp_path, before=2, after=3)
        held = meter.verify(tmp_path)
        assert held["lines"] == 5
        assert held["unchecked"] == 2
