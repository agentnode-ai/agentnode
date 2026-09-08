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

    def test_the_issuer_cannot_redeem_a_code_another_process_already_took(self, tmp_path):
        """EM3C-GATEWAY-0012: the issuing process kept its own copy.

        `gateway pair` issues the code and holds it in memory. If it could redeem from that copy
        without winning the disk claim, then one code yields two tokens -- once to whoever raced
        for the file, and once more to the issuer. The earlier race test used only fresh state
        objects, so the issuer was never among the racers and this path was invisible to it.
        """
        from agentnode_sdk.gateway.identity import PairingError

        issuer = GatewayState(tmp_path / "gw", version="test")
        code = issuer.start_pairing()

        other = GatewayState(tmp_path / "gw", version="test")
        assert other.redeem_pairing(code, client_name="the-racer")

        with pytest.raises(PairingError):
            issuer.redeem_pairing(code, client_name="the-issuer")

    def test_the_issuer_can_still_redeem_when_nobody_raced_it(self, tmp_path):
        """The ordinary case: one process issues and the same one accepts."""
        issuer = GatewayState(tmp_path / "gw", version="test")
        code = issuer.start_pairing()
        assert issuer.redeem_pairing(code, client_name="ordinary")

    def test_the_code_itself_is_not_written_to_disk(self, tmp_path):
        """The gateway directory is what an attacker with a backup copy gets."""
        state = GatewayState(tmp_path / "gw", version="test")
        code = state.start_pairing()
        stored = (tmp_path / "gw" / "pairing.json").read_text(encoding="utf-8")
        assert code not in stored
        assert code.replace("-", "") not in stored


def _free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_until_listening(url: str, timeout: float = 90.0) -> None:
    import time
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + "/v1/hello", timeout=5) as response:
                if response.status == 200:
                    return
        except Exception as exc:                              # noqa: BLE001
            last = str(exc)
        time.sleep(0.25)
    raise AssertionError("the gateway never started listening: " + last)


@pytest.mark.skipif(not os.environ.get("AGENTNODE_SANDBOX_E2E"),
                    reason="needs a container runtime")
