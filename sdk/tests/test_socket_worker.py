"""The line between the control plane and the worker: what crosses it and what is refused.

`ALPHA-BOUNDARY-0001` decided foreign code belongs on a different machine from the process holding
every client's token material and this gateway's signing identity. The machine is not bought yet,
so the two are separate ACCOUNTS on one host, talking over a socket. That is not isolation and is
not claimed to be -- what it is, is the arrangement that makes the later move a change of one
address string.

The socket itself needs a unix socket to exist, which Windows does not have here, so those tests
skip on this machine and are required on the Linux lane. Everything that is not the socket -- the
protocol, what a receiver believes and in what order, the dispatch, and how a caller reads a
refusal -- runs everywhere, because none of it needs a kernel's help.
"""
from __future__ import annotations

import json
import os
import socket
import stat
import struct
import threading
import time
from types import SimpleNamespace

import pytest

from agentnode_sdk.worker import (
    Ceilings,
    SEPARATE_WORKER_HOST,
    SINGLE_HOST_DEVELOPMENT,
    CouldNotRestrictTheNetwork,
    Gone,
    Isolation,
    Job,
    JobFailed,
    Limits,
    Outcome,
    Worker,
    WorkerUnreachable,
)
from agentnode_sdk.worker import protocol as wire
from agentnode_sdk.worker.remote import RUN_MARGIN_SECONDS, SocketWorker, topology_of
from agentnode_sdk.worker import service
from agentnode_sdk.worker.service import DIRECTORY_MODE, SOCKET_MODE, Bench

HAS_UNIX = hasattr(socket, "AF_UNIX")
needs_unix = pytest.mark.skipif(not HAS_UNIX, reason="this machine has no unix sockets")

KEY = b"k" * 48
OTHER = b"o" * 48


def a_job(**changes) -> Job:
    made = dict(run_id="r" * 32, container_name="agentnode-em3c-abcd",
                command=("python", "-c", "print(1)"), artifact=b"print(1)",
                stdin="", network="none", allowed_domains=(), limits=Limits())
    made.update(changes)
    return Job(**made)


class AWorkerThatAnswers(Worker):
    """A worker with no runtime behind it, so the tests are about the line and not about docker."""

    topology = SINGLE_HOST_DEVELOPMENT

    def __init__(self) -> None:
        self.ran: list[Job] = []
        self.stopped: list[tuple] = []
        self.raises: Exception | None = None

    #: What this double says when asked to show a ceiling binds. A double that answered True
    #: unconditionally would make every test here pass through the gate without exercising it,
    #: so it is a field: one test sets it to False and expects to be refused.
    ceilings = True

    def prove_its_ceilings(self, *, megabytes=0, run_id=""):
        return Ceilings(held=self.ceilings,
                        reason="" if self.ceilings else "nothing stopped the allocation")

    def instance_label(self):
        return "AWorkerThatAnswers"

    def image_digest(self):
        return "sha256:" + "1" * 64

    def configuration_sha256(self):
        return "c" * 64

    def can_it_isolate(self):
        return Isolation(available=True, backend="docker", reason="",
                         measured=("container_isolation",))

    def runtime_version(self):
        return "29.8.0"

    def measure(self, *, generated_at, options, egress_matrix, egress_expected):
        return {"measured": True, "generated_at": generated_at}

    def measure_egress(self, *, allowed, denied):
        return {"allowed": list(allowed), "denied": denied}

    def run(self, job):
        if self.raises is not None:
            raise self.raises
        self.ran.append(job)
        return Outcome(exit_code=0, stdout="RAN", stderr="", reason="exited",
                       runtime_platform="a-stand-in")

    def stop(self, run_id, container_name, appear_seconds):
        self.stopped.append((run_id, container_name, appear_seconds))
        return True

    def gone(self, container_name, patiently=True):
        return Gone(answered=True, left=())


# ------------------------------------------------------- what a message is, and in what order


class TestAMessageIsAuthenticatedBeforeItIsRead:

    def test_a_sealed_message_comes_back_out(self):
        body = wire.request("describe", {}, deadline=time.time() + 30)
        frame = wire.seal(body, KEY)
        (length,) = struct.unpack(">I", frame[:4])
        assert wire.unseal(frame[36:], frame[4:36], KEY) == body
        assert length == len(frame) - 36

    def test_a_message_written_with_another_key_is_refused(self):
        frame = wire.seal(wire.request("describe", {}, deadline=time.time() + 30), OTHER)
        with pytest.raises(wire.ProtocolError) as caught:
            wire.unseal(frame[36:], frame[4:36], KEY)
        assert caught.value.code == wire.UNAUTHENTICATED

    def test_the_parser_is_never_reached_without_the_key(self):
        """The order, established rather than described. The same unparseable bytes give
        UNAUTHENTICATED with the wrong key and only MALFORMED with the right one -- so what a
        stranger reaches is the comparison, not the parser."""
        rubbish = b"{ this is not json"
        right = wire.seal({"x": 1}, KEY)[4:36]                # a MAC of something else entirely
        with pytest.raises(wire.ProtocolError) as stranger:
            wire.unseal(rubbish, right, KEY)
        assert stranger.value.code == wire.UNAUTHENTICATED

        import hashlib
        import hmac as _hmac

        ours = _hmac.new(KEY, rubbish, hashlib.sha256).digest()
        with pytest.raises(wire.ProtocolError) as holder:
            wire.unseal(rubbish, ours, KEY)
        assert holder.value.code == wire.MALFORMED

    def test_a_frame_larger_than_anyone_may_send_is_refused_before_it_is_allocated_for(self):
        class Announcing:
            def __init__(self):
                self.asked = []

            def read(self, count):
                self.asked.append(count)
                return struct.pack(">I", wire.MAX_FRAME + 1)[:count]

        stream = Announcing()
        with pytest.raises(wire.ProtocolError) as caught:
            wire.read_frame(stream, KEY)
        assert caught.value.code == wire.TOO_LARGE
        assert stream.asked == [4], "it read past the length it had already refused"

    def test_sealing_something_too_large_is_refused_at_the_sender(self):
        with pytest.raises(wire.ProtocolError) as caught:
            wire.seal({"x": "y" * (wire.MAX_FRAME + 10)}, KEY)
        assert caught.value.code == wire.TOO_LARGE

    def test_a_connection_that_ends_early_is_not_a_small_message(self):
        class Ending:
            def __init__(self):
                self.left = struct.pack(">I", 100)

            def read(self, count):
                got, self.left = self.left[:count], self.left[count:]
                return got

        with pytest.raises(wire.ProtocolError) as caught:
            wire.read_frame(Ending(), KEY)
        assert caught.value.code == wire.MALFORMED
        assert "ended after" in caught.value.detail

    def test_a_version_this_build_does_not_speak(self):
        body = wire.request("describe", {}, deadline=time.time() + 30)
        body["protocol"] = "agentnode-worker/99"
        with pytest.raises(wire.ProtocolError) as caught:
            wire.check(body, wire.Seen())
        assert caught.value.code == wire.MALFORMED
        assert "guessed" in str(caught.value)


class TestTheSameMessageTwiceIsRefused:

    def test_a_repeat_is_seen(self):
        seen = wire.Seen()
        body = wire.request("describe", {}, deadline=time.time() + 30)
        wire.check(body, seen)
        with pytest.raises(wire.ProtocolError) as caught:
            wire.check(body, seen)
        assert caught.value.code == wire.REPLAY

    def test_two_messages_are_two_messages(self):
        seen = wire.Seen()
        for _ in range(50):
            wire.check(wire.request("describe", {}, deadline=time.time() + 30), seen)

    def test_forgetting_is_by_time_and_not_by_how_many_arrived(self):
        """A cache that forgot the oldest when it filled would be one an attacker empties by
        sending enough, and then replays into."""
        seen = wire.Seen(memory=100.0)
        first = wire.request("describe", {}, deadline=1200.0, now=1000.0)
        wire.check(first, seen, now=1000.0)
        for i in range(500):
            wire.check(wire.request("describe", {}, deadline=1200.0, now=1000.0 + i * 0.01),
                       seen, now=1000.0 + i * 0.01)
        with pytest.raises(wire.ProtocolError) as caught:
            wire.check(first, seen, now=1005.0)
        assert caught.value.code == wire.REPLAY

    def test_and_it_does_forget_eventually(self):
        """On a clock nobody can set. Moving the wall clock is not how a receiver forgets."""
        ticking = [0.0]
        seen = wire.Seen(memory=10.0, elapsed=lambda: ticking[0])
        body = wire.request("describe", {}, deadline=2000.0, now=1000.0)
        wire.check(body, seen, now=1000.0)
        ticking[0] = 100.0                                    # a hundred seconds really elapsed
        body["issued_at"] = 1100.0
        wire.check(body, seen, now=1100.0)

    def test_and_moving_the_wall_clock_forward_forgets_nothing(self):
        """The defence against replay must not be removable by setting a clock.

        Forgetting used to be measured against the wall clock: one forward jump aged every
        remembered nonce out of the window at once. An attacker who could move a clock -- or wait
        for an NTP correction -- emptied the memory without forging anything, and a message
        captured before the jump was accepted again after it.
        """
        seen = wire.Seen(memory=10.0, elapsed=lambda: 0.0)    # no time has really passed
        body = wire.request("describe", {}, deadline=2000.0, now=1000.0)
        wire.check(body, seen, now=1000.0)
        # A year of wall clock goes by in one step, and nothing real has elapsed.
        for leap in (1100.0, 100000.0, 1000.0 + 365 * 86400):
            with pytest.raises(wire.ProtocolError) as caught:
                wire.check(dict(body, deadline=leap + 30), seen, now=leap)
            assert caught.value.code in (wire.REPLAY, wire.STALE)
        assert len(seen) == 1, "the memory was emptied by a clock"

    def test_the_whole_attack_end_to_end(self):
        """Capture, make it forget, put the clock back, send it again."""
        elapsed = [0.0]
        seen = wire.Seen(memory=120.0, elapsed=lambda: elapsed[0])
        captured = wire.request("describe", {}, deadline=1030.0, now=1000.0)
        wire.check(dict(captured), seen, now=1000.0)

        # Forward, to make it forget. Something has to arrive for any eviction to run at all.
        try:
            wire.check(wire.request("describe", {}, deadline=87430.0, now=87400.0),
                       seen, now=87400.0)
        except wire.ProtocolError:
            pass
        # Back where it started. The captured message is inside its freshness window again.
        with pytest.raises(wire.ProtocolError) as caught:
            wire.check(dict(captured), seen, now=1000.0)
        assert caught.value.code == wire.REPLAY


