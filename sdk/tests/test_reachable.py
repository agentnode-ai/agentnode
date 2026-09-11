"""Reaching a gateway without a tunnel, and knowing which gateway was reached.

Everything the remote gateway established so far was reached through an ssh tunnel by one operator
who had a key. A sandbox people are meant to be able to use cannot be reached that way, and the
three routes this build already documents each start by requiring something: a private tunnel, a
domain name, or a certificate you already have.

What every client already does before it can send anything is pair, out of band, with something a
person handed over. So that something carries the certificate's digest, and the client pins it
before it sends the code. What authenticates the gateway is then what authorised the client.

The TLS here is real: a real certificate this build made, a real server serving it, and a real
handshake. What is not real is a network -- everything is on loopback, because what is being
established is which certificate a client will talk to, and that is the same question on any wire.
"""
from __future__ import annotations

import http.server
import json
import ssl
import threading
import time

import pytest

from agentnode_sdk.gateway import certificate as tls
from agentnode_sdk.gateway import client as gc
from agentnode_sdk.gateway.invitation import NotAnInvitation, read, write
from agentnode_sdk.gateway.pinning import PinnedConnection, WrongCertificate, opener_for


@pytest.fixture()
def a_certificate(tmp_path):
    cert, key, pin = tls.make(tmp_path / "one", "127.0.0.1")
    return cert, key, pin


@pytest.fixture()
def another_certificate(tmp_path):
    cert, key, pin = tls.make(tmp_path / "two", "127.0.0.1")
    return cert, key, pin


class ASmallServer:
    """Something that serves TLS and counts what reached it.

    It answers everything the same way, because what these tests are about is whether a request
    arrived at all -- not what came back.
    """

    def __init__(self, cert, key):
        self.reached: list[str] = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _answer(self):
                server.reached.append(self.path)
                body = json.dumps({"ok": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = _answer
            do_POST = _answer

        self.http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert), str(key))
        self.http.socket = context.wrap_socket(self.http.socket, server_side=True)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return "https://127.0.0.1:%d" % self.http.server_address[1]

    def stop(self):
        self.http.shutdown()
        self.thread.join(timeout=10)


# --------------------------------------------------------- the certificate is the gateway's own


class TestTheCertificateIsTheGatewaysOwn:

    def test_it_is_made_where_the_gateway_keeps_its_secrets(self, tmp_path):
        cert, key, _pin = tls.make(tmp_path, "sandbox.example")
        assert cert.parent == tmp_path and key.parent == tmp_path
        assert cert.name == tls.CERT_NAME and key.name == tls.KEY_NAME

    def test_the_key_is_readable_by_nobody_else(self, tmp_path):
        import os
        import sys

        _cert, key, _pin = tls.make(tmp_path, "sandbox.example")
        if sys.platform == "win32":
            pytest.skip("this platform's file modes are advisory; the Linux lane checks them")
        assert (os.stat(key).st_mode & 0o077) == 0, "somebody else can read this gateway's key"

    def test_the_pin_is_over_the_certificate_and_not_over_the_file(self, tmp_path):
        """PEM is a text wrapper. A pin that moved when a file was copied through the wrong tool
        would be a pin nobody could rely on."""
        cert, _key, pin = tls.make(tmp_path, "sandbox.example")
        text = cert.read_bytes()
        assert tls.fingerprint(text) == pin
        assert tls.fingerprint(text.replace(b"\n", b"\r\n")) == pin

    def test_a_key_that_is_not_its_key(self, tmp_path):
        cert, _key, _pin = tls.make(tmp_path / "a", "one.example")
        _other, key, _p = tls.make(tmp_path / "b", "two.example")
        assert tls.belongs_together(cert, key) is False

    def test_and_one_that_is(self, tmp_path):
        cert, key, _pin = tls.make(tmp_path, "one.example")
        assert tls.belongs_together(cert, key) is True

    def test_it_cannot_sign_another_certificate(self, tmp_path):
        """A client pinning it is pinning one key, not an authority that could issue more."""
        from cryptography import x509

        cert, _key, _pin = tls.make(tmp_path, "one.example")
        loaded = x509.load_pem_x509_certificate(cert.read_bytes())
        basic = loaded.extensions.get_extension_for_class(x509.BasicConstraints).value
        assert basic.ca is False

    def test_two_gateways_are_two_certificates(self, tmp_path):
        _c1, _k1, one = tls.make(tmp_path / "a", "same.example")
        _c2, _k2, two = tls.make(tmp_path / "b", "same.example")
        assert one != two


