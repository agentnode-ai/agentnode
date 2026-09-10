"""The external-run driver, exercised offline before it is ever pointed at two real machines.

Three external runs have now died in this driver rather than in the product. The first raised on
a missing executable while establishing that the executable was missing. The second could not
construct its third step, because the evidence module could not carry a field its own verifier
read. The third defect was found by `EM3C-EVIDENCE-0004` and by running the recorder against the
driver: `observed()` -- the helper behind more than a dozen steps -- answered one expectation of
five, and the recorder refuses a step that leaves any unanswered. Every step built through it
would have raised on the first call.

None of those was findable by reading. All three are findable by running, so the driver is in the
package now and this file runs it: the whole matrix, with its two I/O calls replaced by a world
this file describes, through the real recorder, to a real file, back through the real reader, into
the real verifier.

The stubs replace only what crosses a network or starts a process. Everything the driver decides
is the driver's own.
"""
from __future__ import annotations

import importlib
import json
import os

import pytest

from agentnode_sdk.gateway.connections import ConnectionStore, SavedGateway

pytest_plugins = ("tests.real_answers",)
from agentnode_sdk.tools import evidence
from agentnode_sdk.tools import external_config as config
from agentnode_sdk.tools import external_run as driver


CLIENT_HOST = "a-client-machine"
GATEWAY_HOST = "a-gateway-machine"
CONTAINER_ID = "0f1e2d3c4b5a"


def container_for(run_id: str) -> str:
    """What the gateway names a container for a run. The driver looks for exactly this."""
    return "agentnode-em3c-" + run_id[:16]
POLICY_A, POLICY_B = "a" * 64, "b" * 64
CONFORMANCE = "c" * 64
PORT = "18099"

#: Everything the driver needs to be told. Nothing is defaulted in the driver itself, so this is
#: also what a real operator has to write down. It is a DOCUMENT now, not a set of exported shell
#: variables: `EM3C-E5-CLASSIFY-0001` found a shell rewriting three of those on the way to the
#: process that needed them, and a document has no shell between it and its reader.
DOCUMENT = {
    "version": config.CONFIG_VERSION,
    "client_home": "home",
    "agentnode": "agentnode",
    "work": "work",
    "evidence": "record.jsonl",
    "ssh_key": "a-key",
    "server": "someone@a-gateway-machine",
    "gateway_bin": "/usr/local/bin/agentnode",
    "gateway_state": "/somewhere/state",
    "gateway_log": "/somewhere/gateway.log",
    "gateway_user": "a-service-account",
    "gateway_port": PORT,
}
SETTINGS = config.parse(DOCUMENT)


