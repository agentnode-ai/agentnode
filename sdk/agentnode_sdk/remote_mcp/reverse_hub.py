"""The control plane's end of dial-back workers (local-worker slice 1, Codex Q-LW-0001 D1/D2/D9).

A worker behind NAT dials this hub; nothing here dials a worker. The hub:

  * listens ONLY when configured (`reverse_listen`, absent = no socket at all) and only on ONE literal private
    address -- never every interface, never loopback in production, never the live worker port 8443;
  * on each accepted TCP connection takes the TLS CLIENT role (the worker is the TLS server, as a listening worker
    is) and checks the peer exactly as `worker/tls.open_to_worker` does: role WORKER, this deployment, an instance
    belonging to a dial-back target that may be parked now -- else the connection is closed and nothing is parked;
  * parks it as ONE slot of that instance (at most `2P` per instance, `P = concurrency + 4`, and `max_total`);
  * hands a slot out EXCLUSIVELY (`checkout`), re-checking at that moment the target's status, the owner's account
    and device (`authority`) and the kill switch -- a failure drains every slot of the instance and is a named
    refusal; a dead slot (its worker hung up, or older than `MAX_SLOT_AGE`) is never handed out;
  * never holds a pair key: the sealed frames pass between the caller and the worker untouched, so the MAC and the
    identity check stay end to end. The local relay (`RelayServer`, a unix socket for the control plane's OTHER
    processes) moves exactly one bounded frame each way and closes.

A slot carries one request and is closed by its user; it is never returned to the pool.
"""
from __future__ import annotations

import collections
import ipaddress
import json
import os
import select
import socket
import ssl
import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from agentnode_sdk.worker import protocol as wire

#: A parked slot older than this is not handed out (its worker closes it at 110 s and dials a new one).
MAX_SLOT_AGE = 115.0
HANDSHAKE_SECONDS = 10.0
DEFAULT_MAX_TOTAL = 256
#: The live worker port: a reverse hub never takes it (Codex C3).
FORBIDDEN_PORTS = (8443,)
#: Statuses whose worker may hold a parked slot (probe and activation need them; terminal/suspended never).
PARKABLE = ("enrolled", "probing", "active")
#: The reaper judges every parked identity's eligibility again this often (in 1-s passes).
DRAIN_EVERY_PASSES = 2


class SlotRefused(Exception):
    """No slot for this target: `code` is a routing code (`target_offline`, `target_device_revoked`, ...)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code + (": " + detail if detail else ""))
        self.code = code
        self.detail = detail


def listen_endpoint(address: str, *, allow_loopback: bool = False) -> tuple[str, int]:
    """The private address a hub may listen on: tcps://<literal private ip>:<port>; never a wildcard, a public
    address, loopback (except in a test), or the live worker port."""
    parsed = urlparse(address or "")
    if parsed.scheme != "tcps" or parsed.port is None:
        raise ValueError("reverse_listen is tcps://<literal private ip>:<port>")
    host = (parsed.hostname or "").strip("[]")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("reverse_listen names a literal IP address, not %r" % host) from None
    if ip.is_unspecified:
        raise ValueError("reverse_listen may not be every interface")
    if ip.is_loopback and not allow_loopback:
        raise ValueError("reverse_listen may not be a loopback address here")
    if not ip.is_loopback and not ip.is_private:
        raise ValueError("reverse_listen must be a private address in slice 1 (no public route)")
    if int(parsed.port) in FORBIDDEN_PORTS:
        raise ValueError("reverse_listen may not use port %d" % parsed.port)
    return host, int(parsed.port)


@dataclass
class _Slot:
    connection: object
    who: object
    der: bytes
    parked_at: float


def slots_for(concurrency: int) -> int:
    return max(1, int(concurrency)) + 4


def _dead(connection) -> bool:
    """A parked slot is dead when its worker hung up or sent application data it may not send before a request.
    Readable alone is NOT dead: after a TLS 1.3 handshake the worker's side (the TLS server) sends session tickets,
    which make the socket readable without being application data -- so one non-blocking read decides."""
    try:
        ready = bool(getattr(connection, "pending", lambda: 0)()) or bool(select.select([connection], [], [], 0)[0])
    except (OSError, ValueError):
        return True
    if not ready:
        return False
    timeout = connection.gettimeout()
    try:
        connection.setblocking(False)
        connection.recv(1)                                    # b"" = hung up; a byte = a worker out of turn
        return True
    except (ssl.SSLWantReadError, BlockingIOError):
        return False                                          # only handshake records were there: alive
    except (OSError, ssl.SSLError):
        return True
    finally:
        try:
            connection.settimeout(timeout)
        except OSError:                                       # pragma: no cover
            pass


def spki_of(der: bytes) -> bytes:
    """The DER SubjectPublicKeyInfo of a certificate: what the 443 edge's slot proof and the inner TLS must share."""
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    return x509.load_der_x509_certificate(der).public_key().public_bytes(Encoding.DER,
                                                                           PublicFormat.SubjectPublicKeyInfo)