class TestNothingIsServedInTheClear:
    """A gateway a stranger could reach without a certificate does not start."""

    def test_binding_where_a_stranger_could_reach_it_needs_one(self):
        from agentnode_sdk.gateway.transport import InsecureTransportError, check_bind_address

        for where in ("0.0.0.0", "::", "116.203.32.193"):
            with pytest.raises(InsecureTransportError) as caught:
                check_bind_address(where, tls=None)
            assert "without encryption" in str(caught.value)

    def test_and_there_is_no_setting_that_permits_it(self):
        """A boundary with a documented way around it is a default. The variable that used to
        permit this is kept only so it can be shown to be inert."""
        import os

        from agentnode_sdk.gateway.transport import (
            LEGACY_PLAINTEXT_ENV,
            InsecureTransportError,
            TransportRules,
            check_bind_address,
        )

        was = os.environ.get(LEGACY_PLAINTEXT_ENV)
        os.environ[LEGACY_PLAINTEXT_ENV] = "1"
        try:
            with pytest.raises(InsecureTransportError):
                check_bind_address("0.0.0.0", tls=None)
        finally:
            if was is None:
                os.environ.pop(LEGACY_PLAINTEXT_ENV, None)
            else:
                os.environ[LEGACY_PLAINTEXT_ENV] = was
        # And the rules can only tighten: there is no field that would loosen this.
        assert not hasattr(TransportRules(), "allow_plain_anywhere")

    def test_a_certificate_that_will_not_load_stops_it_rather_than_serving(self, tmp_path):
        from agentnode_sdk.gateway.transport import InsecureTransportError, TlsFiles

        nothing = tmp_path / "not-a-certificate.pem"
        nothing.write_text("this is not a certificate", encoding="utf-8")
        with pytest.raises(InsecureTransportError) as caught:
            TlsFiles(certfile=str(nothing), keyfile=str(nothing)).context()
        assert "was not started" in str(caught.value)

    def test_a_gateway_that_made_one_serves_it_without_being_told_where_it_is(self, tmp_path):
        """A gateway with a certificate never serves in the clear by omission."""
        import types

        from agentnode_sdk.cli.gateway_commands import _tls_from

        tls.make(tmp_path, "127.0.0.1")
        found = _tls_from({}, types.SimpleNamespace(dir=str(tmp_path)))
        assert found is not None
        assert found.context() is not None


class TestTheKeyIsTheGatewaysAlone:
    """The mode goes on as the file is created, not after it exists."""

    def test_it_is_created_with_its_permissions_and_not_narrowed_afterwards(self, tmp_path,
                                                                           monkeypatch):
        from agentnode_sdk.gateway import certificate as module

        opened = []
        real_open = module.os.open

        def watching(path, flags, mode=0o777):
            opened.append((str(path), oct(mode)))
            return real_open(path, flags, mode)

        monkeypatch.setattr(module.os, "open", watching)
        tls.make(tmp_path, "one.example")
        keys = [mode for path, mode in opened if path.endswith(tls.KEY_NAME)]
        assert keys == [oct(0o600)], opened


# ------------------------------------------------------- the client knows which gateway it is