class TestWhatAReceiverWillNotGoBackBefore:
    """`Seen` cannot outlive its process; this is the part that does.

    A monotonic clock restarts with the process, so on its own the memory leaves one sequence
    open: capture a message, wait for a restart, set the clock back, send it again. Every check
    would pass -- the MAC still verifies, nothing is remembered, and a clock that has moved back
    makes it fresh with an unexpired deadline.
    """

    def test_a_message_older_than_what_was_already_accepted(self, tmp_path):
        floor = wire.Floor(tmp_path / "floor.json")
        seen = wire.Seen(elapsed=lambda: 0.0)
        wire.check(wire.request("describe", {}, deadline=1030.0, now=1000.0), seen,
                   now=1000.0, floor=floor)
        with pytest.raises(wire.ProtocolError) as caught:
            wire.check(wire.request("describe", {}, deadline=530.0, now=500.0),
                       wire.Seen(elapsed=lambda: 0.0), now=500.0, floor=floor)
        assert caught.value.code == wire.ROLLED_BACK

    def test_and_it_survives_the_restart_that_empties_the_memory(self, tmp_path):
        """The whole point: a new process, remembering nothing, still refuses."""
        path = tmp_path / "floor.json"
        wire.check(wire.request("describe", {}, deadline=1030.0, now=1000.0),
                   wire.Seen(elapsed=lambda: 0.0), now=1000.0, floor=wire.Floor(path))

        after_restart = wire.Floor(path)                       # a different object, as on a restart
        assert after_restart.highest == 1000.0
        with pytest.raises(wire.ProtocolError) as caught:
            wire.check(wire.request("describe", {}, deadline=530.0, now=500.0),
                       wire.Seen(elapsed=lambda: 0.0), now=500.0, floor=after_restart)
        assert caught.value.code == wire.ROLLED_BACK

    def test_the_floor_only_ever_moves_forward(self, tmp_path):
        floor = wire.Floor(tmp_path / "floor.json")
        floor.accepted(1000.0)
        floor.accepted(500.0)
        assert floor.highest == 1000.0

    def test_a_message_within_the_window_is_not_refused_by_it(self, tmp_path):
        """Clocks differ by a little between machines; that is what the window is for."""
        floor = wire.Floor(tmp_path / "floor.json")
        floor.accepted(1000.0)
        assert not floor.too_old(1000.0 - wire.FRESHNESS_SECONDS + 1, wire.FRESHNESS_SECONDS)
        assert floor.too_old(1000.0 - wire.FRESHNESS_SECONDS - 1, wire.FRESHNESS_SECONDS)

    def test_only_a_message_that_passed_everything_raises_it(self, tmp_path):
        """A malformed message carrying a far-future moment must not bar everything after it."""
        floor = wire.Floor(tmp_path / "floor.json")
        seen = wire.Seen(elapsed=lambda: 0.0)
        bad = wire.request("describe", {}, deadline=1030.0, now=1000.0)
        bad["method"] = "nothing-like-this"
        with pytest.raises(wire.ProtocolError):
            wire.check(bad, seen, now=1000.0, floor=floor)
        assert floor.highest == 0.0

    def test_a_floor_that_cannot_be_read_stops_the_worker(self, tmp_path):
        """This test used to assert the opposite, and the opposite was wrong.

        It read: "a corrupt hint is not a reason to refuse everything". But starting again from
        zero is precisely the state an attacker wants, because it is the state in which nothing
        is too old -- so corrupting one small file turned the protection off and left a worker
        that looked healthy. Review found it. Present-and-unreadable is not absent.
        """
        path = tmp_path / "floor.json"
        path.write_text("this is not json", encoding="utf-8")
        with pytest.raises(wire.CannotRememberTheFloor):
            wire.Floor(path)

    def test_a_message_from_too_long_ago(self):
        body = wire.request("describe", {}, deadline=4000.0, now=1000.0)
        with pytest.raises(wire.ProtocolError) as caught:
            wire.check(body, wire.Seen(), now=1000.0 + wire.FRESHNESS_SECONDS + 1)
        assert caught.value.code == wire.STALE

    def test_and_one_from_the_future_is_no_less_suspicious(self):
        body = wire.request("describe", {}, deadline=5000.0, now=4000.0)
        with pytest.raises(wire.ProtocolError) as caught:
            wire.check(body, wire.Seen(), now=1000.0)
        assert caught.value.code == wire.STALE

    def test_a_message_whose_moment_has_passed(self):
        body = wire.request("describe", {}, deadline=1000.5, now=1000.0)
        with pytest.raises(wire.ProtocolError) as caught:
            wire.check(body, wire.Seen(), now=1001.0)
        assert caught.value.code == wire.DEADLINE_PASSED


# -------------------------------------------------------------- only structured data crosses


class TestOnlyStructuredDataCrosses:

    def bench(self):
        return Bench(AWorkerThatAnswers(), "unix:///tmp/nowhere.sock", KEY, only_uid=1234)

    def test_a_method_nobody_declared(self):
        with pytest.raises(wire.ProtocolError) as caught:
            wire.request("rm -rf /", {}, deadline=time.time() + 5)
        assert caught.value.code == wire.UNKNOWN_METHOD

    def test_and_the_receiver_refuses_one_too(self):
        body = wire.request("describe", {}, deadline=time.time() + 30)
        body["method"] = "exec"
        with pytest.raises(wire.ProtocolError) as caught:
            wire.check(body, wire.Seen())
        assert caught.value.code == wire.UNKNOWN_METHOD

    def test_a_command_that_is_not_a_list_of_arguments(self):
        with pytest.raises(wire.ProtocolError) as caught:
            self.bench().answer("run", {"job": {"run_id": "r", "container_name": "c",
                                                "command": "python -c 'print(1)'",
                                                "limits": {}}})
        assert caught.value.code == wire.BAD_PARAMS
        assert "never joined into anything a shell would read" in caught.value.detail

    def test_a_job_without_limits(self):
        with pytest.raises(wire.ProtocolError) as caught:
            self.bench().answer("run", {"job": {"run_id": "r", "container_name": "c",
                                                "command": ["python"]}})
        assert caught.value.code == wire.BAD_PARAMS

    def test_a_job_that_is_not_a_job(self):
        for said in (None, [], "a job", 7):
            with pytest.raises(wire.ProtocolError) as caught:
                self.bench().answer("run", {"job": said})
            assert caught.value.code == wire.BAD_PARAMS

    def test_nothing_in_this_line_ever_builds_a_shell_command(self):
        """Not a convention: the source is read, and a shell is not in it."""
        import inspect

        from agentnode_sdk.worker import local, protocol, remote, service

        for module in (protocol, remote, service, local):
            source = inspect.getsource(module)
            for shape in ("shell=True", "os.system", '" ".join(command', "' '.join(command"):
                assert shape not in source, (module.__name__, shape)

    def test_a_job_crosses_and_comes_back_the_same_job(self):
        bench = self.bench()
        job = a_job(stdin="a-challenge\nAWorkerThatAnswers:1\ncGF5", artifact=b"\x00\x01binary")
        params = dict(job.as_message())
        params["artifact"] = wire.as_text(job.artifact)
        # Through JSON, because that is what really happens to it.
        travelled = json.loads(wire.canonical({"job": params}).decode("utf-8"))
        bench.answer("run", travelled)
        arrived = bench.worker.ran[0]
        assert arrived.command == job.command
        assert arrived.artifact == job.artifact
        assert arrived.stdin == job.stdin
        assert arrived.limits == job.limits


