"""mtls-loopback-identity-r1, decision stage 2: the second transport, each direction on its own.

Everything here is a real TLS connection over loopback between real certificates from a real
issuer. Nothing is mocked below the socket. What IS a stand-in is the worker behind the door --
`AWorkerThatAnswers`, which records jobs instead of running containers -- because the question is
who gets through the door, not what a container does.

Each directional case holds the MAC key. That is the point of it: a caller without the key would
be refused by the message layer whatever TLS did, and a test that passes for that reason proves
nothing about TLS. With the key in hand, the certificate check is the ONLY thing that can stop it.
The observation is made where the bytes would have arrived -- at the worker for the worker's
check, at the impostor for the gateway's.

The builders at the top (`World`, `Door`) are shared with the other `test_mtls_*` files. From
stage 5 a `World` also has what every TLS side must judge by: the signed revocation list its issuer
published at setup, and a floor directory set up and written by the root run (`Issuer.tick`) --
so every side here is valid and fresh unless a test makes one thing wrong on purpose.
"""
from __future__ import annotations

import datetime as dt
import json
import socket
import ssl
import threading
import time
from pathlib import Path

import pytest

from agentnode_sdk.pki import floor as floors
from agentnode_sdk.pki import identity as ids
from agentnode_sdk.pki.issuer import Issuer, make_request
from agentnode_sdk.worker import Job, Limits, WorkerUnreachable
from agentnode_sdk.worker import protocol as wire
from agentnode_sdk.worker.remote import SocketWorker, TlsWorker, from_address
from agentnode_sdk.worker.service import Bench
from agentnode_sdk.worker.tls import (NotLoopback, TlsListener, TlsSettings, client_context,
                                      endpoint, server_context)
from tests.test_socket_worker import AWorkerThatAnswers

DEPLOYMENT = "alpha"
KEY = b"m" * 32

#: One boot for the whole test process. The floor is judged against the kernel's boot identity,
#: which Windows does not have; on Linux the real one would do, and this makes both the same.
TEST_BOOT = "test-boot"

#: Short intervals, so a re-evaluation happens within a test. Their sum is what is promised.
TEST_RELOAD_SECONDS = 0.2
TEST_REEVALUATE_SECONDS = 0.1


@pytest.fixture(autouse=True)
def _one_boot(monkeypatch):
    """Every test here runs in one boot. A test about another boot says so itself."""
    monkeypatch.setattr(floors, "_boot", lambda: TEST_BOOT)


# ====================================================================== the shared builders

