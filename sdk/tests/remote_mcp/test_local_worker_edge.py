"""Local worker, slice 2: the public 443 path (Codex Q-LW2-0001 D1-D3, D2 as amended by Q-LW2-0003; profile
local-worker-slice2-r2 L3-L6, counter-checks 1-12).

Real deployment PKI, the real reverse hub in edge-only mode, the real `reverse_edge.EdgeServer`, the real dial-back
agent with the real `edge.EdgeDialer`, and between them a stand-in for nginx: a TLS terminator holding a certificate
for `mcp.agentnode.net` from a test "WebPKI" CA that passes the upgraded bytes through untouched -- exactly what
nginx's `location = /agent/v1/slot` does. What stands in behind the agent is the runtime (`AWorkerThatAnswers`)."""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import socket
import ssl
import threading
import time
from pathlib import Path

import pytest

from agentnode_sdk.remote_mcp import reverse_edge as RE
from agentnode_sdk.remote_mcp import reverse_hub as R
from agentnode_sdk.worker import REVERSE_SESSION, pairkeys
from agentnode_sdk.worker import dialback as D
from agentnode_sdk.worker import edge as E
from agentnode_sdk.worker.journal import Journal
from agentnode_sdk.worker.remote import from_address
from agentnode_sdk.worker.service import Bench
from agentnode_sdk.worker.tls import TlsListener
from tests.test_mtls_transport import World, _one_boot, a_job  # noqa: F401 - autouse: one boot throughout
from tests.test_socket_worker import AWorkerThatAnswers

INST, OTHER, GW = "c1a2b3c4d5e6", "c9f9f9f9f9f9", "g1"
HOST = "mcp.agentnode.net"
ALICE = "acct-00000000000000a1"