def _close(connection) -> None:
    try:
        connection.close()
    except OSError:                                           # pragma: no cover
        pass


class ReverseHub:
    """`targets()` -> {instance: {"target_id", "status", "concurrency"}} for every dial-back target (from the
    verified register); `eligible(target_id)` -> "" or a routing code (account, device, kill switch, status)."""

    def __init__(self, address: str, tls, *, targets: Callable[[], dict], eligible: Callable[[str], str],
                 max_total: int = DEFAULT_MAX_TOTAL, allow_loopback: bool = False, say=None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        from agentnode_sdk.worker.tls import Contexts

        # slice 2: `address=None` is a hub reached ONLY through its 443 edge (`reverse_edge.EdgeServer`): it binds no
        # TCP address of its own at all
        self.host, self.port = (None, None) if address is None else listen_endpoint(address,
                                                                                    allow_loopback=allow_loopback)
        self.tls = tls
        self._targets = targets
        self._eligible = eligible
        self.max_total = int(max_total)
        self.say = say or (lambda text: print(text, flush=True))
        self.clock = clock
        self._contexts = Contexts(ssl.PROTOCOL_TLS_CLIENT, tls, say=self.say)
        self._pools: dict[str, collections.deque] = collections.defaultdict(collections.deque)
        #: slice 2: connections the 443 edge admitted that have not finished their inner handshake yet -- counted
        #: against the same `2P` per identity and `max_total` as parked slots (Codex Q-LW2-0001 D2)
        self._pending: collections.Counter = collections.Counter()
        self._cond = threading.Condition()
        self._socket: socket.socket | None = None
        self._stopped = threading.Event()
        self.refused = collections.Counter()

    # ------------------------------------------------------------ listening
    def open(self) -> tuple[str, int]:
        family = socket.AF_INET6 if ":" in self.host else socket.AF_INET
        s = socket.socket(family, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.port))
        s.listen(64)
        s.settimeout(1.0)
        self._socket = s
        self.port = s.getsockname()[1]
        return self.host, self.port

    def start(self) -> None:
        if self.host is not None:
            if self._socket is None:
                self.open()
            self._acceptor = threading.Thread(target=self._accept_loop, name="reverse-hub", daemon=True)
            self._acceptor.start()
        threading.Thread(target=self._reap_loop, name="reverse-hub-reap", daemon=True).start()

    def stop(self) -> None:
        self._stopped.set()
        if self._socket is not None:
            # On Linux a listening socket another thread is blocked in accept() on is NOT released by close() until
            # that accept returns; shutting it down first wakes the accept, so the address is free when stop returns
            # (a hub restarted on the same port in the same process otherwise met EADDRINUSE).
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            _close(self._socket)
            acceptor = getattr(self, "_acceptor", None)
            if acceptor is not None and acceptor is not threading.current_thread():
                acceptor.join(timeout=3.0)
        with self._cond:
            for pool in self._pools.values():
                while pool:
                    _close(pool.popleft().connection)
            self._cond.notify_all()

    def _accept_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                raw, _peer = self._socket.accept()
            except OSError:
                continue
            threading.Thread(target=self._admit, args=(raw,), daemon=True).start()

    def _reap_loop(self) -> None:
        passes = 0
        while not self._stopped.wait(1.0):
            self.reap()
            passes += 1
            if passes % DRAIN_EVERY_PASSES == 0:
                self.drain_ineligible()

    def drain_ineligible(self) -> int:
        """Codex D4/D9: an identity whose target may not be reached now (suspended, revoked, its device or account
        withdrawn, the kill switch on, unreadable authority) loses its parked slots without waiting for somebody to
        ask for one. Returns how many slots were closed."""
        with self._cond:
            instances = [i for i, pool in self._pools.items() if pool]
        if not instances:
            return 0
        try:
            known = self._targets() or {}
        except Exception:                                     # noqa: BLE001 - unreadable: nothing is reachable
            known = {}
        closed = 0
        for instance in instances:
            t = known.get(instance)
            try:
                why = "not_a_parkable_target" if t is None or t.get("status") not in PARKABLE else \
                    self._eligible(t["target_id"])
            except Exception:                                 # noqa: BLE001 - cannot judge: not reachable
                why = "target_authority_unverifiable"
            if why:
                n = self.drain(instance)
                if n:
                    closed += n
                    self.say("  reverse hub: drained %d slot(s) of %s (%s)" % (n, instance, why))
        return closed

    # ------------------------------------------------------------ admission of a slot
    def reserve(self, instance: str, concurrency: int) -> bool:
        """slice 2: hold one place of `instance` for a connection the edge is about to upgrade, or False when the
        identity already has `2P` parked-or-pending slots or the hub `max_total`. `_admit(..., reserved=instance)`
        gives the place back whatever happens."""
        with self._cond:
            mine = len(self._pools.get(instance) or ()) + self._pending[instance]
            total = sum(len(p) for p in self._pools.values()) + sum(self._pending.values())
            if mine >= 2 * slots_for(int(concurrency or 1)) or total >= self.max_total:
                return False
            self._pending[instance] += 1
            return True

    def _unreserve(self, instance: str) -> None:
        with self._cond:
            if self._pending[instance] > 0:
                self._pending[instance] -= 1
            if self._pending[instance] <= 0:
                del self._pending[instance]

    def _admit(self, raw, *, reserved: str = "", expect_spki: bytes = b"") -> None:
        """Handshake, identity, eligibility, park. `reserved`/`expect_spki` (the 443 edge, slice 2): the place the
        edge reserved for this connection, and the key the slot proof was signed with -- the inner certificate must
        carry exactly that key and that instance, or nothing is parked (`edge_identity_mismatch`)."""
        from agentnode_sdk.pki import identity as _identity

        refused = ""
        instance = ""
        try:
            try:
                raw.settimeout(HANDSHAKE_SECONDS)
                connection = self._contexts.current().wrap_socket(raw, server_side=False)
            except (ssl.SSLError, OSError) as exc:
                self._refuse("handshake", type(exc).__name__)
                _close(raw)
                return
            try:
                der = connection.getpeercert(binary_form=True)
                known = self._targets()
                who = _identity.check_peer(der, deployment=self.tls.deployment, expected_role=_identity.WORKER,
                                           accept_instances=frozenset(known), trust=self.tls.trust(_identity.GATEWAY))
                instance = str(who.instance)
                t = known.get(instance)
                if reserved and (instance != reserved or spki_of(der) != expect_spki):
                    refused = "edge_identity_mismatch"
                elif t is None or t.get("status") not in PARKABLE:
                    refused = "not_a_parkable_target"
                elif self._eligible(t["target_id"]):
                    refused = "not_eligible"
            except Exception as exc:                          # noqa: BLE001 - an unproven peer is never parked
                self._refuse("identity", getattr(exc, "check", "") or type(exc).__name__)
                _close(connection)
                return
            if refused:
                self._refuse(refused, instance)
                _close(connection)
                return
            connection.settimeout(None)
            with self._cond:
                pool = self._pools[instance]
                # a reserved connection's own place is already counted in `_pending`: it moves into the pool
                own = 1 if reserved else 0
                mine = len(pool) + self._pending[instance] - own
                total = sum(len(p) for p in self._pools.values()) + sum(self._pending.values()) - own
                if mine >= 2 * slots_for(int(t.get("concurrency") or 1)) or total >= self.max_total:
                    self._refuse("slot_bound", instance)
                    _close(connection)
                    return
                pool.append(_Slot(connection, who, der, self.clock()))
                self._cond.notify_all()
        finally:
            if reserved:
                self._unreserve(reserved)

    def _refuse(self, why: str, detail: str) -> None:
        self.refused[why] += 1
        self.say("  reverse hub: a connection was closed, nothing parked (%s: %s)" % (why, detail))

    # ------------------------------------------------------------ slots
    def reap(self) -> int:
        """Drop dead or too-old parked slots; returns how many."""
        dropped = 0
        now = self.clock()
        with self._cond:
            for pool in self._pools.values():
                keep = collections.deque()
                while pool:
                    s = pool.popleft()
                    if now - s.parked_at > MAX_SLOT_AGE or _dead(s.connection):
                        _close(s.connection)
                        dropped += 1
                    else:
                        keep.append(s)
                pool.extend(keep)
        return dropped

    def parked(self, instance: str) -> int:
        with self._cond:
            return len(self._pools.get(instance) or ())

    def drain(self, instance: str) -> int:
        with self._cond:
            pool = self._pools.get(instance) or collections.deque()
            n = len(pool)
            while pool:
                _close(pool.popleft().connection)
            return n

    def checkout(self, instance: str, budget: float):
        """An exclusive slot of `instance` -- (connection, proved identity) -- within `budget` seconds, or a named
        refusal. Eligibility is judged HERE, at the moment of use: an ineligible target is drained."""
        t = (self._targets() or {}).get(instance)
        if t is None or t.get("status") not in PARKABLE:
            self.drain(instance)
            raise SlotRefused("target_offline", "not a dial-back target that may be reached now")
        why = self._eligible(t["target_id"])
        if why:
            self.drain(instance)
            raise SlotRefused(why)
        ends = time.monotonic() + max(0.0, float(budget))
        with self._cond:
            while True:
                pool = self._pools.get(instance)
                while pool:
                    s = pool.popleft()
                    if self.clock() - s.parked_at <= MAX_SLOT_AGE and not _dead(s.connection):
                        return s.connection, s.who
                    _close(s.connection)
                left = ends - time.monotonic()
                if left <= 0 or self._stopped.is_set():
                    raise SlotRefused("target_offline", "no slot from the worker within %.1f s" % budget)
                self._cond.wait(min(left, 0.5))