class TestTheClosedList:

    def bench(self):
        return Bench(AWorkerThatAnswers(), "unix:///tmp/nowhere.sock", KEY, only_uid=1234)

    def test_describe_says_what_the_record_has_to_bind(self):
        said = self.bench().answer("describe", {})
        assert said["configuration_sha256"] == "c" * 64
        assert said["runtime_version"] == "29.8.0"
        assert said["isolation"]["backend"] == "docker"

    def test_stop_and_gone_reach_the_worker(self):
        bench = self.bench()
        assert bench.answer("stop", {"run_id": "r", "container_name": "c",
                                     "appear_seconds": 1.0}) is True
        assert bench.worker.stopped == [("r", "c", 1.0)]
        assert bench.answer("gone", {"container_name": "c"})["answered"] is True

    def test_a_job_that_failed_is_refused_as_one(self):
        bench = self.bench()
        bench.worker.raises = JobFailed("it did not work", egress_gone=False)
        with pytest.raises(JobFailed):
            bench.answer("run", {"job": dict(a_job().as_message(),
                                             artifact=wire.as_text(b"x"))})

    def test_a_network_that_could_not_be_built_is_its_own_answer(self):
        bench = self.bench()
        bench.worker.raises = CouldNotRestrictTheNetwork("no runtime")
        with pytest.raises(CouldNotRestrictTheNetwork):
            bench.answer("run", {"job": dict(a_job().as_message(),
                                             artifact=wire.as_text(b"x"))})


# ------------------------------------------------------------------- every failure is closed


class AClientThatHears:
    """The real `SocketWorker`, with only the thing that opens a socket replaced."""

    @staticmethod
    def saying(answer_body, request_id_from=None):
        worker = SocketWorker("unix:///tmp/nowhere.sock", KEY)

        def instead(method, params, *, wait):
            body = wire.request(method, params, deadline=time.time() + wait)
            said = dict(answer_body)
            said.setdefault("request_id",
                            body["request_id"] if request_id_from is None else request_id_from)
            frame = wire.seal(said, KEY)
            return SocketWorker._ask.__wrapped__(worker, frame) if False else said

        return worker, instead


class TestEveryFailureIsClosed:

    def a_client(self):
        return SocketWorker("unix:///tmp/agentnode-nothing-is-here.sock", KEY)

    def test_a_worker_that_is_not_running(self):
        with pytest.raises(WorkerUnreachable) as caught:
            self.a_client().can_it_isolate()
        assert "could not be reached" in str(caught.value) or "no unix sockets" in str(caught.value)

    def test_an_answer_about_a_different_request_is_no_answer(self, monkeypatch):
        worker = SocketWorker("unix:///tmp/x.sock", KEY)
        monkeypatch.setattr(worker, "_ask", SocketWorker._ask.__get__(worker))
        heard = {"protocol": wire.PROTOCOL, "request_id": "somebody-elses", "ok": True,
                 "result": {}}
        with pytest.raises(WorkerUnreachable) as caught:
            _answer_with(worker, heard)
        assert "a different request" in str(caught.value)

    def test_a_refusal_that_is_not_about_the_job_is_nobody_saying_anything(self):
        worker = SocketWorker("unix:///tmp/x.sock", KEY)
        # RUNTIME_ABSENT used to be in this list, and that was the conflation review found: it
        # is the worker ANSWERING that its host has nothing to run code in, which is a fact it
        # established rather than an absence of information. It has its own answer now -- see
        # TestThreeFailuresAreThreeAnswers. What belongs here is only the codes where the worker
        # declines to have an opinion at all.
        for code in (wire.UNAUTHENTICATED, wire.STALE, wire.REPLAY, wire.TOO_LARGE,
                     wire.UNKNOWN_METHOD, wire.BAD_PARAMS, wire.DEADLINE_PASSED,
                     wire.INTERNAL, wire.MALFORMED):
            with pytest.raises(WorkerUnreachable):
                _answer_with(worker, {"ok": False, "error": code, "detail": "x"})

    def test_a_job_that_failed_is_a_job_that_failed(self):
        worker = SocketWorker("unix:///tmp/x.sock", KEY)
        with pytest.raises(JobFailed) as caught:
            _answer_with(worker, {"ok": False, "error": wire.JOB_FAILED, "detail": "it broke",
                                  "egress_gone": False})
        assert caught.value.egress_gone is False

    def test_a_restricted_network_that_could_not_be_built_is_its_own_kind(self):
        worker = SocketWorker("unix:///tmp/x.sock", KEY)
        with pytest.raises(CouldNotRestrictTheNetwork):
            _answer_with(worker, {"ok": False, "error": wire.NETWORK_UNAVAILABLE, "detail": "no"})


def _answer_with(worker: SocketWorker, said: dict):
    """Drive the client's own reading of an answer, without a socket under it."""
    body = wire.request("describe", {}, deadline=time.time() + 30)
    heard = dict(said)
    heard.setdefault("request_id", body["request_id"])
    heard.setdefault("protocol", wire.PROTOCOL)

    class OneExchange:
        def __init__(self):
            self.sent = b""

        def settimeout(self, _):
            pass

        def connect(self, _):
            pass

        def sendall(self, raw):
            self.sent += raw

        def makefile(self, _mode):
            import io as _io

            return _io.BytesIO(wire.seal(heard, KEY))

        def close(self):
            pass

    import agentnode_sdk.worker.remote as remote_mod

    made = OneExchange()
    original = remote_mod.socket.socket
    remote_mod.socket.socket = lambda *a, **k: made          # noqa: E731
    if not hasattr(remote_mod.socket, "AF_UNIX"):
        remote_mod.socket.AF_UNIX = 1
    try:
        # The request id the client really generated is inside its own call, so the answer is
        # rebuilt to match whatever it sends.
        def sending(raw):
            made.sent += raw
            (length,) = struct.unpack(">I", raw[:4])
            asked = json.loads(raw[36:36 + length].decode("utf-8"))
            if "request_id" not in said:
                heard["request_id"] = asked["request_id"]

        made.sendall = sending
        return worker.can_it_isolate()
    finally:
        remote_mod.socket.socket = original


class TestWhereTheWorkerIs:

    def test_a_unix_socket_cannot_leave_the_machine(self):
        assert topology_of("unix:///run/agentnode/worker.sock") == SINGLE_HOST_DEVELOPMENT

    def test_nor_can_loopback(self):
        assert topology_of("tcp://127.0.0.1:9000") == SINGLE_HOST_DEVELOPMENT

    def test_and_anywhere_else_is_somewhere_else(self):
        assert topology_of("tcp://10.0.0.5:9000") == SEPARATE_WORKER_HOST

    def test_the_client_does_not_ask_the_worker_where_it_is(self):
        """A worker's answer about its own location is a self-report, and a record carrying one
        would be carrying something nobody can check."""
        import inspect

        from agentnode_sdk.worker import remote

        source = inspect.getsource(remote.SocketWorker.topology.fget)
        assert "topology_of(self.address)" in source
        assert "_describe" not in source


class AConnectionFrom:
    """A connection whose peer credential says whatever a test wants it to say."""

    def __init__(self, uid, body=None, key=KEY):
        self.uid = uid
        self.sent = b""
        self.closed = False
        self._body = body if body is not None else wire.request(
            "describe", {}, deadline=time.time() + 30)
        self._key = key

    def getsockopt(self, _level, _name, _size):
        if self.uid is None:
            raise OSError("this machine does not answer that")
        return struct.pack("3i", 999, self.uid, self.uid)

    def settimeout(self, _):
        pass

    def makefile(self, _mode):
        import io as _io

        return _io.BytesIO(wire.seal(self._body, self._key))

    def sendall(self, raw):
        self.sent += raw

    def close(self):
        self.closed = True


class TestWhoMayConnect:
    """The kernel says which account is at the other end. It is not something the caller says."""

    def a_bench(self, only_uid=1000):
        return Bench(AWorkerThatAnswers(), "unix:///tmp/nowhere.sock", KEY, only_uid=only_uid)

    def test_the_account_it_was_started_for_is_answered(self):
        connection = AConnectionFrom(1000)
        self.a_bench()._one(connection)
        assert connection.sent, "the account this worker serves got no answer"

    def test_a_connection_from_another_account_is_answered_with_nothing(self):
        """Closed without a word. Telling an account it is the wrong account is telling it there
        is a right one."""
        connection = AConnectionFrom(1001)
        self.a_bench()._one(connection)
        assert connection.sent == b"", "it answered an account it was not started for"
        assert connection.closed

    def test_the_credential_comes_from_the_kernel_and_not_from_the_message(self):
        import inspect

        source = inspect.getsource(Bench.who_is_connecting)
        assert "SO_PEERCRED" in source
        # Nothing in the body is consulted: the check happens before a byte of it is read.
        order = inspect.getsource(Bench._one)
        assert order.index("who_is_connecting") < order.index("read_frame")

    def test_a_message_from_the_right_account_with_the_wrong_key_is_not_answered(self):
        connection = AConnectionFrom(1000, key=OTHER)
        self.a_bench()._one(connection)
        assert connection.sent == b"", "it answered a message it could not authenticate"