class TestTheClientKnowsWhichGatewayItReached:

    def test_it_talks_to_the_certificate_it_was_told_about(self, a_certificate):
        cert, key, pin = a_certificate
        server = ASmallServer(cert, key)
        try:
            status, _body = gc._get(server.url + "/v1/hello", pin=pin)
            assert status == 200
            assert server.reached == ["/v1/hello"]
        finally:
            server.stop()

    def test_and_to_nothing_else(self, a_certificate, another_certificate):
        """The request is refused, and NOTHING reaches the far side -- not the path, not a
        header, not a byte."""
        cert, key, _pin = a_certificate
        _other, _otherkey, other_pin = another_certificate
        server = ASmallServer(cert, key)
        try:
            with pytest.raises(gc.GatewayClientError) as caught:
                gc._get(server.url + "/v1/hello", pin=other_pin)
            assert "different certificate" in str(caught.value)
            assert server.reached == [], "something was sent to a gateway it did not recognise"
        finally:
            server.stop()

    def test_a_connection_with_nothing_to_expect_refuses_to_be_made(self, a_certificate):
        cert, key, _pin = a_certificate
        server = ASmallServer(cert, key)
        try:
            connection = PinnedConnection("127.0.0.1", server.http.server_address[1], pin="")
            with pytest.raises(WrongCertificate) as caught:
                connection.connect()
            assert "nothing to check" in str(caught.value)
        finally:
            server.stop()

    def test_the_check_happens_before_anything_is_written(self, a_certificate,
                                                          another_certificate):
        """Established rather than asserted: the connection is opened and then asked to send,
        and the refusal comes from opening it."""
        cert, key, _pin = a_certificate
        _o, _ok, other_pin = another_certificate
        server = ASmallServer(cert, key)
        try:
            connection = PinnedConnection("127.0.0.1", server.http.server_address[1],
                                          pin=other_pin)
            with pytest.raises(WrongCertificate):
                connection.connect()
            assert server.reached == []
        finally:
            server.stop()

    def test_the_permissive_context_cannot_be_had_without_the_check(self):
        """The context does not verify a chain, because the pin is the verification. One like
        that is only safe with the check attached, so the two are not separable."""
        import inspect

        from agentnode_sdk.gateway import pinning

        source = inspect.getsource(pinning)
        assert "CERT_NONE" in source
        # It is built inside the connection that performs the check, and there is no function
        # here that hands one out.
        assert source.count("ssl.SSLContext(") == 1
        assert "def context(" not in source
        made = inspect.getsource(pinning.PinnedConnection.__init__)
        assert "CERT_NONE" in made

    def test_a_pin_is_to_a_key_and_says_so(self):
        import inspect

        from agentnode_sdk.gateway import pinning

        said = inspect.getdoc(pinning) or ""
        assert "pin is to a KEY" in said
        assert "reason to open a port" in said


# ----------------------------------------------------------------------- what is handed over


class TestWhatIsHandedOver:

    def test_it_carries_where_the_code_and_what_to_expect(self):
        one = write("https://sandbox.example:8099", "ABCD-EFGH-IJKL", "a" * 64)
        assert read(one) == ("https://sandbox.example:8099", "ABCD-EFGH-IJKL", "a" * 64)

    def test_an_address_full_of_colons_survives(self):
        """An IPv6 literal. A reader that split on punctuation would work until somebody
        deployed it properly."""
        where = "https://[2a01:4f8:1c1a:47dd::1]:8099"
        assert read(write(where, "CODE", "b" * 64))[0] == where

    def test_one_with_nothing_to_expect_is_refused(self):
        """Not treated as "no pinning wanted": accepting it would make the check optional in
        exactly the situation where it matters."""
        import base64

        body = json.dumps({"where": "https://x", "code": "y"}).encode()
        without = "agentnode-invite-1." + base64.urlsafe_b64encode(body).decode().rstrip("=")
        with pytest.raises(NotAnInvitation) as caught:
            read(without)
        assert "which certificate to expect" in str(caught.value)

    def test_one_that_was_cut_short(self):
        one = write("https://sandbox.example:8099", "CODE", "c" * 64)
        with pytest.raises(NotAnInvitation) as caught:
            read(one[:40])
        assert "cut short" in str(caught.value)

    def test_something_that_is_not_one_at_all(self):
        with pytest.raises(NotAnInvitation) as caught:
            read("https://sandbox.example:8099")
        assert "does not look like an invitation" in str(caught.value)

    def test_a_certificate_that_is_not_a_digest(self):
        import base64

        body = json.dumps({"where": "https://x", "code": "y", "certificate": "nope"}).encode()
        odd = "agentnode-invite-1." + base64.urlsafe_b64encode(body).decode().rstrip("=")
        with pytest.raises(NotAnInvitation) as caught:
            read(odd)
        assert "not a sha256" in str(caught.value)

    def test_writing_one_without_a_certificate_is_refused_at_the_gateway_too(self):
        with pytest.raises(NotAnInvitation):
            write("https://sandbox.example:8099", "CODE", "")

    def test_what_it_refuses_never_repeats_what_it_was_given(self):
        """An error message is something people paste into issues."""
        secret = "d" * 64
        one = write("https://sandbox.example:8099", "THE-SECRET-CODE", secret)
        try:
            read(one[:45])
        except NotAnInvitation as exc:
            assert "THE-SECRET-CODE" not in str(exc)
            assert secret not in str(exc)


