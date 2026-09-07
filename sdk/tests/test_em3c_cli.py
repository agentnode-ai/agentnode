"""The gateway and remote commands, driven the way a person drives them.

Every test here goes through `agentnode_sdk.cli.main.main(argv)` -- the real argument parser, the
real command functions, the real connection file. Nothing reaches into the service to arrange an
outcome, because the thing being tested is precisely whether the published commands are enough on
their own. A test that calls the internals proves the internals work and says nothing about
whether anybody can get at them.

The gateway itself runs in-process against a stand-in backend, so these can run anywhere. What a
real container does is the container lane's job.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

import pytest

from agentnode_sdk.cli.main import main
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService, make_server

from tests.test_em3c_gateway import StandInBackend, _store_measurement


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A private AGENTNODE_HOME, so nothing touches the developer's own machine."""
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("AGENTNODE_HOME", str(root))
    return root


@pytest.fixture()
def running_gateway(tmp_path):
    """A gateway that has been measured, listening on loopback."""
    state = GatewayState(tmp_path / "gw", version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    server = make_server(service, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d" % server.server_address[1]
    try:
        yield url, state, service
    finally:
        server.shutdown()


@pytest.fixture()
def unmeasured_gateway(tmp_path):
    state = GatewayState(tmp_path / "gw-raw", version="test")
    service = GatewayService(state, backend=StandInBackend())
    server = make_server(service, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d" % server.server_address[1]
    try:
        yield url, state, service
    finally:
        server.shutdown()


def _connect(url, state, extra=()):
    code = state.start_pairing()
    return main(["remote", "connect", url, "--code", code, *extra])


# ---------------------------------------------------------------------------


class TestTheOrdinaryPathIsShort:
    """Connect, test, run. Three commands, no JSON, no digests, no URLs by hand."""

    def test_connect_then_test_then_run(self, home, running_gateway, tmp_path, capsys):
        url, state, service = running_gateway

        assert _connect(url, state, ("--as", "lab")) == 0
        out = capsys.readouterr().out
        assert "Connected" in out
        assert "runs inside a container" in out or "measured" in out

        assert main(["remote", "test"]) == 0
        out = capsys.readouterr().out
        assert "It works" in out
        assert "no network access" in out

        script = tmp_path / "hello.py"
        script.write_text("print('from the script')\n", encoding="utf-8")
        assert main(["remote", "run", str(script)]) == 0
        assert "RAN" in capsys.readouterr().out

    def test_the_user_never_sees_the_machinery(self, home, running_gateway, capsys):
        """Real, load-bearing, and not what someone setting this up needs to read."""
        url, state, service = running_gateway
        _connect(url, state)
        main(["remote", "test"])
        main(["remote", "status"])
        text = capsys.readouterr().out.lower()
        for jargon in ("policy digest", "hmac", "vantage", "nonce", "sha256", "canonical"):
            assert jargon not in text, f"{jargon!r} leaked into ordinary output"

    def test_verbose_is_where_the_machinery_lives(self, home, running_gateway, capsys):
        url, state, service = running_gateway
        _connect(url, state)
        capsys.readouterr()
        main(["remote", "status", "--verbose"])
        text = capsys.readouterr().out.lower()
        assert "fingerprint" in text or "gateway id" in text

    def test_list_use_and_disconnect(self, home, running_gateway, capsys):
        url, state, service = running_gateway
        _connect(url, state, ("--as", "one"))
        code = state.start_pairing()
        assert main(["remote", "connect", url, "--code", code, "--as", "two"]) == 0

        capsys.readouterr()
        assert main(["remote", "list"]) == 0
        listed = capsys.readouterr().out
        assert "one" in listed and "two" in listed

        assert main(["remote", "use", "one"]) == 0
        assert main(["remote", "disconnect", "--name", "two"]) == 0
        capsys.readouterr()
        main(["remote", "list"])
        assert "two" not in capsys.readouterr().out

    def test_rotating_keeps_the_connection_working(self, home, running_gateway, capsys):
        url, state, service = running_gateway
        _connect(url, state, ("--as", "lab"))
        before = json.loads((home / "gateways.json").read_text(encoding="utf-8"))
        assert main(["remote", "rotate"]) == 0
        after = json.loads((home / "gateways.json").read_text(encoding="utf-8"))
        assert (after["gateways"]["lab"]["token"]
                != before["gateways"]["lab"]["token"]), "the token did not change"
        assert main(["remote", "test"]) == 0


class TestTheCredentialIsLookedAfter:

    def test_the_token_is_never_printed(self, home, running_gateway, capsys):
        url, state, service = running_gateway
        _connect(url, state, ("--as", "lab"))
        main(["remote", "status"])
        main(["remote", "list"])
        printed = capsys.readouterr().out
        stored = json.loads((home / "gateways.json").read_text(encoding="utf-8"))
        token = stored["gateways"]["lab"]["token"]
        assert token
        assert token not in printed, "the access token was printed to the terminal"

    def test_the_pairing_code_is_never_written_into_the_connection_file(self, home,
                                                                       running_gateway):
        url, state, service = running_gateway
        code = state.start_pairing()
        assert main(["remote", "connect", url, "--code", code, "--as", "lab"]) == 0
        saved = (home / "gateways.json").read_text(encoding="utf-8")
        assert code not in saved

    @pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
    def test_the_connection_file_is_readable_only_by_its_owner(self, home, running_gateway):
        url, state, service = running_gateway
        _connect(url, state)
        mode = (home / "gateways.json").stat().st_mode
        assert mode & 0o077 == 0, oct(mode)


class TestTheCommandsRefuseTheRightThings:

    def test_connecting_over_plain_http_to_another_machine_is_refused(self, home, capsys):
        assert main(["remote", "connect", "http://10.0.0.4:8099",
                     "--code", "ABCD-EFGH-JKLM"]) == 1
        out = capsys.readouterr().out
        assert "not be encrypted" in out
        assert "--tls-cert" in out, "the refusal must name a way through"

    def test_a_credential_in_the_address_is_refused(self, home, capsys):
        assert main(["remote", "connect", "http://127.0.0.1:8099/?token=s3cret",
                     "--code", "ABCD-EFGH-JKLM"]) == 2
        out = capsys.readouterr().out
        assert "s3cret" not in out

    def test_connecting_to_an_unmeasured_gateway_says_so_plainly(self, home,
                                                                 unmeasured_gateway, capsys):
        url, state, service = unmeasured_gateway
        assert _connect(url, state, ("--as", "raw")) == 0
        out = capsys.readouterr().out
        assert "not ready" in out
        assert "refuse work" in out

        assert main(["remote", "test"]) == 1
        out = capsys.readouterr().out
        assert "has not been measured" in out

    def test_a_wrong_code_does_not_leave_a_half_connection_behind(self, home,
                                                                  running_gateway, capsys):
        url, state, service = running_gateway
        state.start_pairing()
        assert main(["remote", "connect", url, "--code", "ZZZZ-ZZZZ-ZZZZ",
                     "--as", "nope"]) == 1
        assert not (home / "gateways.json").is_file() or \
            "nope" not in (home / "gateways.json").read_text(encoding="utf-8")

    def test_commands_that_need_a_gateway_say_how_to_get_one(self, home, capsys):
        for argv in (["remote", "test"], ["remote", "status"], ["remote", "list"]):
            capsys.readouterr()
            assert main(argv) == 1
            out = capsys.readouterr().out
            assert "remote connect" in out, argv


class TestTheOperatorSide:

    def test_init_then_pair_refuses_until_measured(self, home, capsys):
        assert main(["gateway", "init"]) == 0
        out = capsys.readouterr().out
        assert "sandbox gateway" in out.lower()
        assert "doctor --measure" in out

        # pairing someone to a gateway that will refuse their work wastes their time
        assert main(["gateway", "pair"]) == 1
        out = capsys.readouterr().out
        assert "No code issued" in out

    def test_init_refuses_half_a_certificate(self, home, capsys):
        assert main(["gateway", "init", "--tls-cert", "/nowhere/cert.pem"]) == 2
        assert "needs its key" in capsys.readouterr().out

    def test_init_refuses_a_certificate_that_does_not_load(self, home, tmp_path, capsys):
        cert = tmp_path / "absent.pem"
        key = tmp_path / "absent.key"
        assert main(["gateway", "init", "--tls-cert", str(cert),
                     "--tls-key", str(key)]) == 1
        assert "could not be loaded" in capsys.readouterr().out

    def test_status_before_anything_exists_points_at_init(self, home, capsys):
        assert main(["gateway", "status"]) == 1
        assert "gateway init" in capsys.readouterr().out

    def test_clients_and_revoke(self, home, running_gateway, capsys, monkeypatch):
        url, state, service = running_gateway
        _connect(url, state, ("--as", "lab"))
        monkeypatch.setenv("AGENTNODE_HOME", str(home))

        # the operator's view of the same gateway
        capsys.readouterr()
        assert main(["gateway", "clients", "--dir", str(state.root)]) == 0
        listed = capsys.readouterr().out
        assert "lab" in listed

        client_id = state.paired_clients()[0]["client_id"]
        assert main(["gateway", "revoke", "--dir", str(state.root),
                     "--client", client_id[:8]]) == 0
        assert "no longer send work" in capsys.readouterr().out

        # and the client really is out
        assert main(["remote", "test"]) == 1

    def test_revoking_someone_who_is_not_there(self, home, running_gateway, capsys):
        url, state, service = running_gateway
        assert main(["gateway", "revoke", "--dir", str(state.root),
                     "--client", "nobody"]) == 1
        assert "gateway clients" in capsys.readouterr().out


class TestPairingWorksBetweenProcesses:
    """`gateway pair` and `gateway start` are different processes. They always were."""

    def test_a_code_issued_by_one_process_is_accepted_by_another(self, tmp_path):
        issuing = GatewayState(tmp_path / "gw", version="test")
        code = issuing.start_pairing()

        # a second GatewayState on the same directory is what the serving process has
        serving = GatewayState(tmp_path / "gw", version="test")
        token = serving.redeem_pairing(code, client_name="from-another-process")
        assert token

    def test_it_is_still_usable_only_once_across_processes(self, tmp_path):
        from agentnode_sdk.gateway.identity import PairingError

        issuing = GatewayState(tmp_path / "gw", version="test")
        code = issuing.start_pairing()
        serving = GatewayState(tmp_path / "gw", version="test")
        assert serving.redeem_pairing(code)

        third = GatewayState(tmp_path / "gw", version="test")
        with pytest.raises(PairingError):
            third.redeem_pairing(code)

    def test_two_processes_racing_for_one_code_produce_one_token(self, tmp_path):
        """The rename is the claim. Read-then-delete would let both win."""
        issuing = GatewayState(tmp_path / "gw", version="test")
        code = issuing.start_pairing()

        outcomes: list = []
        barrier = threading.Barrier(6)

        def attempt():
            # a fresh state object each time, so nothing is shared but the directory
            state = GatewayState(tmp_path / "gw", version="test")
            barrier.wait()
            try:
                outcomes.append(("ok", state.redeem_pairing(code)))
            except Exception as exc:                          # noqa: BLE001
                outcomes.append(("no", str(exc)))

        threads = [threading.Thread(target=attempt) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        wins = [o for o in outcomes if o[0] == "ok"]
        assert len(outcomes) == 6, outcomes
        assert len(wins) == 1, f"{len(wins)} of 6 processes were given a token"

    def test_the_code_itself_is_not_written_to_disk(self, tmp_path):
        """The gateway directory is what an attacker with a backup copy gets."""
        state = GatewayState(tmp_path / "gw", version="test")
        code = state.start_pairing()
        stored = (tmp_path / "gw" / "pairing.json").read_text(encoding="utf-8")
        assert code not in stored
        assert code.replace("-", "") not in stored