class TestTheSocketIsNarrowedBeforeAnythingCanReachIt:
    """There is no instant in which the socket exists and anyone may connect to it."""

    def test_the_mode_goes_on_before_it_listens(self, monkeypatch):
        import agentnode_sdk.worker.service as service_mod

        happened = []

        class AListener:
            def bind(self, path):
                happened.append(("bind", path))

            def listen(self, _backlog):
                happened.append(("listen", None))

            def close(self):
                pass

        monkeypatch.setattr(service_mod.socket, "socket", lambda *a, **k: AListener())
        monkeypatch.setattr(service_mod.os, "chmod",
                            lambda path, mode: happened.append(("chmod", oct(mode))))
        monkeypatch.setattr(service_mod.os.path, "exists", lambda _p: False)
        monkeypatch.setattr(service_mod.os, "makedirs", lambda *a, **k: None)
        bench = Bench(AWorkerThatAnswers(), "unix:///somewhere/sock/worker.sock", KEY,
                      only_uid=1000)
        bench.open()

        what = [step for step, _ in happened]
        assert what.index("chmod") < what.index("listen"), happened
        assert ("chmod", oct(SOCKET_MODE)) in happened
        assert ("chmod", oct(DIRECTORY_MODE)) in happened

    def test_owner_and_group_and_nobody_else(self):
        assert SOCKET_MODE == 0o660
        assert DIRECTORY_MODE & 0o777 == 0o750
        assert not (SOCKET_MODE & 0o007), "anyone on the machine could reach this socket"
        assert not (DIRECTORY_MODE & 0o007), "anyone on the machine could reach into this directory"

    def test_the_directory_is_setgid_so_the_socket_inherits_its_group(self):
        """Without this bit the gateway cannot reach the worker at all, and it is easy to lose.

        A unix socket takes the primary group of whoever binds it. The worker's primary group
        must stay its own -- rootless podman maps subordinate ids through newuidmap, which
        refuses when the process gid is not the one in the account's passwd entry -- so the
        group the two accounts share cannot come from the binding process. Setgid on the
        directory is what puts it on the socket.
        """
        import stat

        assert DIRECTORY_MODE & stat.S_ISGID, (
            "the socket would be created with the worker's own group, and the gateway is not "
            "in it")


# ------------------------------------------------------------------------ the socket itself


@needs_unix
class TestTheSocketIsReachableByOneAccount:

    def a_bench(self, tmp_path, only_uid=None):
        address = "unix://" + str(tmp_path / "sock" / "worker.sock")
        bench = Bench(AWorkerThatAnswers(), address, KEY,
                      only_uid=os.getuid() if only_uid is None else only_uid)
        bench.open()
        thread = threading.Thread(target=bench.serve_forever, daemon=True)
        thread.start()
        return bench, address

    def test_a_job_really_crosses_a_socket(self, tmp_path):
        bench, address = self.a_bench(tmp_path)
        try:
            client = SocketWorker(address, KEY)
            outcome = client.run(a_job())
            assert outcome.stdout == "RAN"
            assert client.can_it_isolate().backend == "docker"
            assert client.topology == SINGLE_HOST_DEVELOPMENT
        finally:
            bench.stop_serving()

    def test_the_socket_is_owner_and_group_only(self, tmp_path):
        bench, address = self.a_bench(tmp_path)
        try:
            path = address[len("unix://"):]
            assert (os.stat(path).st_mode & 0o777) == SOCKET_MODE
            # 0o7777, not 0o777: the mask has to keep the setgid bit, which is the whole
            # reason this directory has the mode it has. Masking it off compares against a
            # number that can never match -- and would equally have passed a directory that
            # was NOT setgid, which is the case that leaves the gateway unable to reach its
            # worker at all.
            folder = os.stat(os.path.dirname(path)).st_mode
            assert (folder & 0o7777) == DIRECTORY_MODE
            assert folder & stat.S_ISGID, "the socket would not inherit the shared group"
        finally:
            bench.stop_serving()

    def test_another_account_gets_nothing(self, tmp_path):
        """The kernel says who is connecting; it is not something the caller asserts."""
        bench, address = self.a_bench(tmp_path, only_uid=os.getuid() + 1)
        try:
            with pytest.raises(WorkerUnreachable):
                SocketWorker(address, KEY).can_it_isolate()
        finally:
            bench.stop_serving()

    def test_a_client_with_the_wrong_key_is_told_nothing(self, tmp_path):
        bench, address = self.a_bench(tmp_path)
        try:
            with pytest.raises(WorkerUnreachable):
                SocketWorker(address, OTHER).can_it_isolate()
        finally:
            bench.stop_serving()

    def test_a_socket_left_by_a_process_that_is_gone(self, tmp_path):
        folder = tmp_path / "sock"
        folder.mkdir()
        (folder / "worker.sock").write_bytes(b"")
        bench = Bench(AWorkerThatAnswers(), "unix://" + str(folder / "worker.sock"), KEY,
                      only_uid=os.getuid())
        bench.open()
        bench.stop_serving()

    def test_a_worker_that_is_already_there_is_not_replaced(self, tmp_path):
        bench, address = self.a_bench(tmp_path)
        try:
            second = Bench(AWorkerThatAnswers(), address, KEY, only_uid=os.getuid())
            with pytest.raises(OSError):
                second.open()
        finally:
            bench.stop_serving()


class TestAWorkerIsStartedForOneAccount:

    def test_starting_one_for_nobody_is_refused(self):
        with pytest.raises(ValueError) as caught:
            from agentnode_sdk.worker.service import serve

            serve("unix:///tmp/x.sock", "/nonexistent", None)
        assert "one account" in str(caught.value)


class TestTheKey:

    def test_a_key_that_is_not_there(self, tmp_path):
        with pytest.raises(wire.ProtocolError) as caught:
            wire.read_key(tmp_path / "absent")
        assert caught.value.code == wire.UNAUTHENTICATED

    def test_a_key_too_short_to_be_one(self, tmp_path):
        thin = tmp_path / "thin"
        thin.write_bytes(b"short")
        with pytest.raises(wire.ProtocolError) as caught:
            wire.read_key(thin)
        assert "is not a key" in str(caught.value)

    def test_a_key_this_build_made(self, tmp_path):
        made = tmp_path / "key"
        made.write_bytes(wire.new_key())
        assert len(wire.read_key(made)) >= 32


class TestAWorkerShowsItsCeilingsBindBeforeItServes:
    """The gap this closes is the one that looks exactly like success.

    A runtime can be installed, reachable, and reporting that this host can hold a memory
    ceiling -- and still not apply the ceiling to anything it runs. A rootless runtime does
    precisely that when the account has no systemd user session: it accepts the flag, falls back
    to cgroupfs, and the allocation walks straight past the limit. Every check that ASKS comes
    back clean. Only hitting the ceiling tells you.
    """

    def test_a_worker_that_cannot_stop_an_allocation_does_not_listen(self, tmp_path):
        worker = AWorkerThatAnswers()
        worker.ceilings = False
        key = tmp_path / "k"
        key.write_bytes(wire.new_key())
        with pytest.raises(service.CannotHoldItsLimits):
            service.serve("unix://" + str(tmp_path / "s.sock"), str(key), os.getuid()
                          if hasattr(os, "getuid") else 0, worker=worker)

    def test_and_nothing_is_listening_afterwards(self, tmp_path):
        """A refusal that left a socket open would be a refusal in name only."""
        worker = AWorkerThatAnswers()
        worker.ceilings = False
        key = tmp_path / "k"
        key.write_bytes(wire.new_key())
        where = tmp_path / "s.sock"
        with pytest.raises(service.CannotHoldItsLimits):
            service.serve("unix://" + str(where), str(key),
                          os.getuid() if hasattr(os, "getuid") else 0, worker=worker)
        assert not where.exists(), "it refused, and then opened the socket anyway"

    def test_a_worker_that_cannot_say_is_treated_as_one_that_cannot(self):
        """None is not a pass. A worker that cannot answer is not one to hand foreign code to."""
        worker = AWorkerThatAnswers()
        worker.ceilings = None
        with pytest.raises(service.CannotHoldItsLimits) as refused:
            service.serve("unix:///tmp/never", "/nonexistent", 0, worker=worker)
        assert "could not say" in str(refused.value)

    def test_there_is_no_flag_that_skips_it(self):
        """An operator in a hurry must not be able to turn this off from the command line.

        The check is worth having only if it cannot be waived at the moment it fails, which is
        the moment somebody most wants to waive it.
        """
        import inspect

        from agentnode_sdk.cli import worker_commands

        serving = inspect.getsource(worker_commands.cmd_serve)
        for wording in ("--no-prove", "--skip-ceiling", "--unsafe", "--insecure", "--no-check"):
            assert wording not in serving, f"a way around the ceiling check appeared: {wording}"
        # And the parser for `serve` takes no argument that could carry one.
        adding = inspect.getsource(worker_commands.add_parser)
        after = adding.split('actions.add_parser("serve"')[-1]
        assert "--no-" not in after and "--skip" not in after

        # The gate is not something serve() can be talked out of: it runs before the socket is
        # made, and the only path past it is the proof holding.
        serve_text = inspect.getsource(service.serve)
        assert serve_text.index("prove_its_ceilings") < serve_text.index("bench.open()")

    def test_the_refusal_says_what_to_do_about_it(self, capsys):
        """A refusal an operator cannot act on gets worked around rather than fixed."""
        from agentnode_sdk.cli import worker_commands

        class Args:
            socket = "unix:///tmp/x.sock"
            key = "/tmp/x.key"
            for_user = "0"

        def refuse(*_a, **_k):
            raise service.CannotHoldItsLimits("an allocation of 768 MB ran to completion")

        worker_commands.serve = refuse                       # the import is inside the function
        import agentnode_sdk.worker.service as real

        original, real.serve = real.serve, refuse
        try:
            assert worker_commands.cmd_serve(Args()) == 1
        finally:
            real.serve = original
        said = capsys.readouterr().out
        assert "will not serve" in said
        assert "enable-linger" in said, "it did not name the thing that fixes this"
        assert "768 MB" in said, "it did not say what was actually measured"