# ------------------------------------------------------------------- pairing without a shell


class TestPairingDoesNotNeedAShell:

    def test_what_the_operator_runs_and_what_the_person_runs(self):
        """Two commands, neither of which needs the other machine."""
        import inspect

        from agentnode_sdk.cli import gateway_commands, remote_commands

        issuing = inspect.getsource(gateway_commands.cmd_pair)
        assert "an_invitation(where, code, pin" in issuing
        taking = inspect.getsource(remote_commands.cmd_connect)
        assert "an_invitation(given)" in taking
        for shape in ("ssh", "scp", "tailscale"):
            assert shape not in taking, shape

    def test_the_code_is_still_single_use_and_still_expires(self):
        from agentnode_sdk.gateway.identity import PAIRING_TTL_SECONDS, new_pairing_code

        assert PAIRING_TTL_SECONDS <= 15 * 60
        assert len({new_pairing_code() for _ in range(200)}) == 200

    def test_an_invitation_is_not_written_into_anything(self):
        """It is the thing that would let somebody else pair as you."""
        import inspect

        from agentnode_sdk.cli import gateway_commands

        source = inspect.getsource(gateway_commands.cmd_pair)
        for shape in ("logging.", "logger", "open(", ".write("):
            assert shape not in source, shape


# --------------------------------------------------------------- what this does not establish


class TestItRunsAsAServiceUnderItsOwnAccount:
    """The units are the deployment. What they say is checkable without deploying them."""

    def units(self):
        from pathlib import Path

        here = Path(__file__).resolve().parent.parent / "deploy"
        return ((here / "agentnode-gateway.service").read_text(encoding="utf-8"),
                (here / "agentnode-worker.service").read_text(encoding="utf-8"))

    def test_the_control_plane_is_in_no_group_that_can_drive_a_runtime(self):
        gateway, _worker = self.units()
        groups = [line.split("=", 1)[1] for line in gateway.splitlines()
                  if line.startswith("SupplementaryGroups=")]
        for named in groups:
            assert "docker" not in named, named
            assert "podman" not in named, named
        assert "User=agentnode-gateway" in gateway
        assert "User=root" not in gateway

    def test_and_the_worker_is_the_one_that_can(self):
        _gateway, worker = self.units()
        assert "User=agentnode-worker" in worker

    def test_and_it_does_it_without_a_root_equivalent_group(self):
        """The worker used to be in the docker group. That was the wrong shape.

        Membership of the docker group is root on the host: anything in it can start a
        privileged container bind-mounting `/`. Giving it to the one account whose entire job is
        running foreign code means a sandbox escape owns the machine in a single step -- and the
        machine is where the control plane's signing identity and every client's token are.

        Rootless podman does the same work with no such group, so neither account has one.
        """
        gateway, worker = self.units()
        for unit, which in ((worker, "worker"), (gateway, "gateway")):
            for group in ("docker", "wheel", "sudo", "root"):
                assert f"SupplementaryGroups={group}" not in unit, (
                    f"the {which} unit puts its account in the {group} group")
                assert ("Group=" + group) not in unit.replace("agentnode-", "")

    def test_and_the_worker_keeps_its_own_primary_group(self):
        """Not the shared one, however tempting: rootless podman stops working outright.

        newuidmap refuses to map subordinate ids when the calling process's gid is not the one
        in the account's passwd entry -- "Target process is owned by a different user" -- and
        then no container starts. The shared group has to be supplementary, and the socket gets
        it from its directory's setgid bit instead.
        """
        _gateway, worker = self.units()
        assert "Group=agentnode-worker" in worker
        assert "SupplementaryGroups=agentnode-bridge" in worker

    def test_and_the_socket_directory_is_not_left_to_systemd(self):
        """RuntimeDirectory= would recreate it with the unit's own group, which is the wrong one."""
        _gateway, worker = self.units()
        # A SETTING, not the word: the comment above it in the unit explains why it is absent.
        settings = [l for l in worker.splitlines() if l and not l.startswith("#")]
        assert not [l for l in settings if l.startswith("RuntimeDirectory")], settings

    def test_neither_runs_as_a_person_who_has_to_be_logged_in(self):
        for unit in self.units():
            assert "Restart=always" in unit
            assert "WantedBy=multi-user.target" in unit

    def test_the_worker_cannot_reach_the_control_planes_directory(self):
        _gateway, worker = self.units()
        assert "InaccessiblePaths=-/var/lib/agentnode" in worker
        assert "ReadWritePaths=/run/agentnode" in worker

    def test_what_the_deployment_says_it_is_not(self):
        from pathlib import Path

        said = (Path(__file__).resolve().parent.parent / "deploy" / "README.md").read_text(
            encoding="utf-8")
        # The line wrapping in a document is not what is being established; the claims are.
        flowed = " ".join(said.split())
        for phrase in ("this is not isolation", "root-equivalent", "single-host-development",
                       "may be described as production-safe"):
            assert phrase in flowed, phrase


