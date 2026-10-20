"""The probe-slot service: a renewal probe takes its run slot from the serving process's own scheduler over
a root-only Unix socket (attest-renewal C11, C20; Codex D6; counter-checks 20-25, 39, 40).

The socket tests need Unix sockets and SO_PEERCRED (Linux; CI runs them). The server is the real one on
a real event loop with the real `Scheduler`; the client is the real blocking client in a thread, or a raw
socket for the hostile cases."""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from agentnode_sdk.remote_mcp import probe_slot as PS
from agentnode_sdk.remote_mcp.scheduler import Scheduler

POSIX = os.name == "posix" and hasattr(socket, "SO_PEERCRED")
needs_unix = pytest.mark.skipif(not POSIX, reason="Unix sockets with SO_PEERCRED (Linux); CI runs these")


class Harness:
    """A real server on its own loop thread, the real scheduler behind it."""

    def __init__(self, tmp_path, *, limits=None, global_slots=4, max_wait=30.0, per_account=2, peer_uid=None):
        self.dir = Path("/tmp") / ("pslot-%d-%s" % (os.getpid(), os.urandom(3).hex()))   # short: sun_path limit
        self.path = str(self.dir / "probe-slot.sock")
        self.loop = asyncio.new_event_loop()
        self.limits = limits if limits is not None else {"t-a": (1, 4), "t-b": (1, 4)}
        self.sched = Scheduler(global_slots=global_slots, global_queue=32, max_wait=max_wait, per_account=per_account)
        self.server = PS.ProbeSlotServer(self.sched, lambda tid: self.limits.get(tid), self.path,
                                         peer_uid_required=os.getuid() if peer_uid is None else peer_uid)
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.call(self.server.start())

    def call(self, coro, timeout=20):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def on_loop(self, fn):
        async def go():
            return fn()
        return self.call(go())

    def running(self, target=None):
        return self.on_loop(lambda: self.sched.running(target))

    def wait_for(self, cond, seconds=5.0):
        end = time.time() + seconds
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.02)
        return False

    def close(self):
        try:
            self.call(self.server.stop())
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(5)
            try:
                os.rmdir(self.dir)
            except OSError:
                pass


@pytest.fixture
def h(tmp_path):
    if not POSIX:
        pytest.skip("Unix sockets with SO_PEERCRED (Linux)")
    harness = Harness(tmp_path)
    yield harness
    harness.close()


def _raw(path, payload: bytes, *, read=True):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10)
    s.connect(path)
    if payload:
        s.sendall(payload)
    if not read:
        return s, None
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(512)
        if not chunk:
            break
        buf += chunk
    return s, (json.loads(buf) if buf else None)


@needs_unix
def test_a_grant_is_one_real_slot_released_exactly_on_close(h):
    """CC20 / CC23: the probe occupies one global and one target slot while held; closing frees it."""
    slot = PS.acquire("t-a", path=h.path)
    assert h.running("t-a") == 1 and h.running() == 1 and h.server.granted == 1
    slot.release()
    assert h.wait_for(lambda: h.running() == 0) and h.server.released == 1


@needs_unix
def test_a_full_house_of_calls_does_not_starve_the_slot_service(h):
    """Every running call occupies a thread of the loop's DEFAULT pool (the transport dispatches there); the slot
    service answers from its own pool, so a probe still gets its answer (found on the two-host stand)."""
    from concurrent.futures import ThreadPoolExecutor
    block = threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)
    h.loop.call_soon_threadsafe(h.loop.set_default_executor, pool)
    h.loop.call_soon_threadsafe(lambda: h.loop.run_in_executor(None, block.wait, 30))   # the default pool: full
    time.sleep(0.2)
    try:
        t0 = time.time()
        slot = PS.acquire("t-a", path=h.path, budget=0)
        assert time.time() - t0 < 5 and h.running("t-a") == 1
        slot.release()
    finally:
        block.set()
        pool.shutdown(wait=False)