class TestTheProofIsTheSuitesOwn:
    """One definition of "enforced", not two.

    If the worker's gate and the conformance report each had their own idea of what counts as a
    ceiling binding, the weaker one would be the one standing between a client's job and the
    host -- and nobody would notice, because the stronger one would still be in the report.
    """

    def test_the_worker_uses_the_suites_measurement(self):
        import inspect

        from agentnode_sdk.worker.local import LocalWorker

        text = inspect.getsource(LocalWorker.prove_its_ceilings)
        assert "memory_ceiling_proof" in text

    def test_and_that_measurement_needs_all_three_conditions(self):
        """Began, did not complete, and ended in a way the ceiling accounts for."""
        from agentnode_sdk.conformance.runner import memory_ceiling_proof

        class AResult(tuple):
            """What a backend hands back: a triple that also answers why it stopped."""

            def __new__(cls, rc, out, err):
                self = super().__new__(cls, (rc, out, err))
                self.reason, self.native_status, self.platform = "exited", rc, "linux-container"
                return self

        class Backend:
            native_platform = "linux-container"

            def __init__(self, stdout, stderr="", rc=137):
                self.out, self.err, self.rc = stdout, stderr, rc

            def run_process(self, spec, timeout=None):
                return AResult(self.rc, self.out, self.err)

        # It never began: an image that will not start is not evidence about a ceiling.
        never = memory_ceiling_proof(Backend(""), megabytes=768, run_id="t")
        assert never["killed"] is False

        # It ran the whole way: the ceiling was accepted and then not applied.
        through = memory_ceiling_proof(
            Backend("ALLOCATING\nALLOCATED 768\n", rc=0), megabytes=768, run_id="t")
        assert through["killed"] is False
        assert through["completed"] is True

        # It began, stopped short, and the kernel is why.
        held = memory_ceiling_proof(Backend("ALLOCATING\n"), megabytes=768, run_id="t")
        assert held["killed"] is True


class TestFindingARuntimeTheAccountCanActuallyUse:
    """Installed first is not the same as usable, and a worker account makes them differ.

    The account that runs foreign code must NOT be in the docker group -- that group is
    root-equivalent, so an escape from the sandbox would get the host. On a machine with both
    runtimes installed that is exactly the shape where docker is found and unusable while podman
    works perfectly, and stopping at the first one found meant the worker refused to serve on a
    host that could have held every one of its ceilings.
    """

    def _backend(self, monkeypatch, present, reachable):
        from agentnode_sdk.sandbox import container_backend as cb

        monkeypatch.setattr(cb.shutil, "which",
                            lambda name: f"/usr/bin/{name}" if name in present else None)
        backend = cb.ContainerBackend()
        monkeypatch.setattr(backend, "_runtime_ok",
                            lambda path: (any(r in path for r in reachable), "unreachable"))
        monkeypatch.setattr(backend, "_engine_facts", lambda path: ("linux", True))
        monkeypatch.setattr(backend, "_image_present", lambda path: True)
        return backend

    def test_it_goes_on_to_the_next_one(self, monkeypatch):
        backend = self._backend(monkeypatch, present={"docker", "podman"}, reachable={"podman"})
        found = backend.check_available(force=True)
        assert found.available is True
        assert found.backend == "podman"

    def test_and_still_refuses_when_none_of_them_work(self, monkeypatch):
        """Looking further must not become finding something that is not there."""
        backend = self._backend(monkeypatch, present={"docker", "podman"}, reachable=set())
        found = backend.check_available(force=True)
        assert found.available is False

    def test_and_says_what_the_first_thing_to_fix_is(self, monkeypatch):
        """"no runtime" would be wrong and unhelpful: one is installed, and it is not answering."""
        backend = self._backend(monkeypatch, present={"docker", "podman"}, reachable=set())
        found = backend.check_available(force=True)
        assert found.backend == "docker"
        assert "not reachable" in found.reason

    def test_a_pinned_runtime_is_still_the_only_one_tried(self, monkeypatch):
        """A deployment that names its runtime means it, and must not get a silent substitute."""
        from agentnode_sdk.sandbox import container_backend as cb

        monkeypatch.setattr(cb.shutil, "which", lambda name: f"/usr/bin/{name}")
        backend = cb.ContainerBackend(runtime="docker")
        monkeypatch.setattr(backend, "_runtime_ok", lambda path: ("podman" in path, "no"))
        found = backend.check_available(force=True)
        assert found.available is False
        assert found.backend == "docker"


class TestAskingARuntimeWhetherItHoldsACeiling:
    """Asked in the wrong words, and answered with an error, is not answered "no".

    The backend asks in Docker's vocabulary first. Podman does not have those fields and does not
    politely return nothing for them -- it exits non-zero. Reading that as "this runtime cannot
    enforce a memory ceiling" made every podman host unusable, and on a worker that refuses to
    serve without an enforceable ceiling, unusable means it never starts at all.
    """

    def _facts(self, monkeypatch, answers):
        from agentnode_sdk.sandbox import container_backend as cb

        class Reply:
            def __init__(self, rc, out):
                self.returncode, self.stdout, self.stderr = rc, out, ""

        def fake(argv):
            for pattern, reply in answers.items():
                if pattern in " ".join(argv):
                    return Reply(*reply)
            return Reply(1, "")

        monkeypatch.setattr(cb, "_run_runtime", fake)
        return cb.ContainerBackend()._engine_facts("/usr/bin/podman")

    def test_it_asks_again_in_the_runtimes_own_words(self, monkeypatch):
        engine_os, enforceable = self._facts(monkeypatch, {
            "MemoryLimit": (125, ""),                      # podman: unknown field, non-zero exit
            "CgroupsVersion": (0, "v2\n"),
        })
        assert enforceable is True
        assert engine_os == "linux"

    def test_and_a_runtime_on_cgroup_v1_still_cannot(self, monkeypatch):
        """Looking further must not turn into finding what one hoped for."""
        _, enforceable = self._facts(monkeypatch, {
            "MemoryLimit": (125, ""),
            "CgroupsVersion": (0, "v1\n"),
        })
        assert enforceable is False

    def test_and_when_neither_question_lands_the_answer_is_not_yes(self, monkeypatch):
        _, enforceable = self._facts(monkeypatch, {"MemoryLimit": (125, "")})
        assert enforceable is None, "a runtime that answered nothing must not read as enforcing"

    def test_dockers_own_answer_is_still_taken_when_it_gives_one(self, monkeypatch):
        _, enforceable = self._facts(monkeypatch, {"MemoryLimit": (0, "linux|true|true")})
        assert enforceable is True
        _, no_swap = self._facts(monkeypatch, {"MemoryLimit": (0, "linux|true|false")})
        assert no_swap is False, "a ceiling without swap accounting is not a ceiling"