# ---------------------------------------------------------------------- a test "WebPKI" and a stand-in for nginx
def _webpki(tmp: Path, name: str = HOST):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = dt.datetime.now(dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test webpki root")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
          .not_valid_after(now + dt.timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    d = tmp / ("webpki-" + name)
    d.mkdir()
    (d / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (d / "cert.pem").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    (d / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption()))
    return d


class FakeNginx:
    """TLS for the public name, then the bytes of ONE connection passed to the edge's address untouched."""

    def __init__(self, certdir: Path, upstream: tuple):
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(str(certdir / "cert.pem"), str(certdir / "key.pem"))
        self.ctx.set_alpn_protocols(["http/1.1"])
        self.upstream = upstream
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(64)
        self.sock.settimeout(0.5)
        self.port = self.sock.getsockname()[1]
        self.stopped = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self.stopped.is_set():
            try:
                raw, _ = self.sock.accept()
            except OSError:
                continue
            threading.Thread(target=self._one, args=(raw,), daemon=True).start()

    def _one(self, raw):
        try:
            raw.settimeout(5)
            outer = self.ctx.wrap_socket(raw, server_side=True)
            up = socket.create_connection(self.upstream, 5)
        except OSError:
            raw.close()
            return
        E.pump(outer, up)

    def close(self):
        self.stopped.set()
        self.sock.close()


class EdgeStand:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.world = World(tmp / "pki")
        self.gw_dir = self.world.service("gateway", GW)
        self.wk_dir = self.world.service("worker", INST)
        self.ring = pairkeys.empty().add(gateway=GW, worker=INST, key=os.urandom(32))
        self.gw_tls = self.world.settings(self.gw_dir, {INST, OTHER}, role="gateway")
        self.wk_tls = self.world.settings(self.wk_dir, {GW}, role="worker", floor=self.world.floor_for("worker", INST))
        self.view = {INST: {"target_id": "cust-local1", "status": "active", "concurrency": 1, "owner": ALICE,
                            "device": "dev-a"}}
        self.code = {"now": ""}
        self.said: list[str] = []
        self.hub = R.ReverseHub(None, self.gw_tls, targets=lambda: self.view, eligible=lambda tid: self.code["now"],
                                say=self.said.append)
        self.hub.start()
        self.edge = RE.EdgeServer(self.hub, host=HOST, tcp_loopback=("127.0.0.1", 0), say=self.said.append)
        self.edge.start()
        self.webpki = _webpki(tmp)
        self.nginx = FakeNginx(self.webpki, self.edge.tcp_loopback)
        self.agents = []

    def context(self, cafile=None):
        ctx = E.outer_context()
        ctx.load_verify_locations(str(cafile or (self.webpki / "ca.pem")))
        return ctx

    def connect(self, port=None):
        return lambda _addr, timeout: socket.create_connection(("127.0.0.1", port or self.nginx.port), timeout)

    def material(self, tls=None):
        from agentnode_sdk.worker.service import _edge_material
        return _edge_material(tls or self.wk_tls)

    def dialer(self, *, tls=None, context=None, port=None):
        return E.EdgeDialer(E.control_url("https://" + HOST), self.material(tls), context=context or self.context(),
                            connect=self.connect(port), say=self.said.append)

    def agent(self, *, tls=None, dialer=None, max_runs=1, stub=None):
        stub = stub or AWorkerThatAnswers()
        n = len(self.agents)
        bench = Bench(stub, "", None, only_uid=None, remembers_at=str(self.tmp / ("floor-%d.json" % n)))
        bench.label = INST
        bench.tls = tls or self.wk_tls
        bench.max_runs = max_runs
        bench.features = (D.FEATURE,)
        bench.journal = Journal(str(self.tmp / ("journal-%d" % n)))
        listener = TlsListener(bench, "", tls or self.wk_tls, say=self.said.append, topology=REVERSE_SESSION)
        listener.use_keyring(self.ring, INST)
        agent = D.DialBackAgent(listener, "https://" + HOST, slots=D.slots_for(max_runs), say=self.said.append,
                                dial=dialer or self.dialer(tls=tls))
        agent.stub = stub
        agent.start()
        self.agents.append(agent)
        return agent

    def control(self):
        return from_address("reverse://" + INST, None, topology=REVERSE_SESSION, keyring=self.ring, own_instance=GW,
                            hub=self.hub, may_run=lambda: self.code["now"])

    def wait_parked(self, n, seconds=10.0, instance=INST):
        end = time.time() + seconds
        while time.time() < end and self.hub.parked(instance) < n:
            time.sleep(0.05)
        return self.hub.parked(instance)

    def close(self):
        for a in self.agents:
            a.stop()
        self.nginx.close()
        self.edge.stop()
        self.hub.stop()


@pytest.fixture()
def edge(tmp_path):
    s = EdgeStand(tmp_path)
    yield s
    s.close()


def _raw_request(stand: EdgeStand, head: bytes, *, read=True) -> tuple[int, str]:
    """One request straight to the edge (as nginx would pass it); (status, error code)."""
    s = socket.create_connection(stand.edge.tcp_loopback, 5)
    try:
        s.sendall(head)
        if not read:
            return 0, ""
        data = b""
        s.settimeout(10)
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\r\n\r\n" in data and (b"101" in data.split(b"\r\n", 1)[0] or data.endswith(b"}")):
                break
        status = int(data.split(b" ", 2)[1])
        body = data.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in data else b""
        return status, (json.loads(body).get("error", "") if body.strip() else "")
    finally:
        s.close()


def _proof_headers(stand: EdgeStand, *, tls=None, now=None, nonce=None, instance=None, host=HOST) -> dict:
    key, der, inst = stand.material(tls)()
    return E.make_proof(key, der, instance or inst, host, now=now, nonce=nonce)


def _head(headers: dict, *, line=b"GET /agent/v1/slot HTTP/1.1", extra=b"") -> bytes:
    base = {"Host": HOST, "Connection": "Upgrade", "Upgrade": E.UPGRADE}
    base.update(headers)
    lines = [line] + [("%s: %s" % (k, v)).encode("ascii") for k, v in base.items() if v is not None]
    return b"\r\n".join(lines) + b"\r\n" + extra + b"\r\n"


# ---------------------------------------------------------------------- L3/L6: outbound only, end to end
def test_a_run_crosses_the_443_path_end_to_end(edge):
    """L3/L6: the agent opens every connection, through the public name's TLS and the upgrade; a run's sealed frames
    pass the edge and the stand-in nginx untouched; the result is the worker's."""
    agent = edge.agent()
    assert edge.wait_parked(D.slots_for(1)) == D.slots_for(1)
    assert agent.listener._socket is None
    out = edge.control().run(a_job("edge-1"))
    assert out.stdout == "RAN"
    assert edge.edge.refused == {}


def test_nginx_and_the_hub_see_only_inner_ciphertext(edge, monkeypatch):
    """L6 / CC12: what passes the edge after 101 is TLS records, never a frame in the clear -- the job's text never
    appears in the bytes the stand-in nginx moves."""
    seen = []
    real = E.pump

    def watching(outer, inner, **kw):
        class Tap:
            def __init__(self, s):
                self.s = s

            def recv(self, n):
                b = self.s.recv(n)
                seen.append(b)
                return b

            def __getattr__(self, name):
                return getattr(self.s, name)
        return real(Tap(outer), inner, **kw)
    monkeypatch.setattr(E, "pump", watching)
    edge.agent()
    edge.wait_parked(D.slots_for(1))
    job = a_job("edge-cipher")
    edge.control().run(job)
    blob = b"".join(seen)
    assert job.run_id.encode() not in blob and b"agentnode-worker/1" not in blob


# ---------------------------------------------------------------------- L4: the outer TLS and the authority
@pytest.mark.parametrize("address", [
    "http://mcp.agentnode.net", "https://mcp.agentnode.net:8443", "https://user@mcp.agentnode.net",
    "https://mcp.agentnode.net/agent/v1/other", "https://mcp.agentnode.net/?q=1", "https://mcp.agentnode.net/#f",
    "https://2.28.194.41", "https://[::1]", "https://localhost", "https://MCP.agentnode.net", "https://mcp",
    "https://xn--mcp-9oa.agentnode.net", "https://mcp.agentnode.net.", "https://*.agentnode.net", "tcps://10.0.0.4:9447"])
def test_the_control_url_is_exactly_a_public_name(address):
    """L4 / CC3: redirects, literal IPs, other ports, paths, queries, fragments, userinfo, IDN and case games refused."""
    with pytest.raises(E.EdgeRefused) as got:
        E.control_url(address)
    assert got.value.code == "control_address_unusable"
    assert E.control_url("https://mcp.agentnode.net").text == "https://mcp.agentnode.net/agent/v1/slot"
    assert E.control_url("https://mcp.agentnode.net:443/agent/v1/slot").text == "https://mcp.agentnode.net/agent/v1/slot"


def test_an_untrusted_outer_certificate_is_control_tls_untrusted(edge, tmp_path):
    """L4 / CC2: a certificate from a CA the OS does not trust (an interception proxy) is refused by name."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    other = _webpki(elsewhere)                                 # the same name, from a CA the client does not trust
    with pytest.raises(E.EdgeRefused) as got:
        edge.dialer(context=edge.context(cafile=other / "ca.pem"))(None, 5)
    assert got.value.code == "control_tls_untrusted"


def test_a_certificate_for_another_name_is_control_tls_untrusted(edge, tmp_path):
    """L4 / CC2: SNI and hostname are checked: a trusted certificate for a different name is refused."""
    wrong = _webpki(tmp_path, name="evil.example")
    nginx = FakeNginx(wrong, edge.edge.tcp_loopback)
    try:
        with pytest.raises(E.EdgeRefused) as got:
            edge.dialer(context=edge.context(cafile=wrong / "ca.pem"), port=nginx.port)(None, 5)
        assert got.value.code == "control_tls_untrusted"
    finally:
        nginx.close()


def test_a_configured_proxy_is_refused_by_name():
    """D1: a proxy the OS was told to use is not dialled around."""
    with pytest.raises(E.EdgeRefused) as got:
        E.refuse_configured_proxy({"HTTPS_PROXY": "http://proxy:3128"})
    assert got.value.code == "proxy_unsupported"
    E.refuse_configured_proxy({})


def test_the_outer_context_is_strict():
    ctx = E.outer_context()
    assert ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2


# ---------------------------------------------------------------------- L5: bounded edge admission
def test_a_good_proof_upgrades(edge):
    status, _ = _raw_request(edge, _head(_proof_headers(edge)))
    assert status == 101


@pytest.mark.parametrize("line", [b"POST /agent/v1/slot HTTP/1.1", b"GET /agent/v1/slot/ HTTP/1.1",
                                  b"GET /agent/v1/slot HTTP/1.0", b"GET /mcp HTTP/1.1"])
def test_another_request_line_is_a_bad_request(edge, line):
    assert _raw_request(edge, _head(_proof_headers(edge), line=line)) == (400, "edge_bad_request")


@pytest.mark.parametrize("host", ["MCP.agentnode.net", "mcp.agentnode.net:443", "evil.example", "127.0.0.1", ""])
def test_the_host_is_exactly_the_public_name(edge, host):
    """L5 / CC1: the ORIGINAL authority, not a normalised one."""
    assert _raw_request(edge, _head(dict(_proof_headers(edge), Host=host))) == (421, "edge_host_refused")


def test_any_origin_is_refused(edge):
    """L5 / CC4."""
    got = _raw_request(edge, _head(dict(_proof_headers(edge), Origin="https://mcp.agentnode.net")))
    assert got == (403, "edge_origin_refused")


@pytest.mark.parametrize("name,value", [("Cookie", "__Host-agentnode=x"), ("Authorization", "Bearer x"),
                                        ("Sec-WebSocket-Key", "dGhlIHNhbXBsZSBub25jZQ=="),
                                        ("Content-Length", "5"), ("Transfer-Encoding", "chunked"),
                                        ("User-Agent", "curl/8")])
def test_browser_headers_bodies_and_strangers_are_bad_requests(edge, name, value):
    """L5 / CC4."""
    assert _raw_request(edge, _head(dict(_proof_headers(edge), **{name: value}))) == (400, "edge_bad_request")


def test_a_body_is_a_bad_request(edge):
    assert _raw_request(edge, _head(_proof_headers(edge)) + b"x") == (400, "edge_bad_request")


def test_the_header_block_is_bounded(edge):
    """L5 / CC5: more than 8 KiB of header is refused."""
    big = _head(dict(_proof_headers(edge), Upgrade=E.UPGRADE + " " * 9000))
    assert _raw_request(edge, big) == (431, "edge_header_too_large")


def test_a_slow_header_times_out(edge, monkeypatch):
    """L5 / CC5: the header block must arrive within the deadline."""
    edge.edge.header_seconds = 0.5
    s = socket.create_connection(edge.edge.tcp_loopback, 5)
    s.sendall(b"GET /agent/v1/slot HTTP/1.1\r\nHost: ")
    s.settimeout(5)
    data = s.recv(4096)
    s.close()
    assert b" 408 " in data.split(b"\r\n", 1)[0] and b"edge_header_timeout" in data


def test_a_forged_signature_is_edge_proof_invalid(edge):
    """L5 / CC6."""
    h = _proof_headers(edge)
    parts = h[E.PROOF_HEADER].split(".")
    sig = bytearray(base64.urlsafe_b64decode(parts[4] + "=" * (-len(parts[4]) % 4)))
    sig[-1] ^= 1
    parts[4] = base64.urlsafe_b64encode(bytes(sig)).rstrip(b"=").decode()
    h[E.PROOF_HEADER] = ".".join(parts)
    assert _raw_request(edge, _head(h)) == (401, "edge_proof_invalid")


def test_a_proof_by_another_key_for_this_instance_is_invalid(edge):
    """L5 / CC6: the proof must verify under the PRESENTED certificate's key."""
    h = _proof_headers(edge)
    forged = edge.world.forge("same-instance", uri="agentnode://alpha/worker/%s" % INST, usages=["1.3.6.1.5.5.7.3.1"])
    other = _proof_headers(edge, tls=edge.world.settings(forged, {GW}, role="worker"))
    h[E.PROOF_HEADER] = other[E.PROOF_HEADER]
    assert _raw_request(edge, _head(h)) == (401, "edge_proof_invalid")


def test_a_certificate_of_another_ca_is_edge_proof_invalid(edge):
    ca_key, ca_cert, _ = edge.world.foreign_ca()
    forged = edge.world.forge("foreign", uri="agentnode://alpha/worker/%s" % INST, usages=["1.3.6.1.5.5.7.3.1"],
                              ca=(ca_key, ca_cert))
    h = _proof_headers(edge, tls=edge.world.settings(forged, {GW}, role="worker"))
    assert _raw_request(edge, _head(h)) == (401, "edge_proof_invalid")


@pytest.mark.parametrize("skew", [-61, 61, 3600])
def test_a_proof_outside_sixty_seconds_is_stale(edge, skew):
    """L5 / CC8."""
    assert _raw_request(edge, _head(_proof_headers(edge, now=time.time() + skew))) == (401, "edge_proof_stale")


def test_a_reused_nonce_is_replayed(edge):
    """L5 / CC7: the same signed proof twice within 120 s."""
    h = _proof_headers(edge)
    assert _raw_request(edge, _head(h))[0] == 101
    assert _raw_request(edge, _head(h)) == (401, "edge_proof_replayed")


def test_the_replay_memory_is_bounded_and_refuses_when_full():
    """L5: finite memory; full is a refusal, never forgetting early."""
    clock = [1000.0]
    mem = RE.NonceMemory(seconds=120, limit=2, clock=lambda: clock[0])
    assert mem.first_time("a", "n1") and mem.first_time("a", "n2")
    with pytest.raises(RE.EdgeRefusal) as got:
        mem.first_time("a", "n3")
    assert got.value.code == "edge_slot_bound"
    assert not mem.first_time("a", "n1")
    clock[0] += 121
    assert mem.first_time("a", "n3")


def test_an_instance_that_is_no_parkable_target_is_refused(edge):
    """L5: a valid worker of this deployment that is not a dial-back target here."""
    other = edge.world.service("worker", OTHER)
    h = _proof_headers(edge, tls=edge.world.settings(other, {GW}, role="worker",
                                                    floor=edge.world.floor_for("worker", OTHER)))
    assert _raw_request(edge, _head(h)) == (403, "edge_not_parkable")
    edge.view[INST]["status"] = "suspended"
    assert _raw_request(edge, _head(_proof_headers(edge))) == (403, "edge_not_parkable")


@pytest.mark.parametrize("code,expected", [("target_device_revoked", "edge_not_parkable"),
                                           ("target_account_suspended", "edge_not_parkable"),
                                           ("target_authority_unverifiable", "edge_authority_unavailable")])
def test_eligibility_is_read_afresh(edge, code, expected):
    """L5 / CC10: an ineligible instance parks nothing; unreadable authority is its own refusal."""
    edge.code["now"] = code
    status, got = _raw_request(edge, _head(_proof_headers(edge)))
    assert got == expected


def test_an_unreadable_register_is_authority_unavailable(edge):
    def broken():
        raise OSError("gone")
    edge.hub._targets = broken
    assert _raw_request(edge, _head(_proof_headers(edge))) == (503, "edge_authority_unavailable")


def test_slots_are_bounded_per_identity_including_pending(edge):
    """L5 / CC11: at most 2P parked-or-pending per identity, then edge_slot_bound."""
    p2 = 2 * R.slots_for(1)
    for _ in range(p2):
        assert edge.hub.reserve(INST, 1)
    assert _raw_request(edge, _head(_proof_headers(edge))) == (429, "edge_slot_bound")
    for _ in range(p2):
        edge.hub._unreserve(INST)
    assert _raw_request(edge, _head(_proof_headers(edge)))[0] == 101


def test_the_hub_total_bounds_pending_too(edge):
    edge.hub.max_total = 1
    assert edge.hub.reserve(INST, 1)
    assert _raw_request(edge, _head(_proof_headers(edge))) == (429, "edge_slot_bound")


def test_the_inner_certificate_must_carry_the_proofs_key(edge):
    """L5/L6 / CC9: a proof by the real worker, an inner handshake by a different key for the same instance: nothing
    is parked (edge_identity_mismatch)."""
    forged = edge.world.forge("same-instance", uri="agentnode://alpha/worker/%s" % INST, usages=["1.3.6.1.5.5.7.3.1"])
    impostor = edge.world.settings(forged, {GW}, role="worker", floor=edge.world.floor_for("worker", INST))
    real = edge.material()
    dialer = E.EdgeDialer(E.control_url("https://" + HOST), real, context=edge.context(), connect=edge.connect())
    edge.agent(tls=impostor, dialer=dialer)
    time.sleep(2.0)
    assert edge.hub.parked(INST) == 0
    assert edge.hub.refused["edge_identity_mismatch"] >= 1


def test_refusal_codes_reach_the_agent_by_name(edge):
    """The agent learns WHY (and backs off): a refused upgrade is an EdgeRefused with the edge's code."""
    edge.code["now"] = "target_device_revoked"
    with pytest.raises(E.EdgeRefused) as got:
        edge.dialer()(None, 5)
    assert got.value.code == "edge_not_parkable"


@pytest.mark.parametrize("bad", ["v2.%s.1.n.s" % INST, "v1.UPPER.1.%s.c2ln" % ("A" * 43), "v1.%s.01.%s.c2ln" % (INST, "A" * 43),
                                 "v1.%s.1.short.c2ln" % INST, "v1.%s.1.%s.c2ln=" % (INST, "A" * 43), "", "v1"])
def test_the_proof_grammar_is_closed(bad):
    with pytest.raises(ValueError):
        E.parse_proof(bad)


def test_the_hub_without_an_address_binds_nothing(edge):
    """An edge-only hub has no TCP listener of its own."""
    assert edge.hub.host is None and edge.hub._socket is None


def test_a_relay_waits_as_long_as_the_request_may_take():
    """slice 2 (measured on the stand): through rmcp-agent's relay a run's answer must be awaited for the run's own
    wall clock plus margin -- the relay's 60-s default made a 90-s job `dispatch_indeterminate` while it finished."""
    from agentnode_sdk.worker import WorkerUnreachable
    from agentnode_sdk.worker.remote import ReverseWorker

    asked = []

    class Relay(R.RelayClient):
        def checkout(self, instance, budget, *, wait=60.0):
            asked.append(wait)
            raise R.SlotRefused("target_offline")
    w = ReverseWorker("reverse://" + INST, Relay("/nonexistent"), instance=INST)
    for seconds in (5.0, 200.0):
        with pytest.raises(WorkerUnreachable):
            w._ask("describe", {}, wait=seconds)
    assert asked == [20.0, 215.0]


def test_a_revoked_worker_certificate_is_not_parkable(edge):
    """Codex negative list: a revoked instance is `edge_not_parkable` (measured on the stand after a device revoke:
    it was answered `edge_proof_invalid`)."""
    from cryptography import x509
    serial = x509.load_pem_x509_certificate((edge.wk_dir / "cert.pem").read_bytes()).serial_number
    edge.world.issuer.revoke(format(serial, "x"))
    edge.world.tick()
    time.sleep(0.5)
    assert _raw_request(edge, _head(_proof_headers(edge))) == (403, "edge_not_parkable")


def test_an_expired_worker_certificate_is_refused_at_the_edge(edge):
    """README section 11: a paired worker's certificate is not renewed in this beta; at expiry the edge refuses it
    (fail-closed) -- a certificate of this deployment's CA for the target's own instance, but outside its validity."""
    forged = edge.world.forge("expired", uri="agentnode://alpha/worker/%s" % INST, usages=["1.3.6.1.5.5.7.3.1"],
                              not_after_days=-1, not_before_days=-30)
    h = _proof_headers(edge, tls=edge.world.settings(forged, {GW}, role="worker"))
    assert _raw_request(edge, _head(h)) == (401, "edge_proof_invalid")
