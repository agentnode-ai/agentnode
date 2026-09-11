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
import struct
import threading
import time

import pytest

from agentnode_sdk.worker import (
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
from agentnode_sdk.worker.remote import SocketWorker, topology_of
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
        seen = wire.Seen(memory=10.0)
        body = wire.request("describe", {}, deadline=2000.0, now=1000.0)
        wire.check(body, seen, now=1000.0)
        body["issued_at"] = 1100.0
        wire.check(body, seen, now=1100.0)

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
        for code in (wire.UNAUTHENTICATED, wire.STALE, wire.REPLAY, wire.TOO_LARGE,
                     wire.UNKNOWN_METHOD, wire.BAD_PARAMS, wire.DEADLINE_PASSED,
                     wire.RUNTIME_ABSENT, wire.INTERNAL, wire.MALFORMED):
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
        assert DIRECTORY_MODE == 0o750
        assert not (SOCKET_MODE & 0o007), "anyone on the machine could reach this socket"


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
            assert (os.stat(os.path.dirname(path)).st_mode & 0o777) == DIRECTORY_MODE
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