@needs_unix
def test_a_killed_client_frees_its_slot(h):
    """CC23: the prober dies holding the slot -> released at once (EOF), exactly once."""
    code = ("import sys, time; sys.path.insert(0, %r); from agentnode_sdk.remote_mcp import probe_slot as P; "
            "s = P.acquire('t-a', path=%r); print('GRANTED', flush=True); time.sleep(60)"
            % (str(Path(PS.__file__).parents[2]), h.path))
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, universal_newlines=True)
    try:
        assert proc.stdout.readline().strip() == "GRANTED"
        assert h.running("t-a") == 1
        proc.kill()
        proc.wait(10)
        assert h.wait_for(lambda: h.running() == 0) and h.server.released == 1
    finally:
        if proc.poll() is None:
            proc.kill()


@needs_unix
def test_a_probe_waits_its_turn_behind_queued_calls(h):
    """CC21: FIFO -- a call already queued for the target is granted before the probe, never after."""
    order = []

    async def call_holds():
        return await h.sched.acquire("acct-0000000000000001", "t-a", concurrency=1, queue=4)
    first = h.call(call_holds())                                        # the target's one slot: busy

    async def queued_call():
        g = await h.sched.acquire("acct-0000000000000002", "t-a", concurrency=1, queue=4)
        order.append("call")
        return g
    fut = asyncio.run_coroutine_threadsafe(queued_call(), h.loop)
    assert h.wait_for(lambda: h.on_loop(lambda: h.sched.queued("t-a")) == 1)
    got = {}
    th = threading.Thread(target=lambda: got.setdefault("slot", PS.acquire("t-a", path=h.path)))
    th.start()
    assert h.wait_for(lambda: h.on_loop(lambda: h.sched.queued("t-a")) == 2)
    h.on_loop(first.release)
    second = fut.result(10)
    time.sleep(0.3)
    assert order == ["call"] and "slot" not in got                    # the probe is still waiting
    h.on_loop(second.release)
    th.join(10)
    assert "slot" in got and h.running("t-a") == 1
    got["slot"].release()


@needs_unix
def test_a_probe_never_counts_against_a_customer_account(h):
    """CC22: the synthetic probe account, never a real one; a customer at its limit does not block it."""
    async def fill():
        return [await h.sched.acquire("acct-00000000000000aa", "t-b", concurrency=4, queue=4) for _ in range(2)]
    held = h.call(fill())                                              # the customer account is at its 2
    slot = PS.acquire("t-a", path=h.path, budget=0)
    assert h.on_loop(lambda: h.sched.in_flight("acct-00000000000000aa")) == 2
    assert h.on_loop(lambda: h.sched.in_flight(PS.PROBE_ACCOUNT_PREFIX + "t-a")) == 1
    slot.release()
    for g in held:
        h.on_loop(g.release)


@needs_unix
def test_refusals_are_the_schedulers_own(h):
    """A full target answers like a call would (`target_full`); an unknown target `target_unknown`."""
    h.limits["t-c"] = (1, 1)

    async def busy():
        a = await h.sched.acquire("acct-0000000000000001", "t-c", concurrency=1, queue=1)
        b = asyncio.ensure_future(h.sched.acquire("acct-0000000000000002", "t-c", concurrency=1, queue=1))
        await asyncio.sleep(0.05)
        return a, b
    a, b = h.call(busy())
    with pytest.raises(PS.SlotRefused) as full:
        PS.acquire("t-c", path=h.path, budget=0)
    assert full.value.code == "target_full"
    with pytest.raises(PS.SlotRefused) as unknown:
        PS.acquire("t-zz", path=h.path, budget=0)
    assert unknown.value.code == "target_unknown"
    h.on_loop(a.release)


@needs_unix
def test_retries_are_bounded_and_go_through_the_queue_again(h):
    h.limits["t-c"] = (1, 1)

    async def busy():
        a = await h.sched.acquire("acct-0000000000000001", "t-c", concurrency=1, queue=1)
        b = asyncio.ensure_future(h.sched.acquire("acct-0000000000000002", "t-c", concurrency=1, queue=1))
        await asyncio.sleep(0.05)
        return a, b
    a, b = h.call(busy())
    clock, sleeps = {"t": 0.0}, []

    def sleep(x):
        sleeps.append(x)
        clock["t"] += x
    with pytest.raises(PS.SlotRefused) as refused:
        PS.acquire("t-c", path=h.path, budget=40, clock=lambda: clock["t"], sleep=sleep)
    assert refused.value.code == "target_full" and sleeps[:3] == [2.0, 4.0, 8.0] and sum(sleeps) <= 40
    assert h.server.granted == 0
    h.on_loop(a.release)


