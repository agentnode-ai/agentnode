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

import json

import pytest

from agentnode_sdk.tools import evidence
from agentnode_sdk.tools import external_run as driver


CLIENT_HOST = "a-client-machine"
GATEWAY_HOST = "a-gateway-machine"
RUN_ID = "beeac323964d45d8b1c8ef90fb51bc30"
CONTAINER = f"agentnode-em3c-{RUN_ID[:16]}"
CONTAINER_ID = "0f1e2d3c4b5a"
POLICY_A, POLICY_B = "a" * 64, "b" * 64
CONFORMANCE = "c" * 64


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
        self.token = "t" * 32

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
            self.token = "r" * 32
            return 0, "rotated\n", "", ""
        return 0, "ok\n", "", ""

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
        out = f"run: {RUN_ID}\n"
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

    # -- what the gateway answers over SSH ----------------------------------------------------
    def query(self, command, timeout=300.0):
        full = command + '; echo "' + driver.MARKER + '"'
        for bad in self.fail:
            if bad in command:
                return {"ran": True, "exit_code": None, "stdout": "", "stderr": "ssh died",
                        "error_class": "OSError", "parsed": False, "complete": False,
                        "command": full}
        body = self._answer(command)
        marker = "" if any(t in command for t in self.truncate) else driver.MARKER + "\n"
        return {"ran": True, "exit_code": 0, "stdout": body + marker, "stderr": "",
                "error_class": "", "parsed": True,
                "complete": driver.MARKER in (body + marker), "command": full}

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
            return f"{CONTAINER} {CONTAINER_ID}\n" if self.container_present else ""
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
            return "LISTEN 0 128 127.0.0.1:8099 0.0.0.0:*\n"
        if command.startswith("grep -c"):
            needle = command.split("--")[1].split()[0] if "--" in command else ""
            return "1\n" if needle and needle in self.gateway_log else ""
        return ""


@pytest.fixture
def world(monkeypatch, tmp_path):
    """The driver, wired to a described world instead of to a network."""
    w = World()

    def fake_launch(argv, timeout=600.0):
        text = " ".join(str(a) for a in argv)
        if text.startswith("ssh") or "ssh" in str(argv[0]):
            command = str(argv[-1])
            answer = w.query(command.rsplit(";", 1)[0].strip())
            return answer["exit_code"], answer["stdout"], answer["stderr"], answer["error_class"]
        return w.launch(argv, timeout)

    def fake_query(command, timeout=300.0):
        return w.query(command, timeout)

    class Popen:
        """`remote run` for the long job, which the driver reads line by line."""

        def __init__(self, *a, **kw):
            import io as _io
            self.stdout = _io.StringIO(f"run: {RUN_ID}\nstill going\n")
            self.stderr = _io.StringIO("")
            w.container_present = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    class Saved:
        url, gateway_id, fingerprint = "http://127.0.0.1:8099", "gw", "fp"

        @property
        def token(self):
            return w.token

    monkeypatch.setattr(driver, "launch", fake_launch)
    monkeypatch.setattr(driver, "server_query", fake_query)
    monkeypatch.setattr(driver.subprocess, "Popen", Popen)
    monkeypatch.setattr(driver, "live_secrets", lambda: [])
    monkeypatch.setattr(driver, "connection", lambda: (Saved(), Saved()))
    monkeypatch.setattr(driver, "gateway_record", lambda run_id: {
        "run_id": run_id, "state": "finished", "exit_code": 0, "refusal": "",
        "cleanup_verified": True, "policy_deltas": [],
        "request_policy_sha256": "d" * 64, "effective_policy_sha256": "d" * 64,
        "requested_policy": {"network.enabled": False, "network.allowed_destinations": []},
        "effective_policy": {"network.enabled": False, "network.allowed_destinations": []},
    } if run_id else {"error": "the client never printed a run id"})
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

        def keep_it(argv, timeout=600.0):
            if "remote cancel" in " ".join(str(a) for a in argv):
                return 0, "cancelled\n", "", ""          # cancelled, and yet still there
            return real(argv, timeout)

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

    def test_the_verdict_is_not_a_constant(self, world, tmp_path):
        """Without this, every control above could be satisfied by a driver that always failed."""
        code, _steps = drive(tmp_path)
        assert code == 0


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
        ports = [line for line in lines if "8099" in line]
        assert ports == ['PORT = _setting("EM3C_GATEWAY_PORT", "8099")'], ports
        assert not [line for line in lines if "sudo -u em" in line]

    def test_every_setting_comes_from_the_environment(self, monkeypatch):
        import importlib

        monkeypatch.setenv("EM3C_SERVER", "someone@somewhere")
        monkeypatch.setenv("EM3C_GATEWAY_STATE", "/somewhere/state")
        reloaded = importlib.reload(driver)
        assert reloaded.SERVER == "someone@somewhere"
        assert reloaded.STATE == "/somewhere/state"
        monkeypatch.undo()
        importlib.reload(driver)


def test_the_record_carries_no_hand_built_dictionary(world, tmp_path):
    """The whole point of the move. Every step in this file's evidence came from the driver,
    through the recorder, through the file -- not from a literal written in a test."""
    _code, steps = drive(tmp_path)
    assert steps
    for raw in steps:
        assert raw.get("schema") == evidence.SCHEMA or "schema" not in raw
        assert isinstance(json.dumps(raw), str)