class TestTheWholeJourneyThroughThePublishedCommands:
    """Set one up, connect to it, run something, stop something, and be shut out again.

    Nothing here reaches into a service object to arrange an outcome. The gateway is a separate
    process started by `agentnode gateway start`, the pairing code comes out of `agentnode
    gateway pair` in a third process, and every client step is a published command. That is the
    point: the internals have been tested for a while now, and none of that says whether a person
    with a terminal can actually get to them.

    The one step that is not a CLI command is the replay, which is deliberate -- re-sending a
    captured request is not something the CLI offers or should. It goes through the gateway's
    own HTTP API, which is equally public.
    """

    @staticmethod
    @pytest.fixture(scope="class")
    def gateway_process(tmp_path_factory):
        import subprocess
        import sys

        root = tmp_path_factory.mktemp("e2e")
        gw_dir = root / "gw"
        home = root / "home"
        home.mkdir()
        env = dict(os.environ, AGENTNODE_HOME=str(home))

        assert main(["gateway", "init", "--dir", str(gw_dir)]) == 0
        # The real suite against the real runtime. This is the slow part, and it is the part
        # that decides whether the gateway is allowed to run anything at all.
        assert main(["gateway", "doctor", "--dir", str(gw_dir), "--measure"]) == 0

        port = _free_port()
        process = subprocess.Popen(
            [sys.executable, "-m", "agentnode_sdk.cli", "gateway", "start",
             "--dir", str(gw_dir), "--port", str(port)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        url = "http://127.0.0.1:%d" % port
        try:
            _wait_until_listening(url)
            yield url, gw_dir, home, env
        finally:
            process.terminate()
            try:
                process.wait(timeout=30)
            except Exception:                                 # noqa: BLE001
                process.kill()

    def _pair_and_connect(self, url, gw_dir, home, capsys, name="e2e"):
        import re

        capsys.readouterr()
        assert main(["gateway", "pair", "--dir", str(gw_dir)]) == 0
        printed = capsys.readouterr().out
        found = re.search(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", printed)
        assert found, "the pair command printed no code: " + printed
        code = found.group(0)
        assert main(["remote", "connect", url, "--code", code, "--as", name]) == 0
        return code

    def test_a_person_can_set_this_up_and_run_something_in_it(self, gateway_process,
                                                              monkeypatch, capsys, tmp_path):
        url, gw_dir, home, env = gateway_process
        monkeypatch.setenv("AGENTNODE_HOME", str(home))

        self._pair_and_connect(url, gw_dir, home, capsys, name="journey")
        out = capsys.readouterr().out
        assert "Connected" in out

        assert main(["remote", "status"]) == 0
        assert "not ready" not in capsys.readouterr().out

        assert main(["remote", "test"]) == 0
        out = capsys.readouterr().out
        print("  [observed] remote test said:", out.strip().replace("\n", " | ")[:300])
        assert "It works" in out

        script = tmp_path / "job.py"
        script.write_text("import os\nprint('E2E-RAN-AS', os.getuid(), flush=True)\n",
                          encoding="utf-8")
        assert main(["remote", "run", str(script), "--max-seconds", "90"]) == 0
        out = capsys.readouterr().out
        print("  [observed] remote run said:", out.strip().replace("\n", " | ")[:300])
        assert "E2E-RAN-AS 1000" in out, "the job did not run as the unprivileged user"

    def test_a_run_can_be_stopped_from_another_terminal(self, gateway_process,
                                                        monkeypatch, capsys, tmp_path):
        import re
        import threading

        url, gw_dir, home, env = gateway_process
        monkeypatch.setenv("AGENTNODE_HOME", str(home))
        self._pair_and_connect(url, gw_dir, home, capsys, name="stoppable")

        script = tmp_path / "slow.py"
        script.write_text("import time\nprint('started', flush=True)\ntime.sleep(120)\n",
                          encoding="utf-8")

        captured: dict = {}

        def run_it():
            captured["code"] = main(["remote", "run", str(script), "--max-seconds", "150",
                                     "--timeout", "180"])

        worker = threading.Thread(target=run_it, daemon=True)
        worker.start()

        # find the run id the command printed, then stop it the way anyone else would
        run_id = ""
        import time as _time
        deadline = _time.monotonic() + 60
        while _time.monotonic() < deadline and not run_id:
            found = re.search(r"run: ([0-9a-f]{8,})", capsys.readouterr().out)
            if found:
                run_id = found.group(1)
            else:
                _time.sleep(0.5)
        assert run_id, "the run command never printed a run id"

        _time.sleep(3)                       # let the container actually come up
        assert main(["remote", "cancel", "--run", run_id]) == 0
        print("  [observed] cancel said:", capsys.readouterr().out.strip()[:200])
        worker.join(timeout=180)
        assert not worker.is_alive(), "the run never came back after being cancelled"

    def test_a_job_that_overruns_is_stopped_by_the_sandbox(self, gateway_process,
                                                           monkeypatch, capsys, tmp_path):
        url, gw_dir, home, env = gateway_process
        monkeypatch.setenv("AGENTNODE_HOME", str(home))
        self._pair_and_connect(url, gw_dir, home, capsys, name="overrunner")

        script = tmp_path / "forever.py"
        script.write_text("import time\nprint('going', flush=True)\ntime.sleep(600)\n",
                          encoding="utf-8")
        code = main(["remote", "run", str(script), "--max-seconds", "10", "--timeout", "180"])
        out = capsys.readouterr().out
        print("  [observed] overrun run said:", out.strip().replace("\n", " | ")[:300])
        assert code != 0, "a job that ran past its limit reported success"

    def test_revoking_shuts_the_client_out_at_once(self, gateway_process,
                                                   monkeypatch, capsys, tmp_path):
        import re

        url, gw_dir, home, env = gateway_process
        monkeypatch.setenv("AGENTNODE_HOME", str(home))
        self._pair_and_connect(url, gw_dir, home, capsys, name="doomed")
        assert main(["remote", "test"]) == 0

        capsys.readouterr()
        assert main(["gateway", "clients", "--dir", str(gw_dir)]) == 0
        listed = capsys.readouterr().out
        found = re.search(r"doomed\s+([0-9a-f]{6,})", listed)
        assert found, "the clients list did not show the connection: " + listed

        assert main(["gateway", "revoke", "--dir", str(gw_dir),
                     "--client", found.group(1)]) == 0
        capsys.readouterr()
        assert main(["remote", "test"]) == 1, "a revoked client could still run work"
        print("  [observed] after revocation:", capsys.readouterr().out.strip()[:200])

    def test_nothing_is_left_behind_by_any_of_it(self, gateway_process):
        import subprocess

        subprocess.run(["docker", "ps", "-a"], capture_output=True, timeout=30)
        listed = subprocess.run(
            ["docker", "container", "ls", "-a", "--filter", "name=agentnode-em3c-",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=30,
        )
        assert listed.returncode == 0, listed.stderr
        leftovers = [n for n in listed.stdout.split() if n.strip()]
        print("  [observed] leftover run containers:", leftovers or "none")
        assert not leftovers, leftovers


class TestTheCommandsSayWhatWasGrantedNotWhatWasAsked:
    """EM3C-EGRESS-CLASSIFY-0001.

    In the external two-machine run the client printed "It may reach: example.com -- and
    nothing else." and the run then executed with no network at all. The sentence was printed
    before the job had been submitted, so it described a request as though it were a grant.
    A person reading it had no way to tell that the destination was never allowed.
    """

    @pytest.fixture()
    def closed_gateway(self, tmp_path):
        """A gateway whose operator permits no egress -- the shipped default."""
        from agentnode_sdk.sandbox.contract import NetworkRules, SandboxPolicy

        closed = SandboxPolicy(
            network=NetworkRules(enabled=False, allowed_destinations=frozenset()))
        state = GatewayState(tmp_path / "gw-closed", version="test")
        service = GatewayService(state, backend=StandInBackend(), operator_policy=closed)
        _store_measurement(service)
        server = make_server(service, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:%d" % server.server_address[1]
        try:
            yield url, state, service
        finally:
            server.shutdown()

    def test_the_request_is_not_worded_as_a_grant(self, home, closed_gateway, tmp_path, capsys):
        url, state, _service = closed_gateway
        _connect(url, state)
        capsys.readouterr()
        script = tmp_path / "reach.py"
        script.write_text("print('x')\n", encoding="utf-8")

        main(["remote", "run", str(script), "--allow", "example.com"])
        out = capsys.readouterr().out

        assert "Asking to reach: example.com" in out, out
        assert "It may reach: example.com" not in out, \
            "the client stated a grant it had not been given"

    def test_what_was_actually_granted_is_printed(self, home, closed_gateway, tmp_path, capsys):
        """The point is not to say less. It is to say the true thing."""
        url, state, _service = closed_gateway
        _connect(url, state)
        capsys.readouterr()
        script = tmp_path / "reach.py"
        script.write_text("print('x')\n", encoding="utf-8")

        main(["remote", "run", str(script), "--allow", "example.com"])
        out = capsys.readouterr().out

        assert "Granted: no network access." in out, out

    def test_the_narrowing_is_reported_to_the_person(self, home, closed_gateway, tmp_path,
                                                     capsys):
        """The server now discloses every narrowing; the client has to show it."""
        url, state, _service = closed_gateway
        _connect(url, state)
        capsys.readouterr()
        script = tmp_path / "reach.py"
        script.write_text("print('x')\n", encoding="utf-8")

        main(["remote", "run", str(script), "--allow", "example.com"])
        out = capsys.readouterr().out

        assert "stricter than asked" in out, out
        assert "network.allowed_destinations" in out, out

    def test_a_granted_destination_is_named_as_granted(self, home, tmp_path, capsys):
        """The control. Without it, a client that always said "no network" would pass."""
        from agentnode_sdk.sandbox.contract import NetworkRules, SandboxPolicy

        open_policy = SandboxPolicy(
            network=NetworkRules(enabled=True,
                                 allowed_destinations=frozenset({"example.com"})))
        state = GatewayState(tmp_path / "gw-open", version="test")
        service = GatewayService(state, backend=StandInBackend(), operator_policy=open_policy)
        _store_measurement(service)
        server = make_server(service, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:%d" % server.server_address[1]
        try:
            _connect(url, state)
            capsys.readouterr()
            script = tmp_path / "reach.py"
            script.write_text("print('x')\n", encoding="utf-8")
            main(["remote", "run", str(script), "--allow", "example.com"])
            out = capsys.readouterr().out
            assert "Granted: example.com -- and nothing else." in out, out
            assert "stricter than asked" not in out, \
                "nothing was narrowed, so nothing should have been reported as narrowed"
        finally:
            server.shutdown()

    def test_the_test_command_reads_cleanup_rather_than_asserting_it(self, home,
                                                                     running_gateway, capsys):
        """`remote test` used to state that the sandbox was removed afterwards without ever
        looking at whether that had been established."""
        url, state, _service = running_gateway
        _connect(url, state)
        capsys.readouterr()

        assert main(["remote", "test"]) == 0
        out = capsys.readouterr().out
        assert "It works" in out
        assert "was removed afterwards, and that was confirmed." in out \
            or "could not be confirmed" in out \
            or "was NOT removed afterwards" in out, out
        assert "It had no network access and was removed afterwards." not in out, \
            "removal was asserted rather than read from the record"


class TestAnOperatorCanOpenEgressFromTheCommandLine:
    """The other half of EM3C-EGRESS-CLASSIFY-0001: `--allow` on the client was unreachable
    because no published gateway command could raise the operator ceiling that denies it.

    EM3C-Y6-DECISION-0001 then made opening it a measured transaction, so what these tests check
    is that the command exists, that it never claims a change it has not made, and that a
    machine which cannot measure the change does not get the change.
    """

    def test_the_egress_command_exists(self, home, tmp_path, capsys):
        root = tmp_path / "gw"
        main(["gateway", "egress", "--dir", str(root)])
        out = capsys.readouterr().out
        assert "not been measured" in out, out
        assert "doctor --measure" in out, out

    def test_a_host_that_cannot_be_enforced_is_refused(self, home, tmp_path, capsys):
        root = tmp_path / "gw"
        assert main(["gateway", "egress", "--dir", str(root), "--allow", "*"]) == 2
        out = capsys.readouterr().out
        assert "cannot be enforced" in out, out
        assert "Nothing was changed" in out, out

    def test_opposite_flags_are_refused_rather_than_guessed(self, home, tmp_path, capsys):
        root = tmp_path / "gw"
        assert main(["gateway", "egress", "--dir", str(root),
                     "--allow", "example.com", "--none"]) == 2
        assert "opposite things" in capsys.readouterr().out

    def _measurement_fails(self, monkeypatch):
        """Make the measurement fail on purpose.

        These two tests first got their failure from the machine having no container runtime,
        which is true on a laptop and false in CI -- so in CI the measurement succeeded and both
        failed for the opposite of the reason they were about.
        """
        from agentnode_sdk.gateway.readiness import Readiness
        from agentnode_sdk.gateway.server import GatewayService as Service

        def fake(self, options=None, now=None):
            return Readiness(False, "the allowlist could not be measured on this machine.",
                             {}, ("egress_allowlist",),
                             ("agentnode gateway doctor --measure",))

        monkeypatch.setattr(Service, "measure", fake)

    def test_it_says_it_is_measuring_and_never_that_it_has_finished(self, home, tmp_path,
                                                                    capsys, monkeypatch):
        """The command may not say the policy is saved, active or protecting on the way past."""
        self._measurement_fails(monkeypatch)
        root = tmp_path / "gw"
        rc = main(["gateway", "egress", "--dir", str(root), "--allow", "example.com"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "Measuring the protections" in out, out
        assert "still the one in force" in out, out
        assert "remains in force" in out, out
        for premature in ("is now in force", "Saved. It takes effect", "may reach:"):
            assert premature not in out, f"the command claimed {premature!r} without having done it"

    def test_a_gateway_that_cannot_measure_does_not_get_the_policy(self, home, tmp_path,
                                                                   monkeypatch):
        from agentnode_sdk.gateway.activation import ActivationStore

        self._measurement_fails(monkeypatch)
        root = tmp_path / "gw"
        main(["gateway", "egress", "--dir", str(root), "--allow", "example.com"])
        assert ActivationStore(root).load_active() is None, \
            "a policy was put in force on a machine that could not measure it"

    def test_a_measurement_that_passes_does_report_the_policy_in_force(self, home, tmp_path,
                                                                       capsys, monkeypatch):
        """The control. Without it, both tests above would pass on a command that could never
        activate anything, which is not the behaviour being described."""
        from agentnode_sdk.gateway.activation import ActivationStore
        from agentnode_sdk.gateway.identity import GatewayState
        from agentnode_sdk.gateway.readiness import Readiness
        from agentnode_sdk.gateway.server import GatewayService as Service

        root = tmp_path / "gw"
        seeded = GatewayService(GatewayState(root, version="test"), backend=StandInBackend())
        _store_measurement(seeded)

        def fake(self, options=None, now=None):
            envelope = self.configured_envelope()
            active = ActivationStore(self.state.root).load_active()
            binding = self.report_binding(envelope.digest())
            ActivationStore(self.state.root).activate(envelope, active.report, binding.as_dict())
            return Readiness(True, "", {}, (), ())

        monkeypatch.setattr(Service, "measure", fake)
        assert main(["gateway", "egress", "--dir", str(root), "--allow", "example.com"]) == 0
        out = capsys.readouterr().out
        assert "Measurements passed" in out, out
        assert "example.com" in out, out
        assert ActivationStore(root).load_active().policy.mode == "restricted"

    def test_the_required_properties_are_named_before_measuring(self, home, tmp_path, capsys):
        """An operator is told what is about to be checked, not just that something is."""
        root = tmp_path / "gw"
        main(["gateway", "egress", "--dir", str(root), "--allow", "example.com"])
        out = capsys.readouterr().out
        assert "egress_allowlist" in out, out
        assert "container_isolation" in out, out