# ---------------------------------------------------------------------- the eligibility of a dial-back target
def target_view(register) -> Callable[[], dict]:
    """{instance: {target_id, status, concurrency, owner, device}} of every dial-back target in the verified
    register; an unreadable register gives {} (nothing parks, nothing is handed out)."""
    def view() -> dict:
        try:
            snap = register.load()
        except Exception:                                     # noqa: BLE001 - unreadable: nothing is reachable
            return {}
        out = {}
        for t in snap.targets.values():
            if not t.is_reverse:
                continue
            out[str(t.worker["instance"])] = {"target_id": t.target_id, "status": t.effective_status(snap.now),
                                              "concurrency": int(t.limits["concurrency"]),
                                              "owner": t.owner_account, "device": t.device_id}
        return out
    return view


def device_authority(authority_error) -> Callable[[str, str], str]:
    """(account, device) -> "" or a routing code, from the control plane's live account/device authority
    (`accounts_login.GatewayLogin.authority_error`, the per-call re-check). Anything unreadable fails closed."""
    from types import SimpleNamespace

    codes = {"device_revoked": "target_device_revoked", "account_mismatch": "target_device_revoked",
             "token_not_device_bound": "target_device_revoked", "run_withdrawn": "target_device_revoked",
             "account_suspended": "target_account_suspended"}

    def check(account: str, device: str) -> str:
        if not account or not device:
            return "target_device_revoked"
        try:
            why = authority_error(SimpleNamespace(device_or_connection_id=device, principal_id=account))
        except Exception:                                     # noqa: BLE001
            return "target_authority_unverifiable"
        if why is None:
            return ""
        return codes.get(str(why), "target_authority_unverifiable")
    return check