@needs_unix
class TestAWorkerThatTakesTheCallAndSaysNothing:
    """The failures that look most like success from the gateway's side.

    A worker that is DOWN is obvious: the connection is refused. A worker that accepts the
    connection and then never answers, or answers after the moment has passed, leaves the gateway
    holding an open socket and nothing to read. What must never happen is that silence becomes a
    job that ran -- in either direction. Not a job that succeeded, and not a job that ran and
    failed, because a client told "your code failed" will change the code.
    """

    def _a_socket_that(self, tmp_path, behaviour):
        """A listener that accepts and then does whatever `behaviour` does with the connection."""
        path = str(tmp_path / "w.sock")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(path)
        listener.listen(4)
        keep = []

        def answer():
            while True:
                try:
                    conn, _ = listener.accept()
                except OSError:
                    return
                keep.append(conn)
                try:
                    behaviour(conn)
                except OSError:
                    pass

        thread = threading.Thread(target=answer, daemon=True)
        thread.start()
        return "unix://" + path, listener, keep

    def _worker(self, address):
        return SocketWorker(address, KEY, connect_timeout=1.5, run_margin=2.0)

    def test_a_worker_that_accepts_and_never_answers_is_not_a_job_that_ran(self, tmp_path):
        address, listener, _keep = self._a_socket_that(tmp_path, lambda conn: None)
        try:
            worker = self._worker(address)
            with pytest.raises(WorkerUnreachable) as refused:
                worker.run(a_job(limits=Limits(wall_clock_s=1)))
            # The type IS the classification. A gateway that caught JobFailed here would tell a
            # client its code failed, and a client told that changes the code -- for a worker
            # that never said anything about the code at all.
            assert not isinstance(refused.value, JobFailed), (
                "silence from the worker is being presented as a job that ran and failed")
        finally:
            listener.close()

    def test_and_what_it_waits_comes_from_the_job_rather_than_from_nowhere(self, tmp_path):
        """A silent worker must not hold the gateway open indefinitely.

        The bound cannot be a small constant: a worker legitimately running a job says nothing
        until the job is done, so the gateway has to be willing to wait at least the job's own
        wall clock. What is established here is that the wait is DERIVED from that -- a job
        allowed less time is given up on sooner -- rather than being unbounded.
        """
        address, listener, _keep = self._a_socket_that(tmp_path, lambda conn: None)
        try:
            worker = self._worker(address)
            started = time.time()
            with pytest.raises(WorkerUnreachable):
                worker.run(a_job(limits=Limits(wall_clock_s=1)))
            waited = time.time() - started
            # The number is diagnosis; the raise above is the evidence that it stopped at all.
            # This only shows it stopped near the job's own bound and not at some other one.
            ceiling = 1 + worker.run_margin
            assert waited < ceiling + 10, (
                f"it waited {waited:.0f}s for a job allowed 1s, so the wait is not the job's")
        finally:
            listener.close()

    def test_an_answer_that_arrives_after_the_moment_has_passed_is_refused(self):
        """Late is not merely slow: the message was authenticated for a moment that is gone."""
        body = {"protocol": wire.PROTOCOL, "request_id": "r", "nonce": "n1", "method": "run",
                "issued_at": time.time(), "deadline": time.time() - 0.5, "params": {}}
        with pytest.raises(wire.ProtocolError) as refused:
            wire.check(body, wire.Seen())
        assert refused.value.code == wire.DEADLINE_PASSED

    def test_and_neither_silence_nor_lateness_can_be_read_as_success(self, tmp_path):
        """The whole point: no path through here produces an Outcome nobody computed."""
        address, listener, _keep = self._a_socket_that(tmp_path, lambda conn: conn.close())
        try:
            worker = self._worker(address)
            with pytest.raises(WorkerUnreachable):
                worker.run(a_job(limits=Limits(wall_clock_s=1)))
        finally:
            listener.close()


class TestTheWireDescribesEveryFieldItAccepts:
    """A field the receiver does not describe is refused, not ignored.

    Ignoring one is how two builds come to disagree about what a message meant while both think
    they understood it: the sender puts in something that matters to it, the receiver drops it,
    and the job runs under terms neither agreed to. It also keeps the message that was
    authenticated and the message that was acted on the same message.
    """

    def _message(self, **extra):
        body = {"protocol": wire.PROTOCOL, "request_id": "r", "nonce": "n-%d" % len(extra),
                "method": "run", "issued_at": time.time(), "deadline": time.time() + 30,
                "params": {}}
        body.update(extra)
        return body

    def test_a_field_this_build_does_not_describe(self):
        with pytest.raises(wire.ProtocolError) as refused:
            wire.check(self._message(priority="high"), wire.Seen())
        assert refused.value.code == wire.MALFORMED
        assert "priority" in str(refused.value)

    def test_and_it_says_which_one(self):
        with pytest.raises(wire.ProtocolError) as refused:
            wire.check(self._message(shell="/bin/sh"), wire.Seen())
        assert "shell" in str(refused.value)

    def test_the_fields_it_does_describe_are_accepted(self):
        wire.check(self._message(), wire.Seen())

    def test_and_the_list_is_declared_in_one_place(self):
        """So that adding a field is a decision in a place a reviewer reads."""
        assert set(wire.FIELDS) == {"protocol", "request_id", "nonce", "method", "issued_at",
                                    "deadline", "params"}


class TestAWorkerThatCannotRememberWhatItAcceptedDoesNotServe:
    """The floor used to fail OPEN, with a comment arguing that it should.

    An unreadable floor file started again from zero, and a floor that could not be written was
    ignored. Both were wrong in the one direction this class exists to prevent: zero is the state
    in which nothing is too old, so corrupting one small file -- or filling a disk -- turned the
    replay defence off and left a worker that looked perfectly healthy. Found in review.
    """

    def test_a_floor_that_is_there_and_unreadable_is_refused(self, tmp_path):
        path = tmp_path / "floor.json"
        path.write_text("this is not json", encoding="utf-8")
        with pytest.raises(wire.CannotRememberTheFloor) as refused:
            wire.Floor(path)
        assert "already accepted" in str(refused.value)

    def test_but_one_that_is_simply_absent_is_a_first_run(self, tmp_path):
        """The distinction that matters: absent is ordinary, corrupt is not."""
        assert wire.Floor(tmp_path / "not-there.json").highest == 0.0

    def test_a_floor_that_cannot_be_written_is_refused(self, tmp_path):
        """Not best-effort: a floor that advanced only in memory is reset by a restart."""
        floor = wire.Floor(tmp_path / "sub" / "floor.json")
        floor.path = tmp_path / "sub" / "nope" / "\0" / "floor.json"
        with pytest.raises(wire.CannotRememberTheFloor):
            floor.accepted(2000.0)

    def test_and_the_message_is_not_accepted_when_it_cannot_be_recorded(self, tmp_path):
        """Accepting while unable to record it is how the gap gets built, silently."""
        floor = wire.Floor(tmp_path / "floor.json")
        floor.path = tmp_path / "\0" / "floor.json"
        with pytest.raises(wire.CannotRememberTheFloor):
            wire.check(wire.request("describe", {}, deadline=1030.0, now=1000.0),
                       wire.Seen(elapsed=lambda: 0.0), now=1000.0, floor=floor)

    def test_a_worker_whose_floor_cannot_be_written_does_not_listen(self, tmp_path):
        """The durability contract: no socket unless the record can actually be kept."""
        worker = AWorkerThatAnswers()
        key = tmp_path / "k"
        key.write_bytes(wire.new_key())
        bench = service.Bench(worker, "unix://" + str(tmp_path / "s.sock"), wire.read_key(key),
                              only_uid=0, remembers_at=str(tmp_path / "\0" / "floor.json"))
        with pytest.raises(wire.CannotRememberTheFloor):
            bench.floor.can_be_written()

    def test_and_one_whose_floor_works_is_left_where_it_was(self, tmp_path):
        """The probe must not move the floor it is testing."""
        floor = wire.Floor(tmp_path / "floor.json")
        floor.accepted(5000.0)
        floor.can_be_written()
        assert floor.highest == 5000.0
        assert wire.Floor(tmp_path / "floor.json").highest == 5000.0



def _a_request_body() -> dict:
    """The params a SocketWorker really sends for a run, built the way it builds them."""
    job = a_job()
    params = {"job": job.as_message()}
    params["artifact"] = wire.as_text(job.artifact)
    params["job"].pop("artifact", None)
    return params


class TestTheControlPlaneDoesNotTouchARuntime:
    """The seam the whole arrangement rests on: the gateway asks, it never drives.

    If any path in the gateway reached a container runtime directly -- by running it, naming its
    executable, listing containers or removing one -- then moving the worker to another machine
    would mean changing that path, and "the worker's location is deployment configuration" would
    be untrue in a way nobody would notice until the move.
    """

    def _gateway_source(self) -> str:
        import inspect

        from agentnode_sdk.gateway import server

        return inspect.getsource(server)

    def test_the_gateway_names_no_container_runtime(self):
        text = self._gateway_source()
        for runtime in ("docker", "podman", "crun", "runc"):
            for shape in (f'"{runtime}"', f"'{runtime}'", f"{runtime} ", f"/{runtime}"):
                assert shape not in text, (
                    f"the gateway names a container runtime ({shape!r}); asking the worker is "
                    f"the only way it is supposed to reach one")

    def test_nor_does_it_import_the_backend_except_behind_the_worker(self):
        """A ContainerBackend in the gateway is a runtime in the gateway."""
        text = self._gateway_source()
        for line in text.splitlines():
            if "ContainerBackend" in line and not line.strip().startswith("#"):
                assert "LocalWorker" in text, line
                assert "def backend" in text or "self._backend" in text, line

    def test_everything_the_gateway_asks_for_is_on_the_worker(self):
        """The interface is the whole of what may be asked, so it can be listed."""
        from agentnode_sdk.worker import Worker

        asked = {name for name in dir(Worker) if not name.startswith("_")}
        assert {"run", "stop", "gone", "measure", "can_it_isolate", "instance_label",
                "image_digest", "configuration_sha256", "runtime_version",
                "prove_its_ceilings"} <= asked

    def test_and_none_of_it_hands_back_something_that_only_means_something_here(self):
        """Everything that crosses has to survive being written down and read on another machine.

        Checked on what is actually SENT, not on the objects behind it: a job's artefact is bytes,
        which are data but not JSON, and the transport encodes them. Asserting against
        `Job.as_message()` would have been asserting about a representation that never crosses.
        """
        from agentnode_sdk.worker import Gone, Isolation, Outcome

        sent = json.dumps(_a_request_body())
        assert "artifact" in sent
        for answer in (Outcome(exit_code=0, stdout="", stderr="", reason="exited",
                               native_status=0, native_platform="linux-container").as_message(),
                       Isolation(available=True).as_message(),
                       Gone(answered=True).as_message()):
            json.dumps(answer)                                 # raises on anything that is not data

    def test_no_live_thing_is_put_into_a_message(self):
        """A callable, a handle or a process id would mean something only on one machine."""
        from agentnode_sdk.worker import NotData, _plain

        for live in (lambda: None, object(), open):
            with pytest.raises(NotData):
                _plain({"x": live}, "job")

    def test_a_worker_somewhere_else_is_the_same_interface(self):
        """Same methods, so nothing in the product can tell which one it holds."""
        from agentnode_sdk.worker.local import LocalWorker
        from agentnode_sdk.worker.remote import SocketWorker

        for name in ("run", "stop", "gone", "measure", "can_it_isolate", "prove_its_ceilings"):
            assert callable(getattr(LocalWorker, name, None)), name
            assert callable(getattr(SocketWorker, name, None)), name