class World:
    """An issuer in a temporary directory, and the services it has issued to."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.issuer = Issuer(self.root / "ca", self.root / "trust")
        self.issuer.initialise(DEPLOYMENT)
        self.anchor = self.root / "trust" / "ca.pem"
        self.revocation_list = self.root / "trust" / "revoked.crl"
        self.floor_dir = self.root / "floor"
        self.issuer.floor_init(self.floor_dir)
        self.tick()

    def tick(self) -> dict:
        """The root run, once: reconcile the list, write both floors in this boot."""
        return self.issuer.tick(self.floor_dir)

    def service(self, role: str, instance: str) -> Path:
        """A service's TLS directory, enrolled through the issuer exactly as a deployment does."""
        folder = self.root / (role + "-" + instance)
        folder.mkdir()
        self.issuer.add(role, instance, secret_at=folder / "secret",
                        deliver_to=folder / "cert.pem")
        request = json.loads(make_request(folder, (folder / "secret").read_text()).read_text())
        self.issuer.enroll(request["csr"].encode("ascii"), request["secret"])
        return folder

    def settings(self, folder: Path, accept, *, anchor=None, deployment=DEPLOYMENT, role=None,
                 revocation_list=None, floor=None):
        """A side's settings. Its floor is its ROLE's: a worker's door reads worker.floor, a
        gateway's client gateway.floor. The role follows the folder a service was enrolled into
        unless the caller names it -- a forged or swapped folder has to."""
        if role is None:
            role = "worker" if Path(folder).name.startswith("worker-") else "gateway"
        return TlsSettings(certificate=str(folder / "cert.pem"), key=str(folder / "key.pem"),
                           anchor=str(anchor or self.anchor), deployment=deployment,
                           accept=frozenset(accept),
                           revocation_list=str(revocation_list or self.revocation_list),
                           floor=str(floor or floors.path_for(self.floor_dir, role)),
                           reload_seconds=TEST_RELOAD_SECONDS,
                           reevaluate_seconds=TEST_REEVALUATE_SECONDS)

    def _ca(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        key = serialization.load_pem_private_key((self.root / "ca" / "ca.key").read_bytes(), None)
        return key, x509.load_pem_x509_certificate(self.anchor.read_bytes())

    def foreign_ca(self, name: str = "elsewhere"):
        """Another CA altogether -- not this deployment's. Returns (key, cert, anchor path)."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "foreign " + name)])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - dt.timedelta(days=1))
                .not_valid_after(now + dt.timedelta(days=30))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(x509.KeyUsage(False, False, False, False, False, True, True,
                                             False, False), critical=True)
                .sign(key, hashes.SHA256()))
        path = self.root / (name + "-ca.pem")
        path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return key, cert, path

    def forge(self, name: str, *, uri: str, usages, ca=None, not_after_days: float = 30,
              not_before_days: float = -1) -> Path:
        """A certificate made DIRECTLY with a CA key, bypassing the issuer's rules -- so that a
        fixture can be exactly wrong in one way, and only the check under test can reject it.
        `usages` None means no extended-key-usage extension at all."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID, ObjectIdentifier

        ca_key, ca_cert = ca or self._ca()
        key = ec.generate_private_key(ec.SECP256R1())
        now = dt.datetime.now(dt.timezone.utc)
        builder = (x509.CertificateBuilder()
                   .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
                   .issuer_name(ca_cert.subject).public_key(key.public_key())
                   .serial_number(x509.random_serial_number())
                   .not_valid_before(now + dt.timedelta(days=not_before_days))
                   .not_valid_after(now + dt.timedelta(days=not_after_days))
                   .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                                  critical=True)
                   .add_extension(x509.SubjectAlternativeName(
                       [x509.UniformResourceIdentifier(uri)]), critical=False))
        if usages is not None:
            builder = builder.add_extension(
                x509.ExtendedKeyUsage([ObjectIdentifier(u) for u in usages]), critical=False)
        certificate = builder.sign(ca_key, hashes.SHA256())
        folder = self.root / ("forged-" + name)
        folder.mkdir()
        (folder / "cert.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        (folder / "key.pem").write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
        return folder


class _CountingConnection:
    """The worker's side of one connection, counting every application byte it reads.

    The message layer reads a frame through `connection.makefile("rb")` and nothing else, so the
    count is exactly the number of bytes that reached the worker's code from that peer.
    """

    def __init__(self, connection, door) -> None:
        self._connection = connection
        self._door = door

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def makefile(self, mode="rb"):
        stream = self._connection.makefile(mode)
        door = self._door

        class Reader:
            def read(self, n=-1):
                data = stream.read(n)
                door.bytes_in += len(data or b"")
                return data

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                stream.close()

        return Reader()


class Door:
    """A real worker's TLS listener on 127.0.0.1, with a stand-in behind it.

    `bytes_in` is the number of application bytes the worker READ, over every connection. Zero
    means nothing any peer sent ever reached the worker's code -- the decision's "null
    Anwendungsbytes". `conversations` counts connections the door handed to the message layer,
    which is a weaker thing: a door with its check removed hands over a connection the other
    side has already abandoned, which reads zero bytes.
    """

    def __init__(self, world: World, worker_dir: Path, accept, *, stub=None, label=None,
                 anchor=None, settings=None) -> None:
        self.stub = stub or AWorkerThatAnswers()
        self.bench = Bench(self.stub, "unix:///nowhere.sock", KEY, only_uid=None)
        self.bench.label = label
        self.conversations = 0
        self.bytes_in = 0
        through = self.bench.converse

        def counted(connection, noted=None):
            self.conversations += 1
            return through(_CountingConnection(connection, self), noted=noted)

        self.bench.converse = counted
        self.said: list[str] = []
        self.listener = TlsListener(self.bench, "tcps://127.0.0.1:0",
                                    settings or world.settings(worker_dir, accept, anchor=anchor,
                                                               role="worker"),
                                    say=self.said.append)
        _, port = self.listener.open()
        self.address = "tcps://127.0.0.1:%d" % port
        self.port = port
        threading.Thread(target=self.listener.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.listener.stop_serving()


def a_job(run_id: str = "run-1") -> Job:
    return Job(run_id=run_id, container_name="agentnode-run-" + run_id, command=("true",),
               artifact=b"", stdin="", network="none", allowed_domains=(),
               limits=Limits(cpu=1.0, memory_mb=256, processes=64, wall_clock_s=5,
                             storage_mb=0))


def a_raw_tls_client(world: World, port: int, *, folder: Path | None):
    """A client that holds the MAC key and trusts the anchor, with or without a certificate of
    its own. Returns the TLS socket, or raises what the handshake raised."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cafile=str(world.anchor))
    if folder is not None:
        context.load_cert_chain(str(folder / "cert.pem"), str(folder / "key.pem"))
    raw = socket.create_connection(("127.0.0.1", port), timeout=5)
    return context.wrap_socket(raw, server_side=False)


def job_params(run_id: str) -> dict:
    """A run's parameters exactly as `SocketWorker.run` sends them."""
    job = a_job(run_id)
    params = dict(job.as_message())
    params["artifact"] = wire.as_text(job.artifact)
    return params


def send_a_sealed_run(connection, key: bytes = KEY, body=None) -> bytes:
    body = body or wire.request("run", {"job": job_params("sneaked")},
                                deadline=time.time() + 30)
    try:
        connection.sendall(wire.seal(body, key))
        connection.settimeout(3)
        return connection.recv(65536)
    except (OSError, ssl.SSLError):
        return b""


def settle() -> None:
    """Let the worker's per-connection thread finish what it was doing."""
    time.sleep(0.3)


@pytest.fixture()
def world(tmp_path):
    return World(tmp_path)


@pytest.fixture()
def pair(world):
    """A gateway g1 and a worker w1 that accept each other, with the worker's door open."""
    gateway = world.service("gateway", "g1")
    worker = world.service("worker", "w1")
    door = Door(world, worker, {"g1"}, label="w1")
    try:
        yield world, gateway, worker, door
    finally:
        door.close()


# ====================================================================== stage 2 (a)

class TestAJobCrossesTheNewTransport:

    def test_a_job_runs_over_mutual_tls(self, pair):
        world, gateway, _worker, door = pair
        client = TlsWorker(door.address, KEY, world.settings(gateway, {"w1"}))
        outcome = client.run(a_job("run-a"))
        assert outcome.stdout == "RAN"
        assert [j.run_id for j in door.stub.ran] == ["run-a"]
        assert client.who_ran("run-a") == ("mtls", "agentnode://alpha/worker/w1", "w1")

    def test_the_mac_still_decides_over_tls(self, pair):
        """A certified gateway with the WRONG MAC key is not served. The handshake passing
        gives no exception to the message layer."""
        world, gateway, _worker, door = pair
        connection = a_raw_tls_client(world, door.port, folder=gateway)
        send_a_sealed_run(connection, key=b"x" * 32)
        connection.close()
        settle()
        assert door.conversations == 1, "it got past the door, as it should with a certificate"
        assert door.stub.ran == [], "and the MAC still refused it"

    def test_a_replayed_message_is_refused_over_tls(self, pair):
        world, gateway, _worker, door = pair
        body = wire.request("run", {"job": job_params("once")}, deadline=time.time() + 30)
        for _ in range(2):
            connection = a_raw_tls_client(world, door.port, folder=gateway)
            send_a_sealed_run(connection, body=body)
            connection.close()
            settle()
        assert [j.run_id for j in door.stub.ran] == ["once"], "the second copy was a replay"


# ====================================================================== stage 2 (b), (c)

class TestEachDirectionOnItsOwn:

    def test_a_gateway_without_a_certificate_reaches_nothing(self, pair):
        """(b) The caller has the MAC key and trusts the anchor, and presents no certificate.
        Only the worker's certificate requirement can stop it -- and it does, before a single
        application byte is read."""
        world, _gateway, _worker, door = pair
        try:
            connection = a_raw_tls_client(world, door.port, folder=None)
            send_a_sealed_run(connection)
            connection.close()
        except (ssl.SSLError, OSError):
            pass
        settle()
        assert door.bytes_in == 0, "an application byte from it reached the worker"
        assert door.stub.ran == []
        assert any("handshake" in s for s in door.said), door.said

    def test_an_impostor_worker_is_never_sent_the_job(self, world):
        """(c) The impostor holds the MAC key and a certificate REGULARLY issued by this
        deployment -- for instance w2. The gateway accepts only w1. Observed at the impostor."""
        gateway = world.service("gateway", "g1")
        impostor_dir = world.service("worker", "w2")
        impostor = Door(world, impostor_dir, {"g1"}, label="w1")
        try:
            client = TlsWorker(impostor.address, KEY, world.settings(gateway, {"w1"}))
            with pytest.raises(WorkerUnreachable) as refused:
                client.run(a_job("never"))
            settle()
            assert "instance" in str(refused.value)
            assert impostor.bytes_in == 0, "the impostor was sent application bytes"
            assert impostor.stub.ran == []
        finally:
            impostor.close()


# ====================================================================== stage 2 (d)

class TestPlaintextIsNotAWayIn:

    def test_plaintext_on_the_tls_port_reaches_nothing(self, pair):
        """A caller with the MAC key speaking plain frames to the TLS port. The MAC would pass;
        the door does not let it get that far."""
        _world, _gateway, _worker, door = pair
        raw = socket.create_connection(("127.0.0.1", door.port), timeout=5)
        send_a_sealed_run(raw)
        raw.close()
        settle()
        assert door.bytes_in == 0, "plaintext bytes reached the worker's message layer"
        assert door.stub.ran == []


# ====================================================================== loopback, fallback, default

class TestLoopbackOnly:

    @pytest.mark.parametrize("address", [
        "tcps://10.0.0.5:8443", "tcps://0.0.0.0:8443", "tcps://192.168.1.2:8443",
        "tcps://localhost:8443", "tcps://worker.example:8443", "tcps://[::]:8443",
    ])
    def test_anything_but_a_literal_loopback_address_is_refused(self, address):
        with pytest.raises(NotLoopback):
            endpoint(address)

    @pytest.mark.parametrize("address", ["tcps://127.0.0.1:8443", "tcps://[::1]:8443"])
    def test_a_literal_loopback_address_is_accepted(self, address):
        host, port = endpoint(address)
        assert port == 8443

    def test_neither_end_can_be_pointed_elsewhere(self, pair):
        world, gateway, worker, _door = pair
        with pytest.raises(NotLoopback):
            TlsWorker("tcps://10.1.2.3:8443", KEY, world.settings(gateway, {"w1"}))
        bench = Bench(AWorkerThatAnswers(), "unix:///nowhere.sock", KEY, only_uid=None)
        listener = TlsListener(bench, "tcps://0.0.0.0:0",
                               world.settings(worker, {"g1"}, role="worker"))
        with pytest.raises(NotLoopback):
            listener.open()


class TestNoSilentFallback:

    def test_a_tls_address_without_settings_is_refused_not_rerouted(self):
        with pytest.raises(WorkerUnreachable) as refused:
            from_address("tcps://127.0.0.1:8443", KEY, tls=None)
        assert "refused" in str(refused.value)

    def test_a_refused_handshake_tries_nothing_else(self, pair, monkeypatch):
        """After the TLS door refuses, no unix socket is opened and no plaintext is tried."""
        world, gateway, _worker, door = pair
        opened = []
        real_socket = socket.socket

        def watching(family=socket.AF_INET, *args, **kwargs):
            opened.append(family)
            return real_socket(family, *args, **kwargs)

        client = TlsWorker(door.address, KEY, world.settings(gateway, {"not-w1"}))
        monkeypatch.setattr(socket, "socket", watching)
        with pytest.raises(WorkerUnreachable):
            client.run(a_job("refused"))
        assert getattr(socket, "AF_UNIX", object()) not in opened
        assert door.stub.ran == []

    def test_the_worker_refuses_half_a_tls_configuration(self):
        from agentnode_sdk.worker.service import serve

        with pytest.raises(ValueError):
            serve("unix:///nowhere.sock", "/nonexistent", 1000,
                  tls_address="tcps://127.0.0.1:8443", tls=None)


class TestTheSocketIsStillTheDefault:

    def test_a_unix_address_is_the_socket_even_with_tls_settings_present(self, pair):
        world, gateway, _worker, _door = pair
        worker = from_address("unix:///run/agentnode/worker.sock", KEY,
                              tls=world.settings(gateway, {"w1"}))
        assert type(worker) is SocketWorker
        assert worker.transport == "unix"

    def test_a_gateway_with_no_tls_section_has_no_tls_settings(self, tmp_path):
        from agentnode_sdk.gateway.identity import GatewayState
        from agentnode_sdk.gateway.server import GatewayService
        from tests.test_em3c_gateway import StandInBackend

        state = GatewayState(str(tmp_path / "gw"), version="test")
        service = GatewayService(state, backend=StandInBackend())
        try:
            assert "worker_tls" not in service.config
            assert service._worker_tls() is None
        finally:
            service.close()
            state.close()


class TestExactlyOneAnchor:

    def test_the_contexts_trust_one_certificate_and_no_default_store(self, pair):
        world, gateway, worker, _door = pair
        for context in (server_context(world.settings(worker, {"g1"})),
                        client_context(world.settings(gateway, {"w1"}))):
            assert context.cert_store_stats()["x509_ca"] == 1
            assert context.verify_mode == ssl.CERT_REQUIRED
            assert context.minimum_version == ssl.TLSVersion.TLSv1_3
