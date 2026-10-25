"""A worker that DIALS OUT to its control plane and listens nowhere (local-worker slice 1, Codex Q-LW-0001 D1).

The machine is behind NAT; nothing can reach it inbound, and nothing here opens a listening socket. Instead the
worker keeps `P` TCP connections OPEN TO the control plane's reverse hub ("dial-back slots", `P = its signed
concurrency + 4`). On each, the roles of the protocol are exactly those of a worker that listens:

    the worker is the TLS SERVER, the control plane the TLS CLIENT (who opened the TCP connection does not
    decide that -- `wrap_socket(server_side=True)` on a socket this side connected);
    the control plane's certificate is checked as the GATEWAY role, deployment and instance (`TlsListener.admit`);
    then ONE sealed request and ONE sealed answer (`Bench.converse`: MAC with the pair's key, freshness, replay
    floor, nonce memory, lease, journal), and the connection is closed.

So the whole security model of `worker/protocol.py`, `worker/lease.py` and `worker/journal.py` is unchanged; only
who dialled whom is. A slot is never reused: after its one request, or after `IDLE_SECONDS` without one (below
the 120 s freshness window), it is closed and a fresh one is dialled. A failed dial backs off (0.5 s .. 30 s, full
jitter), and the whole agent dials at most `MAX_DIALS_PER_SECOND` times a second.

What this is NOT: a tunnel, a port forward, UPnP, or a stream multiplexer. There is no inbound path to forward.
"""
from __future__ import annotations

import random
import socket
import sys
import threading
import time
from urllib.parse import urlparse

#: How long a parked slot waits for its one request before it is closed and replaced (< FRESHNESS_SECONDS 120).
IDLE_SECONDS = 110.0
#: Extra slots beyond the run concurrency, for lease renewal, result, stop, trust and probe traffic.
EXTRA_SLOTS = 4
MAX_DIALS_PER_SECOND = 4.0
BACKOFF_MIN, BACKOFF_MAX = 0.5, 30.0
DIAL_TIMEOUT_SECONDS = 10.0
#: The one feature a dial-back worker states in `describe` (Codex D11).
FEATURE = "reverse-session/1"


class DialRefused(Exception):
    """The agent will not start: a named reason (`code`), never a fallback to something that listens."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(code + ": " + detail)
        self.code = code
        self.detail = detail


def control_endpoint(address: str, *, allow_loopback: bool = False) -> tuple[str, int]:
    """The control plane's reverse hub as (literal ip, port). A name, a wildcard or a missing port is refused; a
    loopback address only where a test says so (one machine is not the arrangement this is for)."""
    import ipaddress

    parsed = urlparse(address or "")
    if parsed.scheme != "tcps" or parsed.port is None:
        raise DialRefused("control_address_unusable", "the control plane is tcps://<literal-ip>:<port>")
    host = (parsed.hostname or "").strip("[]")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise DialRefused("control_address_unusable", "the control plane is named by a literal IP address") from None
    if ip.is_unspecified or (ip.is_loopback and not allow_loopback):
        raise DialRefused("control_address_unusable", "%s is not an address to dial a control plane at" % host)
    return host, int(parsed.port)


def platform_report() -> dict:
    """What this machine is, as the agent sees it -- a CLAIM, recorded as such; suitability is decided by what the
    control plane observes inside isolated jobs, never by this (Codex D6)."""
    import platform

    out = {"os": sys.platform, "machine": platform.machine(), "release": platform.release(),
           "python": platform.python_version()}
    if sys.platform.startswith("win"):
        out["wsl2_distribution"] = _wsl_distributions()
    return out


def _wsl_distributions() -> str:
    """'none' when WSL has no installed distribution, 'present' when it has one, 'unknown' when it cannot be asked.
    Nothing is installed or started here (DECISION-0001 R10)."""
    import subprocess

    try:
        done = subprocess.run(["wsl.exe", "--list", "--quiet"], capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    text = done.stdout.decode("utf-16-le", "replace") if done.stdout[:2] != b"\xff\xfe" else done.stdout.decode("utf-16")
    names = [line.strip().strip("\x00") for line in text.splitlines() if line.strip().strip("\x00")]
    if done.returncode != 0 and not names:
        return "none"
    return "present" if names else "none"


def private_state_problems(files, directories=()) -> list[str]:
    """What is wrong with how this agent's secrets are kept (Codex D5/C18), or []: every secret file is this
    account's and 0600 at most, every state directory this account's and writable by nobody else. On Windows the
    restrictive ACLs are not proved by this build, so the answer is a refusal (slice 2 brings a Windows service)."""
    import os
    import stat

    if sys.platform.startswith("win"):
        return ["restrictive ACLs on the agent's secrets are not proved on Windows; the agent does not start"]
    problems = []
    me = os.geteuid()
    for path in files:
        try:
            st = os.stat(path)
        except OSError as exc:
            problems.append("%s: unreadable (%s)" % (os.path.basename(str(path)), type(exc).__name__))
            continue
        if st.st_uid != me:
            problems.append("%s: not this account's" % os.path.basename(str(path)))
        if stat.S_IMODE(st.st_mode) & 0o077:
            problems.append("%s: mode %04o, more than 0600" % (os.path.basename(str(path)), stat.S_IMODE(st.st_mode)))
    for path in directories:
        try:
            st = os.stat(path)
        except OSError as exc:
            problems.append("%s/: unreadable (%s)" % (os.path.basename(str(path)), type(exc).__name__))
            continue
        if st.st_uid != me:
            problems.append("%s/: not this account's" % os.path.basename(str(path)))
        if stat.S_IMODE(st.st_mode) & 0o022:
            problems.append("%s/: writable by another account" % os.path.basename(str(path)))
    return problems


def refuse_without_isolation(worker) -> None:
    """Before anything is dialled: a worker that cannot isolate a job does not connect at all (`runtime-absent`).
    No job ever runs on this host's own processes (Codex D6/D12)."""
    from agentnode_sdk.worker import protocol as wire

    can = worker.can_it_isolate()
    if not can.available:
        raise DialRefused(wire.RUNTIME_ABSENT, "no isolating runtime here (%s); this agent connects to nothing and "
                          "runs nothing. Platform: %r" % (can.reason or can.backend, platform_report()))