def reverse_device_authority(rule: Callable[[str, str], str]) -> Callable[[str, str], str]:
    """slice 2: (account, device) -> "" or a routing code from a direct device rule (`GatewayLogin.
    reverse_device_error`, `agent_authority.Authority.reverse_device_error`) -- which knows a paired WORKER DEVICE
    holds no RUN by design. Anything that raises fails closed."""
    def check(account: str, device: str) -> str:
        if not account or not device:
            return "target_device_revoked"
        try:
            return str(rule(account, device) or "")
        except Exception:                                     # noqa: BLE001
            return "target_authority_unverifiable"
    return check


def eligibility(view: Callable[[], dict], authority: Callable[[str, str], str] | None,
                kill_switch: Callable[[], str] | None) -> Callable[[str], str]:
    """target_id -> "" or the code that refuses it right now: kill switch, account/device, status."""
    def check(target_id: str) -> str:
        try:
            stopped = kill_switch() if kill_switch is not None else ""
        except Exception:                                     # noqa: BLE001 - unreadable is stopped
            stopped = "unreadable"
        if stopped:
            return "target_offline"
        t = next((v for v in view().values() if v["target_id"] == target_id), None)
        if t is None:
            return "target_offline"
        if t["status"] == "suspended":
            return "target_suspended"
        if t["status"] in ("revoked", "expired", "provisioning_failed"):
            return "target_revoked" if t["status"] == "revoked" else "target_offline"
        if authority is None:
            return "target_authority_unverifiable"
        return authority(str(t["owner"] or ""), str(t["device"] or ""))
    return check