@needs_unix
@pytest.mark.parametrize("payload", [b'{"acquire": "t-a"}' + b" " * 300 + b"\n", b"{not json}\n", b'{"acquire": "t-a", "and": 1}\n',
                                     b'{"acquire": "../t-a"}\n', b'["t-a"]\n'])
def test_a_malformed_request_is_refused_and_holds_nothing(h, payload):
    """CC25: bounded framing, exact shape, a valid target id."""
    s, reply = _raw(h.path, payload)
    s.close()
    assert reply == {"refused": "protocol"} and h.server.granted == 0 and h.running() == 0


@needs_unix
def test_a_second_request_on_a_granted_connection_releases_the_slot(h):
    """CC25: one request per connection -- anything more is refused and the grant given back."""
    s, reply = _raw(h.path, b'{"acquire": "t-a"}\n')
    assert reply == {"granted": "t-a"} and h.running() == 1
    s.sendall(b'{"acquire": "t-b"}\n')
    buf = s.recv(512)
    s.close()
    assert json.loads(buf) == {"refused": "protocol"}
    assert h.wait_for(lambda: h.running() == 0) and h.server.released == 1


@needs_unix
def test_a_silent_client_is_cut_off(h, monkeypatch):
    monkeypatch.setattr(PS, "READ_DEADLINE_SECONDS", 0.3)
    s, reply = _raw(h.path, b"")
    s.close()
    assert reply == {"refused": "protocol"} and h.server.granted == 0


@needs_unix
def test_a_peer_with_another_uid_is_refused(tmp_path):
    """CC24: only UID 0 in production (here: anybody but the test's own uid is refused)."""
    other = Harness(tmp_path, peer_uid=os.getuid() + 1)
    try:
        s, reply = _raw(other.path, b'{"acquire": "t-a"}\n')
        s.close()
        assert reply == {"refused": "peer"} and other.server.granted == 0 and other.running() == 0
    finally:
        other.close()


@needs_unix
def test_socket_and_directory_modes(h):
    assert stat.S_IMODE(os.lstat(h.path).st_mode) == 0o600 and stat.S_ISSOCK(os.lstat(h.path).st_mode)
    assert stat.S_IMODE(os.lstat(h.dir).st_mode) == 0o700 and os.lstat(h.dir).st_uid == os.geteuid()


@needs_unix
def test_server_shutdown_releases_held_grants(tmp_path):
    other = Harness(tmp_path)
    slot = PS.acquire("t-a", path=other.path)
    assert other.running() == 1
    other.call(other.server.stop())
    assert other.wait_for(lambda: other.running() == 0) and not slot.still_held()
    assert not os.path.exists(other.path)
    other.loop.call_soon_threadsafe(other.loop.stop)
    other.thread.join(5)
    os.rmdir(other.dir)


@needs_unix
def test_a_stale_socket_is_replaced_but_a_live_listener_never(tmp_path):
    d = Path("/tmp") / ("pslot-stale-%s" % os.urandom(3).hex())
    os.mkdir(d, 0o700)
    path = str(d / "probe-slot.sock")
    try:
        dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        dead.bind(path)
        dead.close()                                           # a pathname nobody listens on
        PS.clear_stale_socket(path)
        assert not os.path.exists(path)
        live = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        live.bind(path)
        live.listen(1)
        with pytest.raises(PS.SlotServiceRefused):
            PS.clear_stale_socket(path)
        assert os.path.exists(path)                            # never unlinked under a live listener
        live.close()
        os.unlink(path)
        Path(path).write_text("not a socket")
        with pytest.raises(PS.SlotServiceRefused):
            PS.clear_stale_socket(path)
        os.unlink(path)
    finally:
        for p in d.glob("*"):
            p.unlink()
        os.rmdir(d)