class TestWhatThisDoesNotEstablish:

    def test_the_limits_are_where_a_reader_will_meet_them(self):
        import inspect

        from agentnode_sdk.gateway import certificate, pinning

        for module, must_say in (
            (pinning, ("pin is to a KEY", "reason to open a port")),
            (certificate, ("not who owns it", "reason to open a port",
                           "pin to a KEY")),
        ):
            said = inspect.getdoc(module) or ""
            for phrase in must_say:
                assert phrase in said, (module.__name__, phrase)


class TestEveryRequestToAPairedGatewayIsPinned:
    """Pinning that some requests do and others do not is not pinning.

    Found by running the real client against a real gateway: `remote test` worked and `remote
    status` failed, because status asked `hello` without the certificate the client had pinned
    when it paired. Against a gateway with a self-signed certificate that request cannot
    succeed at all -- and its failure was reported as "that is not the sandbox you paired with",
    which describes an attack rather than a forgotten argument.
    """

    def _calls(self):
        import inspect

        from agentnode_sdk.cli import remote_commands

        return inspect.getsource(remote_commands)

    def test_no_command_asks_a_saved_gateway_without_its_certificate(self):
        import re

        # Every hello() against a SAVED connection, with what was passed to it.
        for call in re.findall(r"gc\.hello\([^)]*\)", self._calls()):
            if "saved" in call:
                assert "pin=" in call, f"this request is not pinned: {call}"

    def test_and_the_one_before_pairing_is_pinned_to_the_invitation(self):
        import re

        for call in re.findall(r"gc\.hello\([^)]*\)", self._calls()):
            assert "pin=" in call, f"an unpinned request to a gateway: {call}"


class TestAnAddressToAdvertiseIsCheckedWhereItIsTyped:
    """The failure used to happen on somebody else's machine, which is the worst place for it.

    `--advertise 127.0.0.1:8099` was accepted, put a colon in the certificate's name and in every
    invitation the gateway then issued, and the first complaint came from a client refusing to
    parse the address. The operator who made the mistake never saw it.
    """

    def _init(self, tmp_path, advertise):
        from agentnode_sdk.cli import gateway_commands

        class Args:
            dir = str(tmp_path)
            tls_self_signed = True
            tls_cert = tls_key = None

        Args.advertise = advertise
        return gateway_commands.cmd_init(Args())

    def test_an_address_with_a_port_is_refused_at_once(self, tmp_path, capsys):
        assert self._init(tmp_path, "127.0.0.1:8099") == 2
        said = capsys.readouterr().out
        assert "without a port" in said
        assert "--advertise 127.0.0.1" in said, "it did not show the corrected command"

    def test_and_it_says_where_the_port_goes_instead(self, tmp_path, capsys):
        self._init(tmp_path, "sandbox.example:9000")
        assert "--port 9000" in capsys.readouterr().out

    def test_a_plain_address_is_accepted(self, tmp_path):
        assert self._init(tmp_path, "127.0.0.1") == 0

    def test_and_an_ipv6_literal_is_not_mistaken_for_one(self, tmp_path):
        """Colons are how IPv6 is spelled; refusing those would refuse the address itself."""
        assert self._init(tmp_path, "2001:db8::1") == 0