# ---------------------------------------------------------------------- the local relay
@dataclass(frozen=True)
class ProvedPeer:
    """The identity a slot's handshake proved, as the relay reports it (for key selection and the record)."""
    instance: str
    uri_text: str

    def uri(self) -> str:
        return self.uri_text


def _read_exact(sock, n: int) -> bytes:
    out = b""
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise ConnectionError("the other end closed")
        out += chunk
    return out


def _move_one_frame(src, dst) -> None:
    """Exactly one `[4-byte length][32-byte MAC][body]` frame, length bounded BEFORE it is read, bytes untouched."""
    head = _read_exact(src, 4)
    (length,) = struct.unpack(">I", head)
    if length > wire.MAX_FRAME:
        raise ValueError("a frame larger than the protocol allows")
    dst.sendall(head + _read_exact(src, 32 + length))


class RelayServer:
    """A unix socket for the control plane's other processes (probe, trust push, renewal): one JSON line
    {"instance", "budget", "wait"} -> {"ok": true, "instance", "uri"} | {"ok": false, "code"}, then exactly one
    frame each way. Only this account (or root) may connect; the socket is 0600."""

    def __init__(self, hub: ReverseHub, path: str, *, max_wait: float = 7200.0) -> None:
        self.hub = hub
        self.path = path
        self.max_wait = float(max_wait)
        self._socket = None
        self._stopped = threading.Event()

    def start(self) -> None:
        if os.path.lexists(self.path):
            os.unlink(self.path)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            s.bind(self.path)
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)
        s.listen(32)
        s.settimeout(1.0)
        self._socket = s
        threading.Thread(target=self._loop, name="reverse-relay", daemon=True).start()

    def stop(self) -> None:
        self._stopped.set()
        if self._socket is not None:
            _close(self._socket)

    def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                client, _ = self._socket.accept()
            except OSError:
                continue
            threading.Thread(target=self._one, args=(client,), daemon=True).start()

    def _one(self, client) -> None:
        slot = None
        try:
            creds = client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", creds)
            if uid not in (0, os.getuid()):
                return
            client.settimeout(30.0)
            line = b""
            while not line.endswith(b"\n") and len(line) < 4096:
                chunk = client.recv(1)
                if not chunk:
                    return
                line += chunk
            ask = json.loads(line.decode("utf-8"))
            wait = min(self.max_wait, max(1.0, float(ask.get("wait") or 60.0)))
            try:
                slot, who = self.hub.checkout(str(ask["instance"]), float(ask.get("budget") or 10.0))
            except SlotRefused as refused:
                client.sendall((json.dumps({"ok": False, "code": refused.code}) + "\n").encode("utf-8"))
                return
            client.sendall((json.dumps({"ok": True, "instance": str(who.instance), "uri": str(who.uri())})
                            + "\n").encode("utf-8"))
            client.settimeout(wait)
            slot.settimeout(wait)
            _move_one_frame(client, slot)
            _move_one_frame(slot, client)
        except Exception:                                     # noqa: BLE001 - the caller sees a closed socket
            pass
        finally:
            if slot is not None:
                _close(slot)
            _close(client)


class RelayClient:
    """The other end of `RelayServer`: `open(instance, budget, wait)` -> (socket, ProvedPeer)."""

    def __init__(self, path: str) -> None:
        self.path = path

    def checkout(self, instance: str, budget: float, *, wait: float = 60.0):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(max(1.0, float(budget)) + 5.0)
        try:
            s.connect(self.path)
            s.sendall((json.dumps({"instance": instance, "budget": float(budget), "wait": float(wait)})
                       + "\n").encode("utf-8"))
            line = b""
            while not line.endswith(b"\n") and len(line) < 4096:
                chunk = s.recv(1)
                if not chunk:
                    raise SlotRefused("target_offline", "the reverse hub closed the request")
                line += chunk
            said = json.loads(line.decode("utf-8"))
        except SlotRefused:
            _close(s)
            raise
        except (OSError, ValueError) as exc:
            _close(s)
            raise SlotRefused("target_offline", "the reverse hub cannot be reached: %s" % type(exc).__name__) from exc
        if not said.get("ok"):
            _close(s)
            raise SlotRefused(str(said.get("code") or "target_offline"))
        return s, ProvedPeer(str(said.get("instance") or ""), str(said.get("uri") or ""))


__all__ = ["DEFAULT_MAX_TOTAL", "FORBIDDEN_PORTS", "MAX_SLOT_AGE", "ProvedPeer", "RelayClient", "RelayServer",
           "ReverseHub", "SlotRefused", "device_authority", "eligibility", "listen_endpoint", "slots_for",
           "target_view"]