class TestThreeFailuresAreThreeAnswers:
    """Nobody answered, the worker has no runtime, the job ran and failed.

    They send a person to three different places: the network, the worker's host, and their own
    code. Review found the middle one collapsed into the first -- a worker that ANSWERED, saying
    its host has no usable runtime, was reported as a worker that could not be reached, which
    sends somebody to look at a connection that is working perfectly.
    """

    def _refusing(self, code, detail="nothing here can isolate anything"):
        worker = SocketWorker("unix:///nowhere", KEY)
        worker._ask = lambda *a, **k: (_ for _ in ()).throw(AssertionError("unused"))
        return worker, {"ok": False, "error": code, "detail": detail}

    def test_a_worker_that_has_no_runtime_says_so(self):
        from agentnode_sdk.worker import NoRuntimeThere

        worker, answer = self._refusing(wire.RUNTIME_ABSENT)
        with pytest.raises(NoRuntimeThere) as raised:
            worker._interpret(answer)
        assert "no container runtime" in str(raised.value)

    def test_and_that_is_not_the_same_as_nobody_answering(self):
        from agentnode_sdk.worker import JobFailed, NoRuntimeThere

        assert not issubclass(NoRuntimeThere, WorkerUnreachable)
        assert not issubclass(NoRuntimeThere, JobFailed)

    def test_a_job_that_ran_and_failed_is_its_own_answer(self):
        worker, answer = self._refusing(wire.JOB_FAILED, "it exited 1")
        with pytest.raises(JobFailed):
            worker._interpret(answer)

    def test_and_anything_the_worker_will_not_have_an_opinion_on_is_unreachable(self):
        """The catch-all stays a catch-all: what it must not swallow is a definite answer."""
        for code in (wire.UNAUTHENTICATED, wire.STALE, wire.REPLAY, wire.INTERNAL):
            worker, answer = self._refusing(code)
            with pytest.raises(WorkerUnreachable):
                worker._interpret(answer)


class TestNothingClaimsTheWorkerCanAlreadyBeMoved:
    """The seam is what has been built. The move is not, and nothing may say it is.

    A review found the two claims side by side and incompatible: the module said the arrangement
    is "what makes moving it a matter of configuration", while the transport refuses every scheme
    but unix in as many words. Both cannot be true. A reader who believed the first would plan a
    second machine around a change that does not exist.

    What is actually established is narrower and worth keeping: the vocabulary, the data and the
    failure modes do not depend on co-location, so a transport is the ONLY thing missing. These
    tests hold the difference between those two statements, because it is exactly the kind of
    sentence that drifts back when somebody tidies a docstring.
    """

    def _sources(self):
        import inspect

        from agentnode_sdk import worker
        from agentnode_sdk.gateway import server

        return {"worker/__init__.py": inspect.getdoc(worker) or "",
                "gateway/server.py": inspect.getsource(server.GatewayService.worker.fget)}

    def test_nothing_says_moving_it_is_configuration(self):
        for where, text in self._sources().items():
            flowed = " ".join(text.split()).lower()
            for claim in ("moving the worker is configuration",
                          "matter of configuration",
                          "a deployment change rather than a rewrite"):
                assert claim not in flowed, (where, claim)

    def test_and_what_is_missing_is_named(self):
        """Not merely the absence of the wrong claim: the right one has to be present, or
        removing a sentence would satisfy this and leave a reader knowing nothing."""
        from agentnode_sdk import worker
        import inspect

        said = " ".join((inspect.getdoc(worker) or "").split()).lower()
        assert "transport" in said
        assert "unix socket" in said or "unix sockets" in said
        for phrase in ("does not have", "does not exist", "needs a transport"):
            if phrase in said:
                break
        else:
            raise AssertionError("it does not say the transport is missing: " + said[:400])

    def test_and_the_transport_really_does_refuse_everything_else(self):
        """The claim is checked against the code rather than against another sentence."""
        from agentnode_sdk.worker import WorkerUnreachable
        from agentnode_sdk.worker.remote import from_address

        for address in ("tcp://10.0.0.5:9000", "https://elsewhere.example",
                        "ssh://box/run/agentnode/worker.sock"):
            with pytest.raises(WorkerUnreachable):
                from_address(address, b"k" * 32)

    def test_and_the_deployment_does_not_claim_it_either(self):
        from pathlib import Path

        said = (Path(__file__).resolve().parent.parent / "deploy" / "README.md").read_text(
            encoding="utf-8")
        flowed = " ".join(said.split())
        assert "moving the worker is configuration" not in flowed
        assert "does not follow that the worker can be moved" in flowed


class TestARecordSaysWhatItsArrangementDoesNotEstablish:
    """A label means nothing to a reader who does not already know what it means.

    "single-host-development" in a record months later, or handed to somebody as evidence, tells
    them nothing about what it does not protect against -- and that implication is the whole
    reason the label is recorded. So the limits travel with it.
    """

    def test_the_limits_are_a_value_and_not_only_prose(self):
        from agentnode_sdk.worker import (
            SEPARATE_WORKER_HOST,
            SINGLE_HOST_DEVELOPMENT,
            what_it_does_not_establish,
        )

        said = what_it_does_not_establish(SINGLE_HOST_DEVELOPMENT)
        assert "not isolation" in said
        assert "signing identity" in said
        assert "not production-ready" in said and "not multi-tenant" in said
        assert what_it_does_not_establish(SEPARATE_WORKER_HOST) != said

    def test_an_arrangement_this_build_does_not_describe_claims_nothing(self):
        from agentnode_sdk.worker import what_it_does_not_establish

        said = what_it_does_not_establish("something-nobody-has-defined")
        assert "does not describe" in said

    def test_and_every_record_of_use_carries_them(self, tmp_path):
        from agentnode_sdk.gateway import meter
        from agentnode_sdk.worker import SINGLE_HOST_DEVELOPMENT

        meter.record(tmp_path, run_id="r", client_id="c", started_at=1.0, finished_at=2.0,
                     cpu=1.0, memory_mb=512, wall_clock_s=60, state="finished",
                     outcome="succeeded", bytes_out=1,
                     worker_topology=SINGLE_HOST_DEVELOPMENT, allowance_sha256="a" * 64)
        line = meter.read(tmp_path)[0]
        assert line["worker_topology"] == SINGLE_HOST_DEVELOPMENT
        assert "not isolation" in line["worker_topology_means"]

    def test_and_a_caller_cannot_put_its_own_words_there(self):
        """It is derived from the label, so it cannot become a place to write anything."""
        import inspect

        from agentnode_sdk.gateway import meter

        assert "worker_topology_means" not in str(inspect.signature(meter.record))