class World:
    """One consistent pair of machines, answering the driver's commands.

    Written as a world rather than as a list of canned replies so a test can change one fact --
    the container is still there, the listing is truncated, the sentinel never crossed -- and see
    the driver and the verifier react to that fact alone.
    """

    def __init__(self):
        self.generation = 1
        self.policy = POLICY_A
        self.mode = "none"
        self.allowlist: list[str] = []
        self.container_present = False
        self.gateway_log = ""
        self.pid = 1000
        self.truncate = ()            # substrings of commands whose answers lose their marker
        self.fail = ()                # substrings of commands that cannot run at all
        self.client_sentinel = ""
        self.gateway_sentinel = ""
        #: Set by the fixture from the real gateway this world stands beside. The world answers
        #: for the LINUX HOST over ssh; it does not answer for the gateway, which answers for
        #: itself over its own transport (`EM3C-E3-CLASSIFY-0001`).
        self.gateway = None
        self.run_id = ""
        #: Every ssh invocation, with what went over stdin. What the far side really received.
        self.sent: list = []
        #: Commands that run and fail, by substring. Distinct from `fail`, which is the
        #: transport not working at all -- a difference the driver has to keep.
        self.exit_codes: dict = {}

    # -- what the client runs locally ---------------------------------------------------------
    def launch(self, argv, timeout=600.0):
        parts = [str(a) for a in argv]
        text = " ".join(parts)
        if parts[0] == "hostname":
            return 0, CLIENT_HOST + "\n", "", ""
        if "vol C:" in text:
            return 0, " Volume Serial Number is 1234-5678\n", "", ""
        if parts[-1] == "ver":
            return 0, "Microsoft Windows [Version 10.0.22631.0]\n", "", ""
        if parts[0] == "docker":
            # This client has no container runtime, which is what step J establishes.
            return None, "", "docker not found", "FileNotFoundError"
        if "run" in parts and "remote" in parts:
            return self._remote_run(parts)
        if "cancel" in parts:
            self.container_present = False
            return 0, "cancelled\n", "", ""
        if "rotate" in parts:
            # The real thing: the production client asks the gateway for a new credential and
            # saves what it gets. Nothing here makes one up.
            from agentnode_sdk.gateway import client as real_client

            rotated = real_client.rotate(self.gateway.connection)
            self.gateway.connection = rotated
            self.token = rotated.token
            self.store.save(SavedGateway(name="e3", url=self.url, token=rotated.token,
                                         gateway_id=rotated.gateway_id,
                                         fingerprint=rotated.fingerprint))
            return 0, "rotated" + chr(10), "", ""
        return 0, "ok\n", "", ""

    def submit(self, ends_at_its_limit: bool = False) -> str:
        """A real job, on the real gateway, over its real transport. What comes back is the
        gateway's answer, and nothing in this file has an opinion about its shape."""
        answer = (self.gateway.a_run_its_limit_ended() if ends_at_its_limit
                  else self.gateway.a_finished_run())
        self.run_id = answer["run_id"]
        return self.run_id

    def _remote_run(self, parts):
        """A job.

        Refused only when EVERY host it named is off the ceiling, which is the gateway's own
        rule: a job naming one reachable host and one that is not gets the reachable one and a
        delta saying so, and a job naming nothing reachable is told so rather than quietly given
        nothing. The refusal is printed, the way the real command prints it.
        """
        asked = [parts[i + 1] for i, a in enumerate(parts)
                 if a == "--allow" and i + 1 < len(parts)]
        reachable = [host for host in asked
                     if self.mode == "unrestricted" or host in self.allowlist]
        if asked and not reachable and self.allowlist:
            return 1, ("refused: this job named no host it may reach ("
                       + ", ".join(asked) + " is not allowed)" + chr(10)), "", ""
        # The payload that ignores signals is the one the sandbox has to stop at its limit.
        stubborn = any("stubborn" in part for part in parts)
        out = "run: " + self.submit(ends_at_its_limit=stubborn) + chr(10)
        if stubborn:
            # What the real CLI exits with for a run its limit ended. `test_em3c_cli.py` runs
            # the real command against a real gateway and holds it to the same number, so this
            # is a model of something checked rather than a number chosen here.
            from agentnode_sdk.gateway.protocol import TIMEOUT_EXIT_STATUS

            return TIMEOUT_EXIT_STATUS, out + "it ran out of time" + chr(10), "", ""
        payload = parts[-3] if len(parts) >= 3 else ""
        source = ""
        try:
            source = open(payload, encoding="utf-8").read()
        except OSError:
            source = ""
        if "E3-FROM-CLIENT" in source:
            # The value the client put in its payload comes back, and the gateway makes its own.
            for line in source.splitlines():
                if "E3-FROM-CLIENT" in line and "'" in line:
                    self.client_sentinel = line.split("E3-FROM-CLIENT ")[1].strip("')\" ")
            self.gateway_sentinel = "f" * 32
            out += f"E3-FROM-CLIENT {self.client_sentinel}\n"
            out += f"E3-FROM-GATEWAY {self.gateway_sentinel}\n"
            # Both values are on the gateway: one arrived with the job, one was made there.
            self.gateway_log += f"{self.client_sentinel}\n{self.gateway_sentinel}\n"
        else:
            out += "it ran\n"
        return 0, out, "", ""

    # -- what the LINUX HOST answers over ssh -------------------------------------------------
    def over_ssh(self, script):
        """A POSIX login shell, reading its work from stdin and answering as one would.

        This is the TRANSPORT and nothing else. `server_query` is the driver's own and really
        runs: it builds the script, reads the status back, and decides what parsed and what did
        not. Stubbing it would have left exactly the part that failed untested.
        """
        command = script.splitlines()[0] if script.strip() else ""
        for bad in self.fail:
            if bad in command:
                return None, "", "ssh: connect failed", "OSError"
        body = self._answer(command)
        code = next((c for k, c in self.exit_codes.items() if k in command), 0)
        marker = "" if any(t in command for t in self.truncate) else driver.MARKER + chr(10)
        # The script takes the command's status BEFORE printing the marker and exits with it,
        # so the marker appears even when the command failed.
        return code, body + marker, "", ""

    def _answer(self, command):
        if command.startswith("hostname"):
            return GATEWAY_HOST + "\n"
        if "machine-id" in command:
            return "9c5c1e0a11d24f0b8b6f2e2f8a3c4d5e\n"
        if command.startswith("uname"):
            return "Linux 6.8.0-generic\n"
        if "findmnt" in command:
            return "0a1b2c3d-4e5f-6071-8293-a4b5c6d7e8f9\n"
        if "docker version" in command:
            return "29.8.0\n"
        if "docker info" in command:
            return f"{GATEWAY_HOST}/linux\n"
        if "sha256sum" in command:
            return f"{CONFORMANCE}  conformance.json\n"
        if "docker ps" in command:
            return ((container_for(self.run_id) + " " + CONTAINER_ID + chr(10))
                    if self.container_present else "")
        if "gateway egress" in command and "--allow" in command:
            self.generation += 1
            self.policy = POLICY_B
            self.mode = "restricted"
            self.allowlist = ["example.com"]
            return "Measurements passed. example.com\n"
        if "gateway egress" in command:
            listed = "".join(f"    {host}\n" for host in self.allowlist)
            return (listed
                    + f"  network mode          : {self.mode}\n"
                    + f"  activation generation : {self.generation}\n"
                    + f"  policy digest         : {self.policy}\n"
                    + f"  configured digest     : {self.policy}\n"
                    + "  digests agree         : True\n"
                    + "  measured properties   : container_isolation, verified_cleanup\n")
        if "pgrep" in command:
            return f"{self.pid}\n"
        if "pkill" in command:
            self.pid += 1
            return ""
        if "gateway start" in command:
            return ""
        if "ss -ltn" in command:
            return f"LISTEN 0 128 127.0.0.1:{PORT} 0.0.0.0:*" + chr(10)
        if command.startswith("grep -c"):
            needle = command.split("--")[1].split()[0] if "--" in command else ""
            return "1\n" if needle and needle in self.gateway_log else ""
        return ""


