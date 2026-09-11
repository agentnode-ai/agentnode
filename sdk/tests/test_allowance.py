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