class TestNoPathInTheGatewayReachesARuntimeDirectly:
    """A scan for the word "docker" is not a gate; it is a spelling check.

    Review made the point: a direct backend call that never names an executable —
    `self.backend.run_process(...)`, `ContainerBackend().remove(...)` — passes a text scan
    completely while being exactly the thing the seam exists to prevent. So this walks the
    gateway's syntax instead of its characters.

    The rule: `self.backend` may be touched in TWO places and nowhere else — the `backend`
    property that builds it, and the `worker` property that wraps it in a `LocalWorker`. Anywhere
    else is the control plane driving a runtime.
    """

    def _offending_uses(self):
        import ast
        import inspect

        from agentnode_sdk.gateway import server

        tree = ast.parse(inspect.getsource(server))
        # The two properties that are allowed to know a runtime exists, and the import
        # block, where naming the class is how it gets into the module at all.
        allowed = {"backend", "worker"}
        offending = []

        class Look(ast.NodeVisitor):
            def __init__(self):
                self.where = []

            def visit_FunctionDef(self, node):
                self.where.append(node.name)
                self.generic_visit(node)
                self.where.pop()

            def _here(self):
                return self.where[-1] if self.where else "<module>"

            def _outside(self):
                return not self.where or self.where[-1] not in allowed

            def visit_Attribute(self, node):
                # self.backend.<anything> -- the attribute chain, not the property itself
                inner = node.value
                if (isinstance(inner, ast.Attribute) and inner.attr == "backend"
                        and isinstance(inner.value, ast.Name) and inner.value.id == "self"):
                    if self._outside():
                        offending.append((self._here(), node.attr, node.lineno))
                self.generic_visit(node)

            def visit_Assign(self, node):
                # A local variable is the obvious way around an attribute-chain check:
                #     runtime = self.backend
                #     runtime.remove(...)
                # so binding it to a name outside the two properties is itself the offence,
                # whatever is done with the name afterwards.
                value = node.value
                if (isinstance(value, ast.Attribute) and value.attr == "backend"
                        and isinstance(value.value, ast.Name) and value.value.id == "self"
                        and self._outside()):
                    offending.append((self._here(), "bound to a local name", node.lineno))
                self.generic_visit(node)

            def visit_Name(self, node):
                # And building one directly needs no `self.backend` at all.
                if node.id in ("ContainerBackend", "LocalWorker") and self._outside():
                    offending.append((self._here(), "constructs " + node.id, node.lineno))
                self.generic_visit(node)

        Look().visit(tree)
        return offending

    def test_the_gateway_touches_its_backend_in_two_places_and_nowhere_else(self):
        offending = self._offending_uses()
        assert offending == [], (
            "the control plane drives a runtime directly at: "
            + ", ".join(f"{fn}() line {ln} -> .{attr}" for fn, attr, ln in offending))

    def test_and_the_rule_catches_a_call_that_names_no_executable(self):
        """The counter-case for the gate itself: it must see a call it cannot read as text."""
        import ast

        tree = ast.parse(
            "class S:\n"
            "    def somewhere_else(self):\n"
            "        return self.backend.run_process(spec)\n")
        found = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Attribute)
                 and n.value.attr == "backend"]
        assert found, "the shape this gate looks for is not the shape a direct call has"


class TestEveryJobsEvidenceBindsWhereItWasMeasured:
    """The conformance report bound these; a single run's record did not.

    Somebody holding one run's evidence could not tell which arrangement produced it without
    going to find a separate document and trusting that it was the same one. The five values a
    reader needs are the operator policy, the effective policy digest, the backend version, the
    worker's configuration and the topology — and all five have to be on the run itself.
    """

    MUST_BIND = ("requested_policy", "effective_policy_sha256", "backend_version",
                 "worker_configuration_sha256", "worker_topology")

    def _a_record(self):
        from agentnode_sdk.gateway.server import RunRecord

        record = RunRecord(run_id="r" * 32, job_id="j")
        record.requested_policy = {"network": "none"}
        record.effective_policy_sha256 = "e" * 64
        record.backend_version = "podman 5.8.4"
        record.worker_configuration_sha256 = "c" * 64
        record.worker_topology = SINGLE_HOST_DEVELOPMENT
        return record

    def test_every_mandated_value_is_on_the_run(self):
        shown = self._a_record().public()
        for field in self.MUST_BIND:
            assert field in shown, f"a run's evidence does not bind {field}"
            assert shown[field], f"a run's evidence binds {field} as empty"

    def test_and_the_topology_comes_with_what_it_means(self):
        shown = self._a_record().public()
        assert "not isolation" in shown["worker_topology_means"]

    def test_they_are_taken_at_admission_and_not_at_the_end(self):
        """A worker replaced mid-flight must not rewrite what a finished run was measured on."""
        import inspect

        from agentnode_sdk.gateway import server

        text = inspect.getsource(server.GatewayService.submit)
        assert "record.worker_topology = self.worker.topology" in text
        assert "taken now rather than at the end" in text

    def test_a_record_that_binds_none_of_them_is_visibly_empty(self):
        """The counter-case: the check must be able to tell bound from unbound."""
        from agentnode_sdk.gateway.server import RunRecord

        shown = RunRecord(run_id="r" * 32, job_id="j").public()
        assert not shown["worker_topology"]
        assert not shown["backend_version"]


class TestAWorkerLostAtCleanupStillEndsTheRun:
    """The case most likely to happen, and the one that used to hang.

    A worker lost during a run is a worker that cannot be asked about cleanup either -- so the
    cleanup question fails precisely when it matters. It was an unguarded call inside a
    `finally`, so that exception escaped before the terminal state was published and the run
    never reached one. A client polling it waits forever on something nobody will finish, which
    is worse than either answer it could have been given.
    """

    def test_the_run_still_reaches_a_terminal_state(self):
        from agentnode_sdk.gateway.protocol import TERMINAL_STATES

        assert "unverified" in TERMINAL_STATES

    def test_not_knowing_is_recorded_as_not_knowing(self):
        """None, not False: "nobody could ask" is not "something was left behind"."""
        from agentnode_sdk.gateway.server import RunRecord

        record = RunRecord(run_id="r" * 32, job_id="j")
        assert record.cleanup_verified is None
        assert record.public()["cleanup_verified"] is None

    def test_and_it_is_not_turned_into_a_job_that_failed(self):
        """Nothing about the job is known from a cleanup question that could not be asked."""
        import inspect

        from agentnode_sdk.gateway import server

        # The line BEFORE the cleanup question must be the `try:` that guards it. Asserting
        # only that some `try:` exists in the block stopped discriminating the moment a second
        # guard was added beside it -- the meter's -- and a check that any guard exists is not
        # a check that THIS one does.
        lines = inspect.getsource(server.GatewayService._run).splitlines()
        asking = next(i for i, l in enumerate(lines) if "self.worker.gone(" in l)
        assert lines[asking - 1].strip() == "try:", (
            "the cleanup question is not the thing that try guards: "
            + lines[asking - 1].strip())
        after = "\n".join(lines[asking:asking + 12])
        assert 'terminal = "unverified"' in after
        assert "record.cleanup_verified = None" in after

    def test_a_refusal_that_was_already_decided_is_not_overwritten(self):
        """A job that genuinely failed keeps saying so, even if cleanup then could not be asked."""
        import inspect

        from agentnode_sdk.gateway import server

        guarded = inspect.getsource(server.GatewayService._run).split("finally:")[-1]
        assert 'if terminal not in ("refused", "cancelled")' in guarded
        assert "if not record.refusal:" in guarded


class TestTheConformanceReportBindsWhereItWasMeasured:
    """The report is what says what this sandbox enforces. What it was measured ON belongs in it.

    A report that lost one of those bindings would still look like a report -- the numbers would
    all be there -- while no longer saying which arrangement produced them.
    """

    MUST_BIND = ("image_digest", "backend_version", "worker_topology",
                 "worker_configuration_sha256")

    def test_the_report_is_built_with_every_one_of_them(self):
        import inspect

        from agentnode_sdk.gateway import server

        built = inspect.getsource(server.GatewayService.report_binding)
        for field in self.MUST_BIND:
            assert field + "=" in built, f"the conformance report does not bind {field}"

    def test_and_each_comes_from_the_worker_rather_than_from_here(self):
        """A control plane that filled these in itself would be describing something it guessed."""
        import inspect

        from agentnode_sdk.gateway import server

        built = inspect.getsource(server.GatewayService.report_binding)
        assert "self.worker.image_digest()" in built
        assert "self.worker.topology" in built
        assert "self.worker.configuration_sha256()" in built


class TestWhatTheWorkerIsToldAndWhatItIsNot:
    """It gets the job. It does not get the things that would make an escape worth more.

    The worker runs foreign code, so what reaches it is what an escape reaches. This gateway's
    signing identity, any client's token and the ledger are the three that would turn a contained
    escape into a compromise of everything the gateway has ever said, and none of them is a field
    of anything that crosses.
    """

    FORBIDDEN = ("token", "signing", "identity", "ledger", "secret", "private")

    def test_a_job_carries_the_job_and_nothing_else(self):
        carried = set(a_job().as_message())
        assert carried == {"run_id", "container_name", "command", "artifact", "stdin",
                           "network", "allowed_domains", "limits"}, carried

    def test_and_no_field_of_it_is_named_like_a_secret(self):
        for field in a_job().as_message():
            for bad in self.FORBIDDEN:
                assert bad not in field.lower(), f"a job carries a field called {field}"

    def test_nor_does_what_is_actually_sent(self):
        sent = _a_request_body()
        for field in sent.get("job", {}):
            for bad in self.FORBIDDEN:
                assert bad not in field.lower(), f"the wire carries a field called {field}"

    def test_and_the_check_would_see_one_if_it_were_there(self):
        """The counter-case for the check itself."""
        pretend = dict(a_job().as_message(), client_token="abc")
        assert any(bad in f.lower() for f in pretend for bad in self.FORBIDDEN)