class TestAnInvitationIsOneTimeShortLivedWithdrawableAndCheckable:
    """Four properties, and each one is a different way of being handed to the wrong person.

    An invitation travels out of band -- read aloud, pasted into a chat, photographed off a
    screen. Every one of those can reach further than intended, so the question is never "can it
    leak" but "for how long does a leak matter, and what can be done once it has".
    """

    def _invitation(self, **changes):
        from agentnode_sdk.gateway import invitation

        made = dict(where="https://127.0.0.1:8099", code="AAAA-BBBB-CCCC",
                    certificate_sha256="a" * 64, expires=time.time() + 900,
                    gateway_id="gw-1234567890")
        made.update(changes)
        return invitation.write(made.pop("where"), made.pop("code"),
                                made.pop("certificate_sha256"), **made)

    def test_it_says_when_it_stops_working(self):
        from agentnode_sdk.gateway import invitation

        carried = invitation.details(self._invitation())
        assert carried["expires"] > time.time()

    def test_and_a_client_refuses_an_expired_one_without_contacting_anything(self, capsys,
                                                                            monkeypatch):
        """Contacting the far end first makes an expired invitation look like a broken network."""
        from agentnode_sdk.cli import remote_commands

        def nobody_should_call_this(*_a, **_k):
            raise AssertionError("it contacted the gateway before checking the expiry")

        from agentnode_sdk.gateway import client as gateway_client

        monkeypatch.setattr(gateway_client, "hello", nobody_should_call_this)

        class Args:
            url = self._invitation(expires=time.time() - 600)
            code = ""
            name = ""

        assert remote_commands.cmd_connect(Args()) == 2
        said = capsys.readouterr().out
        assert "has expired" in said
        assert "nothing was contacted" in said

    def test_it_names_the_gateway_it_was_written_for(self):
        from agentnode_sdk.gateway import invitation

        assert invitation.details(self._invitation())["gateway"] == "gw-1234567890"

    def test_an_older_invitation_does_not_pair_with_a_rebuilt_gateway(self, capsys, monkeypatch):
        """It would otherwise appear to work, and attach the client to something nobody meant."""
        from agentnode_sdk.cli import remote_commands

        from agentnode_sdk.gateway import client as gateway_client

        monkeypatch.setattr(
            gateway_client, "hello",
            lambda url, pin="": {"gateway": {"gateway_id": "a-different-gateway"},
                                 "protocol": "em3c/2"})

        class Args:
            url = self._invitation()
            code = ""
            name = ""

        assert remote_commands.cmd_connect(Args()) == 1
        said = capsys.readouterr().out
        assert "not the gateway this invitation was written for" in said

    def test_the_essentials_are_still_three(self):
        """Older invitations carry no expiry and no gateway, and must still be usable."""
        from agentnode_sdk.gateway import invitation

        old = invitation.write("https://x:8099", "AAAA-BBBB-CCCC", "b" * 64)
        where, code, pin = invitation.read(old)
        assert (where, code, pin) == ("https://x:8099", "AAAA-BBBB-CCCC", "b" * 64)
        assert "expires" not in invitation.details(old)


class TestTakingAnInvitationBack:
    """Issuing another one replaces the first, but that is not the same as being able to kill it."""

    def _state(self, tmp_path):
        from agentnode_sdk.gateway.server import GatewayState

        return GatewayState(str(tmp_path), version="test")

    def test_a_code_that_was_withdrawn_no_longer_works(self, tmp_path):
        from agentnode_sdk.gateway.identity import PairingError

        state = self._state(tmp_path)
        code = state.start_pairing()
        assert state.withdraw_pairing() is True
        with pytest.raises(PairingError):
            state.redeem_pairing(code)

    def test_withdrawing_when_there_is_nothing_says_so(self, tmp_path):
        state = self._state(tmp_path)
        state.start_pairing()
        assert state.withdraw_pairing() is True
        assert state.withdraw_pairing() is False

    def test_a_code_that_was_not_withdrawn_still_works(self, tmp_path):
        """The counter-case: withdrawal must not be the only outcome."""
        state = self._state(tmp_path)
        code = state.start_pairing()
        assert state.redeem_pairing(code)

    def test_and_it_is_still_one_time(self, tmp_path):
        from agentnode_sdk.gateway.identity import PairingError

        state = self._state(tmp_path)
        code = state.start_pairing()
        state.redeem_pairing(code)
        with pytest.raises(PairingError):
            state.redeem_pairing(code)

    def test_and_still_expires(self, tmp_path):
        from agentnode_sdk.gateway.identity import PAIRING_TTL_SECONDS, PairingError

        state = self._state(tmp_path)
        code = state.start_pairing(now=1000.0)
        with pytest.raises(PairingError):
            state.redeem_pairing(code, now=1000.0 + PAIRING_TTL_SECONDS + 1)