@pytest.fixture
def world(monkeypatch, tmp_path, real_gateway):
    """The driver, wired to a described world instead of to a network."""
    w = World()

    def fake_launch(argv, timeout=600.0, script=None):
        """The transport, and only the transport.

        What reaches the far machine is `script`, on stdin -- so this is where a test can see
        exactly what was sent, byte for byte, and whether anything rewrote it on the way
        (`EM3C-E3-CLASSIFY-0001`).
        """
        if str(argv[0]) == "ssh":
            w.sent.append({"argv": list(argv), "script": script or ""})
            return w.over_ssh(script or "")
        return w.launch(argv, timeout)

    class Popen:
        """`remote run` for the long job, which the driver reads line by line."""

        def __init__(self, *a, **kw):
            import io as _io
            self.stdout = _io.StringIO("run: " + w.submit() + chr(10) + "still going" + chr(10))
            self.stderr = _io.StringIO("")
            w.container_present = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None


    # The gateway record is NOT stubbed. Finding out what the gateway says about a run is one of
    # the driver's own decisions -- which URL, which credential, what an error means -- and
    # replacing it would leave exactly that untested (`EM3C-EVIDENCE-0007`). What is replaced is
    # the HTTP call itself, one function at the network boundary, and the connection the driver
    # reads is a real one written to a real store under a temporary home.
    # A REAL gateway, on loopback, with a real pairing. The driver reads the connection store
    # the same way it does on a real client, and every answer it gets about a run is one this
    # gateway produced, stamped and signed by its own code.
    w.gateway = real_gateway
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AGENTNODE_HOME", str(home))
    w.store = ConnectionStore()          # AGENTNODE_HOME above, resolved the way the driver does
    w.url = real_gateway.base
    w.token = real_gateway.connection.token
    w.store.save(SavedGateway(name="e3", url=w.url, token=w.token,
                              gateway_id=real_gateway.connection.gateway_id,
                              fingerprint=real_gateway.connection.fingerprint))

    # The driver is told where it is happening the way a real run tells it: by handing it a
    # configuration. Nothing is read from the environment, so nothing has to be set in it, and
    # the assertion below is what a run gets rather than what a shell happened to leave behind.
    importlib.reload(driver)
    driver.configure(SETTINGS)
    assert driver.PORT == PORT and driver.SERVER == SETTINGS.server

    monkeypatch.setattr(driver, "launch", fake_launch)
    monkeypatch.setattr(driver.subprocess, "Popen", Popen)
    monkeypatch.setattr(driver, "live_secrets", lambda: [])
    monkeypatch.setattr(driver, "HOME", home)
    monkeypatch.setattr(driver.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(driver, "WORK", tmp_path / "work")
    (tmp_path / "work").mkdir()
    return w


def drive(tmp_path):
    """Run the whole matrix and return (exit code, the steps as recorded and read back)."""
    path = tmp_path / "evidence.jsonl"
    code = driver.main(path)
    return code, evidence.load(path)


class TestTheDriverCanRecordAtAll:
    """The defect EM3C-EVIDENCE-0004 exposed: not that a step was wrong, that no step could be
    written. A driver that raises on its first call has no findings to report."""

    def test_every_step_of_the_matrix_is_recorded(self, world, tmp_path):
        code, steps = drive(tmp_path)
        assert len(steps) > 25, f"only {len(steps)} steps were recorded"
        assert isinstance(code, int)

    def test_the_helper_answers_every_expectation(self):
        built = driver.step(name="x", role="client", argv=[], started_at=1.0, ended_at=1.1,
                            exit_code=0)
        for name in evidence.EXPECTATIONS:
            assert getattr(built, name) is not evidence.UNSET, name

    def test_a_step_the_helper_built_is_accepted_by_the_recorder(self, tmp_path):
        recorder = evidence.Recorder(tmp_path / "e.jsonl", role="client", announce=False)
        recorder.record(driver.step(name="x", role="client", argv=[], started_at=1.0,
                                    ended_at=1.1, exit_code=0))
        assert (tmp_path / "e.jsonl").read_text(encoding="utf-8").strip() != ""

    def test_what_the_caller_states_still_wins(self):
        built = driver.step(name="x", role="client", argv=[], started_at=1.0, ended_at=1.1,
                            exit_code=0, expect_cleanup=True, expected_exit=3)
        assert built.expect_cleanup is True and built.expected_exit == 3


class TestTheRecordItProducesIsReadable:

    def test_the_file_reloads_through_the_real_reader(self, world, tmp_path):
        _code, steps = drive(tmp_path)
        for raw in steps:
            evidence.parse_step(dict(raw))

    def test_a_well_behaved_world_produces_a_verdict_with_no_findings(self, world, tmp_path):
        code, steps = drive(tmp_path)
        problems = evidence.verify(steps)
        assert problems == [], "\n".join(str(p) for p in problems)
        assert code == 0

    def test_the_two_machines_are_established(self, world, tmp_path):
        _code, steps = drive(tmp_path)
        assert evidence.verify_two_machines(steps) == []

    def test_the_binding_is_captured_and_holds(self, world, tmp_path):
        _code, steps = drive(tmp_path)
        assert evidence.verify_bindings(steps) == []

    def test_the_sought_container_id_is_carried_into_the_absence_check(self, world, tmp_path):
        """EM3C-EVIDENCE-0004: the driver never learned an id, so absence could only ever be an
        evidence error. The id has to be read while the container still exists."""
        _code, steps = drive(tmp_path)
        gone = [s for s in steps if s.get("expect_container_gone")]
        assert gone, "no step claimed a container was gone"
        assert gone[0]["container_query"]["sought_id"] == CONTAINER_ID

    def test_the_client_digest_is_over_output_the_record_keeps(self, world, tmp_path):
        """EM3C-EVIDENCE-0004: the digest was over a hostname the commands beside it never
        produced, so nothing connected the stored value to any evidence."""
        import hashlib

        _code, steps = drive(tmp_path)
        client = [s["machine"] for s in steps
                  if isinstance(s.get("machine"), dict) and s["machine"]["role"] == "client"][0]
        produced = [c for c in client["commands"] if c["command"] == "hostname"]
        assert produced, client["commands"]
        assert client["host_sha256"] == hashlib.sha256(
            produced[0]["stdout"].strip().encode()).hexdigest()


class TestTheDriverReactsToTheWorldRatherThanAsserting:
    """The controls. Every test above would pass on a driver that wrote the same record whatever
    it saw, so each of these changes one fact and requires the record to change with it."""

    def test_a_container_still_there_is_not_reported_gone(self, world, tmp_path):
        world.container_present = True

        real = driver.launch

        def keep_it(argv, timeout=600.0, script=None):
            if "cancel" in [str(a) for a in argv]:
                return 0, "cancelled" + chr(10), "", ""   # cancelled, and yet still there
            return real(argv, timeout, script)

        driver.launch = keep_it
        try:
            code, steps = drive(tmp_path)
        finally:
            driver.launch = real
        problems = evidence.verify(steps)
        assert any("still there" in p.message or "is still" in p.message
                   or "was found" in p.message for p in problems), \
            "\n".join(str(p) for p in problems)
        assert code != 0

    def test_a_truncated_policy_answer_is_not_read_as_a_snapshot(self, world, tmp_path):
        world.truncate = ("gateway egress",)
        _code, steps = drive(tmp_path)
        readings = [s for s in steps if s["name"].startswith("the operator-policy binding")]
        assert readings, "no binding was read at all"
        assert all(s["exit_code"] != 0 for s in readings), \
            "an answer that never reached its end was recorded as a complete reading"

    def test_a_gateway_that_cannot_be_reached_does_not_produce_an_identity(self, world,
                                                                          tmp_path):
        world.fail = ("hostname", "machine-id", "uname", "findmnt")
        _code, steps = drive(tmp_path)
        problems = evidence.verify_two_machines(steps)
        assert problems, "an unreachable gateway still produced a machine identity"

    def test_a_sentinel_that_never_reached_the_other_channel_fails(self, world, tmp_path):
        real_query = driver.server_query

        def never_found(command, timeout=300.0):
            if command.startswith("grep -c"):
                answer = real_query(command, timeout)
                return {**answer, "exit_code": 1, "stdout": driver.MARKER + "\n"}
            return real_query(command, timeout)

        driver.server_query = never_found
        try:
            _code, steps = drive(tmp_path)
        finally:
            driver.server_query = real_query
        assert evidence.verify_two_machines(steps), \
            "a value that never appeared on the other machine was accepted as having crossed"

    def test_an_identity_attached_to_the_wrong_channel_is_caught(self, world, tmp_path):
        """`EM3C-EVIDENCE-0011`: the suite proved a good world passes and several broken ones
        fail, and none of them broke provenance. A driver that recorded one machine's identity
        over the other machine's channel satisfied every control it had."""
        real = driver.observed

        def swap_the_channel(name, argv, holds, detail, *, role="client", **fields):
            if isinstance(fields.get("machine"), dict):
                role = "gateway" if role == "client" else "client"
            return real(name, argv, holds, detail, role=role, **fields)

        driver.observed = swap_the_channel
        try:
            code, steps = drive(tmp_path)
        finally:
            driver.observed = real
        problems = evidence.verify_two_machines(steps)
        assert any("channel" in p.message for p in problems),             "an identity carried on the other machine's channel was accepted"
        assert code != 0

    def test_the_verdict_is_not_a_constant(self, world, tmp_path):
        """Without this, every control above could be satisfied by a driver that always failed."""
        code, _steps = drive(tmp_path)
        assert code == 0


class TestRemoteWorkArrivesUnchanged:
    """`EM3C-E3-CLASSIFY-0001`, the second defect: the command was the last argument on the ssh
    command line, and on Windows the MSYS layer rewrites an argument that looks like an absolute
    POSIX path before ssh.exe is started. A Linux path left this machine as a Windows one."""

    A_PATH = "/home/a-service-account/em3c-state"

    def test_no_argument_could_be_rewritten(self, world):
        assert driver.check_argv() == [], driver.check_argv()
        assert not any(str(a).startswith("/") for a in driver.ssh_argv())

    def test_the_shape_it_looks_for_is_the_shape_a_shell_rewrites(self):
        """The rule itself, apart from any platform, so a Linux runner establishes it too."""
        assert driver.path_like(["/c/Users/somebody/.ssh/a-key"]) ==             ["/c/Users/somebody/.ssh/a-key"]
        assert driver.path_like([chr(92) * 2 + "server" + chr(92) + "share"])             == [chr(92) * 2 + "server" + chr(92) + "share"]
        assert driver.path_like(["ssh", "-T", "-i", "C:/Users/somebody/a-key", "-o",
                                 "BatchMode=yes", "someone@somewhere"]) == []

    @pytest.mark.skipif(os.name != "nt", reason="only this platform rewrites such an argument")
    def test_a_path_like_argument_is_refused_rather_than_sent(self, world, monkeypatch):
        """The guarantee is mechanical, not a habit: if one ever appears, nothing is sent."""
        monkeypatch.setattr(driver, "KEY", "/c/Users/somebody/.ssh/a-key")
        assert driver.check_argv() == ["/c/Users/somebody/.ssh/a-key"]
        answer = driver.server_query("ls " + self.A_PATH)
        assert answer["ran"] is False
        assert answer["error_class"] == "ArgumentWouldBeRewritten"
        assert answer["parsed"] is False and answer["complete"] is False

    @pytest.mark.skipif(os.name == "nt", reason="this platform does rewrite it")
    def test_and_on_a_machine_that_rewrites_nothing_it_is_not_reported(self, world, monkeypatch):
        """Not a hole: an absolute POSIX path on a POSIX client is the path. Reporting it would
        stop every run on Linux from sending anything at all."""
        monkeypatch.setattr(driver, "KEY", "/home/somebody/.ssh/a-key")
        assert driver.path_like(driver.ssh_argv()) == ["/home/somebody/.ssh/a-key"]
        assert driver.check_argv() == []
        assert driver.server_query("ls " + self.A_PATH)["ran"] is True

    def test_the_path_that_is_sent_is_the_path_that_was_asked_for(self, world):
        """What crosses is stdin, and stdin is what this reads back."""
        world.sent.clear()
        driver.server_query("ls -la " + self.A_PATH)
        assert len(world.sent) == 1
        sent = world.sent[0]
        assert self.A_PATH in sent["script"], sent["script"]
        assert not any(self.A_PATH in str(a) for a in sent["argv"])
        assert not any(str(a).startswith("/") for a in sent["argv"])

    def test_the_script_keeps_the_command_s_own_status(self):
        """Not the marker's. `cmd; echo MARKER` reports the echo's status, which is always 0."""
        script = driver.one_command("exit 3")
        assert script.splitlines()[0] == "exit 3"
        assert "__status=$?" in script
        assert script.rstrip().endswith("exit $__status")
        assert driver.MARKER in script

    def test_a_remote_step_records_the_command_s_own_status(self, world):
        assert driver.server_query("hostname")["exit_code"] == 0        # the control
        world.exit_codes = {"hostname": 3}
        answer = driver.server_query("hostname")
        assert answer["exit_code"] == 3, answer
        assert answer["parsed"] is False
        # and the marker is still there, so a failed command is not a truncated answer
        assert answer["complete"] is True

    def test_a_transport_failure_and_a_failed_command_are_different(self, world):
        world.exit_codes = {"hostname": 3}
        failed = driver.server_query("hostname")
        world.exit_codes = {}
        world.fail = ("hostname",)
        unreachable = driver.server_query("hostname")
        assert failed["error_class"] == "" and failed["exit_code"] == 3
        assert unreachable["error_class"] and unreachable["exit_code"] is None
        assert unreachable["complete"] is False

    def test_the_two_streams_stay_apart(self, world):
        answer = driver.server_query("hostname")
        assert answer["stdout"] and answer["stderr"] == ""
        world.fail = ("hostname",)
        answer = driver.server_query("hostname")
        assert answer["stdout"] == "" and answer["stderr"]

    def test_a_transport_failure_is_not_an_answer(self, world):
        """It cannot be read as the absence the step was looking for."""
        world.fail = ("docker ps",)
        listing, names, ids = driver.list_containers("a" * 32)
        assert listing["parsed"] is False
        assert names == [] and ids == []
        assert listing["error_class"]


class TestNothingLocalLeaksIntoThePackagedDriver:

    def test_it_imports_without_touching_the_process_it_is_imported_into(self):
        import importlib
        import os

        before = dict(os.environ)
        importlib.reload(driver)
        assert dict(os.environ) == before

    def test_no_machine_of_mine_is_written_into_it(self):
        import inspect

        source = inspect.getsource(driver)
        for private in ("AppData", "C--Users", "root@", "em3ce1", "116.203", "91.98",
                        "a1e_spike", "C:/Users", "clientvenv"):
            assert private not in source, private

    def test_no_account_or_port_is_written_in_by_hand(self):
        """Both were. A driver carrying one operator's account name is a driver about that
        operator's machine rather than about whichever two it is pointed at."""
        import inspect

        lines = inspect.getsource(driver).splitlines()
        assert not [line for line in lines if "8099" in line], "a port is written into the driver"
        assert not [line for line in lines if "sudo -u em" in line]
        assert any("PORT = str(settings.gateway_port)" in line for line in lines), (
            "the port no longer comes from the configuration this run was handed")

    def test_a_run_that_was_not_told_where_it_is_happening_refuses(self, tmp_path):
        """Nothing has a default that describes one pair of machines, so a run that was not told
        refuses instead of quietly using somebody else's."""
        importlib.reload(driver)
        assert driver.configured() is False
        assert driver.main(tmp_path / "e.jsonl") == 2
        assert not (tmp_path / "e.jsonl").exists()

    def test_every_setting_comes_from_the_configuration(self):
        importlib.reload(driver)
        driver.configure(config.parse(dict(DOCUMENT, server="someone@somewhere",
                                           gateway_state="/elsewhere/state")))
        assert driver.SERVER == "someone@somewhere"
        assert driver.STATE == "/elsewhere/state"
        importlib.reload(driver)

    def test_and_none_of_it_from_the_environment(self, monkeypatch):
        """`EM3C-E5-CLASSIFY-0001`: this is the exact thing that broke. The environment says one
        place, the configuration says another, and what runs is what the configuration says."""
        importlib.reload(driver)
        for name in config.SUPERSEDED_ENVIRONMENT:
            monkeypatch.setenv(name, "/somewhere/a-shell-decided")
        driver.configure(SETTINGS)
        assert driver.STATE == "/somewhere/state"
        assert driver.SERVER == "someone@a-gateway-machine"
        importlib.reload(driver)

    def test_a_run_started_with_that_environment_set_refuses(self, tmp_path, monkeypatch):
        """And it does not merely prefer the configuration: it stops, because somebody who set
        those believes they are in effect."""
        monkeypatch.setenv("EM3C_GATEWAY_STATE", "/somewhere/a-shell-decided")
        path = tmp_path / "run-config.json"
        path.write_text(json.dumps(DOCUMENT), encoding="utf-8")
        assert driver.run(["--config", str(path), "--preflight"]) == 2


def test_the_record_carries_no_hand_built_dictionary(world, tmp_path):
    """The whole point of the move. Every step in this file's evidence came from the driver,
    through the recorder, through the file -- not from a literal written in a test."""
    _code, steps = drive(tmp_path)
    assert steps
    for raw in steps:
        assert raw.get("schema") == evidence.SCHEMA or "schema" not in raw
        assert isinstance(json.dumps(raw), str)
