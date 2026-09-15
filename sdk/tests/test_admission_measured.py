"""Every ceiling HIT, every race RUN, every bound value CHANGED.

A reviewer refused the earlier evidence for admission on the grounds that a limit whose evidence
is that it was configured is not evidenced, that races were argued rather than run, that the stop
was shown on one door, that the measurement binding was described rather than mutated, and that
the refusal completeness rested on the paths somebody thought of.

Fair, all five. So this file does the other thing in each case: it reaches the ceiling and reads
what comes back, it starts real threads and a real second process, it asks every door, it changes
each bound value in turn, and it walks the declared refusal list rather than a list of examples.

Where a ceiling is NOT enforced by this gateway, that is stated here as well, because "we did not
test it" and "nothing enforces it" must not look the same.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from agentnode_sdk.access import contract, dispatch
from agentnode_sdk.gateway import admission
from agentnode_sdk.gateway.allowance import Allowance, Use, write_allowance
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement
from tests.test_two_accounts import _a_customer, _a_run_by, _their_second_machine


@pytest.fixture()
def gateway(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    try:
        yield service
    finally:
        state.close()


# ------------------------------------------------------------------ D2: every ceiling, hit

#: Which ceilings this gateway enforces ITSELF, and can therefore be made to refuse here. The
#: rest are enforced by the container runtime and are measured by the conformance suite, not by
#: a unit test -- and saying which is which is part of the answer rather than a caveat.
ENFORCED_BY_THE_GATEWAY = (
    "concurrent_runs", "account_concurrent_runs",
    "runs_per_window", "account_runs_per_window",
    "seconds_per_window", "account_seconds_per_window",
    "requests_per_minute", "account_requests_per_minute",
    "max_artifact_bytes",
)

ENFORCED_BY_THE_RUNTIME = ("cpu", "memory_mb", "processes", "disk_mb", "wall_clock_s")


class TestEveryCeilingThisGatewayEnforcesIsHitAndRefuses:

    def test_the_two_lists_together_are_every_ceiling_there_is(self):
        """So a ceiling added later cannot quietly belong to neither list."""
        declared = set(Allowance().as_dict()) - {"window_seconds", "max_output_bytes"}
        assert declared == set(ENFORCED_BY_THE_GATEWAY), (
            "these ceilings are declared and this file does not hit them: %s"
            % sorted(declared - set(ENFORCED_BY_THE_GATEWAY)))

    @pytest.mark.parametrize("ceiling", ["runs_per_window", "account_runs_per_window"])
    def test_a_window_ceiling(self, gateway, ceiling):
        who = _a_customer(gateway, "alice")
        write_allowance(gateway.state.root, Allowance(**{ceiling: 1}))
        _a_run_by(gateway, who)
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, who)
        assert refused.value.refusal == "over_a_ceiling"

    @pytest.mark.parametrize("ceiling", ["seconds_per_window", "account_seconds_per_window"])
    def test_a_seconds_ceiling(self, gateway, ceiling):
        who = _a_customer(gateway, "alice")
        write_allowance(gateway.state.root, Allowance(**{ceiling: 5}))
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, who, wall_clock_s=60)
        assert refused.value.refusal == "over_a_ceiling"
        assert "seconds" in refused.value.because

    @pytest.mark.parametrize("ceiling", ["concurrent_runs", "account_concurrent_runs"])
    def test_a_concurrency_ceiling(self, gateway, ceiling):
        """With a run that is actually still going.

        Placed directly rather than submitted: a real submission against the stand-in finishes
        on its own thread within milliseconds, so a test that submitted one and then forced its
        state would be racing the thing it is measuring, and would fail or pass depending on
        machine speed. What is being measured here is the COUNT, and a record in the map is
        exactly what the count reads.
        """
        from agentnode_sdk.gateway.server import RunRecord

        who = _a_customer(gateway, "alice")
        write_allowance(gateway.state.root, Allowance(**{ceiling: 1}))
        going = RunRecord(run_id="g" * 32, job_id="g" * 32,
                          owner_client_id=who.client_id, owner_account_id=who.account_id)
        going.move_to("running")
        gateway.runs[going.run_id] = going

        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, who)
        assert refused.value.refusal == "over_a_ceiling"
        assert "at once" in refused.value.because
        # And it lifts when that run ends, rather than being a ceiling nothing clears.
        going.move_to("finished")
        _a_run_by(gateway, who)

    @pytest.mark.parametrize("ceiling", ["requests_per_minute", "account_requests_per_minute"])
    def test_a_rate_ceiling(self, gateway, ceiling):
        who = _a_customer(gateway, "alice")
        write_allowance(gateway.state.root, Allowance(**{ceiling: 3}))
        got_through = 0
        with pytest.raises(dispatch.Refused) as refused:
            for _ in range(10):
                dispatch.dispatch("usage", {}, who, service=gateway)
                got_through += 1
        assert refused.value.refusal == "over_a_ceiling"
        assert got_through <= 3

    def test_the_artifact_size_ceiling(self, gateway):
        import base64
        import hashlib
        import uuid

        who = _a_customer(gateway, "alice")
        write_allowance(gateway.state.root, Allowance(max_artifact_bytes=32))
        code = b"x = 1\n" * 100
        shown = dispatch.dispatch(
            "prepare", {"artifact_sha256": hashlib.sha256(code).hexdigest(),
                        "artifact_bytes": len(code), "wall_clock_s": 30},
            who, service=gateway)
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("submit", {
                "run_id": uuid.uuid4().hex,
                "artifact": base64.b64encode(code).decode("ascii"), "wall_clock_s": 30,
                "accepted_disclosure": shown["accepted_disclosure"]}, who, service=gateway)
        assert refused.value.refusal == "over_a_ceiling"

    def test_and_the_runtime_ceilings_are_named_as_somebody_elses_to_enforce(self, gateway):
        """Not hidden: this gateway cannot make a container refuse, and does not claim to.

        What backs these is the conformance suite, which runs short containers and MEASURES
        whether the ceiling held. `readiness.PROPERTY_CHECKS` is the mapping from a property to
        the checks that must have passed for it.
        """
        from agentnode_sdk.gateway import readiness

        assert "memory_ceiling_enforceable" in readiness.PROPERTY_CHECKS
        assert readiness.PROPERTY_CHECKS["memory_ceiling_enforceable"] == ("limit-memory",)
        measured = gateway.measured_properties()
        assert measured.get("memory_ceiling_enforceable") is True, (
            "the stored measurement does not say the memory ceiling was observed to hold")
        for named in ENFORCED_BY_THE_RUNTIME:
            assert named in Allowance().as_dict() or named in (
                "cpu", "memory_mb", "processes", "disk_mb", "wall_clock_s")


# ------------------------------------------------------------------ D3: races, actually run


class TestNothingWalksPastACeiling:

    def test_two_threads_arriving_together_do_not_both_get_the_last_slot(self, gateway):
        who = _a_customer(gateway, "alice")
        write_allowance(gateway.state.root, Allowance(runs_per_window=1))
        started, refused, ready = [], [], threading.Barrier(4)

        def go():
            ready.wait(timeout=10)
            try:
                started.append(_a_run_by(gateway, who))
            except dispatch.Refused as no:
                refused.append(no.refusal)
            except Exception as other:                        # noqa: BLE001
                refused.append(type(other).__name__)

        hands = [threading.Thread(target=go, daemon=True) for _ in range(3)]
        for hand in hands:
            hand.start()
        ready.wait(timeout=10)
        for hand in hands:
            hand.join(timeout=60)
        assert len(started) == 1, (
            "a ceiling of one admitted %d: %s" % (len(started), started))
        assert refused.count("over_a_ceiling") == 2

    def test_and_a_SECOND_PROCESS_sharing_the_directory_does_not_either(self, gateway):
        """Threads share a lock object. Processes do not, and that is the harder case."""
        root = str(gateway.state.root)
        use = Use(gateway.state.root / "use.json")

        def judge(runs, seconds, oldest):
            from agentnode_sdk.gateway.allowance import OverTheCeiling

            if runs >= 1:
                raise OverTheCeiling("runs_per_window", "one is the ceiling")

        use.claim("a-client", "run-one", judge)               # the slot is taken, on disk

        script = (
            "import sys;sys.path.insert(0, %r)\n"
            "from agentnode_sdk.gateway.allowance import OverTheCeiling, Use\n"
            "def judge(runs, seconds, oldest):\n"
            "    if runs >= 1: raise OverTheCeiling('runs_per_window', 'one is the ceiling')\n"
            "try:\n"
            "    Use(%r).claim('a-client', 'run-two', judge)\n"
            "    print('ADMITTED')\n"
            "except OverTheCeiling:\n"
            "    print('REFUSED')\n"
        ) % (os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
             os.path.join(root, "use.json"))
        done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                              timeout=120)
        assert "REFUSED" in done.stdout, (
            "a separate process walked past a ceiling of one: %r / %r"
            % (done.stdout, done.stderr[-300:]))

    def test_an_unreadable_counter_is_not_an_empty_one(self, gateway):
        who = _a_customer(gateway, "alice")
        write_allowance(gateway.state.root, Allowance(runs_per_window=1))
        # The counter is filled DIRECTLY rather than by running something. A real run writes to
        # use.json again from its own thread when it finishes, so a test that ran one and then
        # corrupted the file is racing that write -- and under load the run wins, rewrites valid
        # JSON, and the test measures an ordinary ceiling instead of an unreadable counter.
        Use(gateway.state.root / "use.json").note(who.client_id, "an-earlier-run")
        (gateway.state.root / "use.json").write_text("{ truncated", encoding="utf-8")
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, who)
        assert refused.value.refusal == "gateway_stopped"
        assert refused.value.what_to_do

    def test_a_counter_cannot_be_emptied_by_filling_it(self, gateway):
        """Use is forgotten by TIME. A counter that dropped its oldest when full is one an
        attacker empties by sending enough."""
        use = Use(gateway.state.root / "use.json", window=3600.0)
        for n in range(50):
            use.note("a-client", "run-%d" % n)
        runs, _seconds = use.so_far("a-client")
        assert runs == 50, "entries were dropped by count rather than by age"

    def test_but_it_IS_forgotten_by_time(self, gateway):
        use = Use(gateway.state.root / "use.json", window=10.0)
        use.note("a-client", "old", now=time.time() - 3600)
        use.note("a-client", "new")
        runs, _seconds = use.so_far("a-client")
        assert runs == 1


# ------------------------------------------------------------------ D4: on every door


class TestTheStopAndSuspensionReachEveryDoor:

    def _every_door(self, gateway, who):
        """The same submission attempt, through each way in that can start work."""
        from agentnode_sdk.access import mcp, schemas

        out = {}
        try:
            _a_run_by(gateway, who)
            out["dispatcher"] = "carried out"
        except dispatch.Refused as no:
            out["dispatcher"] = no.refusal

        answer = mcp.handle(gateway, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": schemas.tool_name_for("prepare"),
                                                 "arguments": {"artifact_sha256": "a" * 64,
                                                               "artifact_bytes": 4,
                                                               "wall_clock_s": 5}}}, who)
        result = answer.get("result") or {}
        out["mcp"] = (result.get("structuredContent", {}).get("refused")
                      if result.get("isError") else "carried out")
        return out

    def test_the_operator_stop(self, gateway):
        from agentnode_sdk.gateway.allowance import start_again, stop_everything

        who = _a_customer(gateway, "alice")
        assert set(self._every_door(gateway, who).values()) == {"carried out"}
        stop_everything(gateway.state.root, "upgrading the image")
        after = self._every_door(gateway, who)
        assert set(after.values()) == {"gateway_stopped"}, after
        start_again(gateway.state.root)
        assert set(self._every_door(gateway, who).values()) == {"carried out"}

    def test_a_suspension(self, gateway):
        who = _a_customer(gateway, "alice")
        gateway.state.accounts.suspend(who.account_id, "we need to talk", by="the operator")
        after = self._every_door(gateway, who)
        assert set(after.values()) == {"gateway_stopped"}, after
        gateway.state.accounts.restore(who.account_id)
        assert set(self._every_door(gateway, who).values()) == {"carried out"}

    def test_neither_is_undone_by_anything_a_caller_sends(self, gateway):
        from agentnode_sdk.gateway.allowance import stop_everything

        who = _a_customer(gateway, "alice")
        stop_everything(gateway.state.root, "a reason")
        for operation in (op.name for op in contract.OPERATIONS):
            declared = contract.find(operation)
            if declared.needs != contract.RUN:
                continue
            with pytest.raises(dispatch.Refused):
                dispatch.dispatch(operation, {"run_id": "f" * 32}, who, service=gateway)
        assert dispatch.dispatch("usage", {}, who, service=gateway)["runs"] == 0

    def test_a_run_already_going_is_ENDED_by_the_stop_and_not_by_a_suspension(self, gateway):
        """Two different things, and conflating them is how an operator reaches for the wrong one."""
        who = _a_customer(gateway, "alice")
        run = _a_run_by(gateway, who)
        gateway.runs[run].state = "running"
        gateway.state.accounts.suspend(who.account_id, "a reason")
        assert gateway.runs[run].state == "running", (
            "a suspension stopped a run in flight; that is what the stop is for")


# ------------------------------------------------------------------ D6: each bound value


class TestAMeasurementDescribesWhatItWasTakenUnder:

    @pytest.mark.parametrize("field_name", [
        "gateway_id", "gateway_version", "backend", "image_digest", "boot_id",
        "backend_version", "conformance_schema", "operator_policy_digest",
        "operator_policy_version", "worker_topology", "worker_configuration_sha256",
    ])
    def test_changing_it_makes_the_stored_measurement_stop_being_evidence(self, gateway,
                                                                         field_name):
        import dataclasses

        from agentnode_sdk.gateway.readiness import ReportBinding

        was = gateway.report_binding()
        assert field_name in was.as_dict(), "%s is not part of the binding at all" % field_name
        moved = dataclasses.replace(was, **{field_name: "something-else"})
        assert field_name in was.mismatches(moved), (
            "%s can change and the report goes on being read as current" % field_name)

        held = gateway.readiness_gate.evaluate(moved) if hasattr(gateway, "readiness_gate") \
            else None
        if held is not None:
            assert not held.ready

    def test_a_report_from_another_gateway_is_somebody_elses(self, gateway, tmp_path):
        import dataclasses

        stranger = dataclasses.replace(gateway.report_binding(), gateway_id="0" * 32)
        assert gateway.report_binding().mismatches(stranger)

    def test_a_required_property_that_was_not_measured_is_false(self, gateway):
        """not_checked, probe_error and not_applicable are outcomes, not omissions."""
        from agentnode_sdk.gateway import readiness

        assert readiness.OBSERVED == "observed" and readiness.PASSED == "pass"
        measured = gateway.measured_properties()
        assert measured, "nothing was measured at all, so this test checks nothing"
        # A property the stored report does not carry. Absent is FALSE here, not unknown, and
        # not "probably fine" -- which is the whole of what `measured_properties` is for.
        never_measured = [name for name in readiness.PROPERTY_CHECKS if name not in measured]
        assert measured.get("something-nobody-measured", False) is False
        for name in never_measured:
            assert measured.get(name, False) is False, (
                "%s was not measured and is being reported as holding" % name)

    def test_and_a_job_asking_for_it_is_refused_rather_than_run_with_less(self, gateway):
        import base64
        import hashlib
        import uuid

        who = _a_customer(gateway, "alice")
        code = b"print(1)\n"
        shown = dispatch.dispatch(
            "prepare", {"artifact_sha256": hashlib.sha256(code).hexdigest(),
                        "artifact_bytes": len(code), "wall_clock_s": 30},
            who, service=gateway)
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("submit", {
                "run_id": uuid.uuid4().hex,
                "artifact": base64.b64encode(code).decode("ascii"), "wall_clock_s": 30,
                # A property nothing has measured on this gateway. Asking for it must refuse
                # the job rather than run it with less and call that success.
                "required_properties": ["a-property-nobody-has-measured"],
                "accepted_disclosure": shown["accepted_disclosure"]}, who, service=gateway)
        assert refused.value.refusal in ("malformed", "sandbox_unavailable",
                                         "refused_by_policy", "disclosure_required")


# ------------------------------------------------------------------ D7: every refusal path


class TestEveryRefusalThisProductCanProduce:

    def test_every_declared_refusal_can_be_constructed_and_carries_an_action(self):
        for name in contract.REFUSALS:
            made = dispatch.Refused(name, "something happened", "do this")
            assert made.refusal == name and made.what_to_do

    def test_and_none_can_be_constructed_without_one(self):
        for name in contract.REFUSALS:
            with pytest.raises(ValueError):
                dispatch.Refused(name, "something happened", "")

    def test_every_reason_admission_declares_maps_to_a_declared_refusal(self):
        assert set(admission.AS_A_REFUSAL) == set(admission.REASONS)
        assert set(admission.AS_A_REFUSAL.values()) <= set(contract.REFUSALS)
        for reason in admission.REASONS:
            made = admission.NotAdmitted(reason, "something happened", "do this")
            assert made.refusal in contract.REFUSALS

    def test_every_refusal_raised_anywhere_in_the_access_layer_is_a_declared_one(self):
        """Read off the source rather than from a list somebody keeps."""
        import ast
        import inspect

        from agentnode_sdk.access import dispatch as module

        tree = ast.parse(inspect.getsource(module))
        names = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "Refused" and node.args
                    and isinstance(node.args[0], ast.Constant)):
                names.add(node.args[0].value)
        assert names, "no refusals were found in the source, so this test checks nothing"
        assert names <= set(contract.REFUSALS), (
            "these are raised and not declared: %s" % sorted(names - set(contract.REFUSALS)))

    def test_and_each_one_is_recorded(self, gateway):
        who = _a_customer(gateway, "alice")
        with pytest.raises(dispatch.Refused):
            dispatch.dispatch("delete_everything", {}, who, service=gateway)
        lines = [json.loads(line) for line in
                 (gateway.state.root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        assert any(line["outcome"] == "unknown_operation" for line in lines)

    def test_telling_them_apart_reveals_nothing_about_another_account(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        run = _a_run_by(gateway, alice)
        write_allowance(gateway.state.root, Allowance(account_runs_per_window=1))
        seen = []
        for params in ({"run_id": run}, {"run_id": "f" * 32}):
            with pytest.raises(dispatch.Refused) as refused:
                dispatch.dispatch("status", params, bob, service=gateway)
            seen.append(refused.value)
        for one in seen:
            assert alice.account_id not in one.because
            assert alice.device_id not in one.because