@needs_unix
def test_an_unsafe_runtime_directory_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(PS.SlotServiceRefused):
        PS.prepare_runtime_dir(str(link), owner_uid=os.geteuid())
    open_dir = tmp_path / "open"
    open_dir.mkdir()
    os.chmod(open_dir, 0o755)
    with pytest.raises(PS.SlotServiceRefused):
        PS.prepare_runtime_dir(str(open_dir), owner_uid=os.geteuid())
    with pytest.raises(PS.SlotServiceRefused):
        PS.prepare_runtime_dir(str(real), owner_uid=os.geteuid() + 1)


def test_no_socket_means_no_slot(tmp_path):
    """Control not serving (or no Unix sockets): the probe gets no slot and runs nothing."""
    with pytest.raises(PS.SlotRefused) as refused:
        PS.acquire("t-a", path=str(tmp_path / "absent.sock"), budget=0)
    assert refused.value.code == "control_not_serving"


def test_there_is_no_network_variant():
    """CC39: Unix socket only -- no TCP listener, no address option, nothing that makes one."""
    src = inspect.getsource(PS)
    for forbidden in ("AF_INET", "start_server(", "create_server(", "bind((", "host=", "port="):
        assert forbidden not in src, forbidden
    assert "start_unix_server" in src
    from agentnode_sdk.remote_mcp import production
    psrc = inspect.getsource(production)
    assert "--probe-slot-socket" in psrc and "probe-slot-host" not in psrc and "probe-slot-port" not in psrc


# ---------------------------------------------------------------------- CC40: the ASGI lifespan
def _lifespan(app, messages):
    sent = []

    async def go():
        q = list(messages)

        async def receive():
            return q.pop(0)

        async def send(m):
            sent.append(m)
        await app({"type": "lifespan"}, receive, send)
    asyncio.run(go())
    return sent


def _app(tmp_path):
    from .catalog_fixtures import Stand
    return Stand(tmp_path).app


def test_lifespan_runs_hooks_in_order(tmp_path):
    app, calls = _app(tmp_path), []

    def hook(name):
        async def start():
            calls.append("start " + name)

        async def stop():
            calls.append("stop " + name)
        return start, stop
    app.lifespan_hooks += [hook("a"), hook("b")]
    sent = _lifespan(app, [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    assert [m["type"] for m in sent] == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert calls == ["start a", "start b", "stop b", "stop a"]


def test_a_failing_startup_hook_fails_the_startup_and_unwinds(tmp_path):
    app, calls = _app(tmp_path), []

    async def ok():
        calls.append("start a")

    async def ok_stop():
        calls.append("stop a")

    async def boom():
        raise RuntimeError("cannot")
    app.lifespan_hooks += [(ok, ok_stop), (boom, ok_stop)]
    sent = _lifespan(app, [{"type": "lifespan.startup"}])
    assert sent[0]["type"] == "lifespan.startup.failed" and "cannot" in sent[0]["message"]
    assert calls == ["start a", "stop a"]


def test_an_unknown_lifespan_message_is_an_error_not_a_success(tmp_path):
    app = _app(tmp_path)
    with pytest.raises(RuntimeError):
        _lifespan(app, [{"type": "lifespan.startup"}, {"type": "lifespan.bogus"}])


def test_an_unavailable_slot_service_never_stops_the_serving(tmp_path, capsys):
    """The slot service cannot start (no Unix sockets here / an unsafe directory): calls are still served,
    the failure is named, and every renewal then finds no slot."""
    from agentnode_sdk.remote_mcp.server import attach_probe_slots
    app = _app(tmp_path)
    blocker = tmp_path / "file"
    blocker.write_text("x")

    class Router:
        register = None
    attach_probe_slots(app, Router(), str(blocker / "sub" / "probe-slot.sock"))
    sent = _lifespan(app, [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    assert sent[0]["type"] == "lifespan.startup.complete"
    assert "PROBE_SLOT_UNAVAILABLE" in capsys.readouterr().err