class _Rate:
    """At most `per_second` dials a second, across every slot."""

    def __init__(self, per_second: float) -> None:
        self.gap = 1.0 / float(per_second)
        self.next = 0.0
        self.lock = threading.Lock()

    def wait(self, stopped: threading.Event) -> None:
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next)
            self.next = at + self.gap
        stopped.wait(max(0.0, at - time.monotonic()))


class DialBackAgent:
    """`slots` threads, each: dial -> handshake as TLS server -> wait for one request -> one conversation -> close."""

    def __init__(self, listener, control: str, *, slots: int, say=None, allow_loopback: bool = False,
                 idle: float = IDLE_SECONDS, dial=None, rate: float = MAX_DIALS_PER_SECOND) -> None:
        self.listener = listener
        if str(control or "").startswith("https://"):
            # slice 2: the public 443 path. The name is dialled through `dial` (an `edge.EdgeDialer`), which runs the
            # outer TLS and the upgrade; the slot after it is this one, unchanged.
            from agentnode_sdk.worker import edge as _edge

            if dial is None:
                raise DialRefused("control_address_unusable", "the 443 path is dialled through its edge dialer")
            url = _edge.control_url(control)
            self.host, self.port = url.host, url.port
        else:
            self.host, self.port = control_endpoint(control, allow_loopback=allow_loopback)
        self.slots = max(1, int(slots))
        self.idle = float(idle)
        self.say = say or (lambda text: print(text, flush=True))
        self._dial = dial or socket.create_connection
        self._rate = _Rate(rate)
        self._stopped = threading.Event()
        self._threads: list[threading.Thread] = []
        self.dials = 0
        self.failed_dials = 0
        self.conversations = 0
        #: slots the control plane closed without a request (not eligible now, slots full): backed off from
        self.refused_slots = 0
        #: slice 2 health (what an update checks): when the control plane last completed an inner handshake on one of
        #: this agent's slots, and the name of the last refusal of a dial
        self.last_admitted_at = 0.0
        self.last_refusal = ""
        try:
            listener.on_admitted = self._admitted
        except AttributeError:                                # pragma: no cover - a listener stand-in
            pass

    def _admitted(self) -> None:
        self.last_admitted_at = time.time()
        self.last_refusal = ""

    def start(self) -> None:
        for i in range(self.slots):
            t = threading.Thread(target=self._slot, name="dial-back-%d" % i, daemon=True)
            t.start()
            self._threads.append(t)

    def serve_forever(self) -> None:
        self.start()
        try:
            while not self._stopped.wait(1.0):
                pass
        finally:
            self.stop()

    def stop(self) -> None:
        self._stopped.set()

    def _slot(self) -> None:
        backoff = 0.0
        while not self._stopped.is_set():
            self._rate.wait(self._stopped)
            if self._stopped.is_set():
                return
            self.dials += 1
            try:
                raw = self._dial((self.host, self.port), DIAL_TIMEOUT_SECONDS)
            except OSError as exc:
                self.failed_dials += 1
                self.last_refusal = str(getattr(exc, "code", "") or type(exc).__name__)
                backoff = min(BACKOFF_MAX, max(BACKOFF_MIN, backoff * 2 or BACKOFF_MIN))
                self.say("  the control plane could not be reached (%s); next try within %.1f s"
                         % (getattr(exc, "code", "") or type(exc).__name__, backoff))
                self._stopped.wait(random.uniform(0.0, backoff))   # full jitter
                continue
            _keepalive(raw)
            try:
                admitted = self.listener._one(raw, idle=self.idle)
            except Exception as exc:                          # noqa: BLE001 - a slot never takes the agent down
                admitted = False
                self.say("  a slot ended with %s" % type(exc).__name__)
            if admitted:
                backoff = 0.0
                self.conversations += 1
            else:
                self.refused_slots += 1
                backoff = min(BACKOFF_MAX, max(BACKOFF_MIN, backoff * 2 or BACKOFF_MIN))
                self._stopped.wait(random.uniform(0.0, backoff))


def _keepalive(sock) -> None:
    """Diagnostic only (the 110 s idle bound is what replaces a slot): ask the kernel to notice a dead peer."""
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for name, value in (("TCP_KEEPIDLE", 30), ("TCP_KEEPINTVL", 10), ("TCP_KEEPCNT", 3)):
            if hasattr(socket, name):
                sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, name), value)
    except OSError:                                           # pragma: no cover
        pass


def slots_for(concurrency: int) -> int:
    """`P = concurrency + 4` (Codex D1, DECISION-0001 R4)."""
    return max(1, int(concurrency)) + EXTRA_SLOTS


__all__ = ["BACKOFF_MAX", "BACKOFF_MIN", "DialBackAgent", "DialRefused", "EXTRA_SLOTS", "FEATURE", "IDLE_SECONDS",
           "MAX_DIALS_PER_SECOND", "control_endpoint", "platform_report", "refuse_without_isolation", "slots_for"]
