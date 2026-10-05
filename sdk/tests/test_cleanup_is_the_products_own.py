"""Cleanup is the product's own, a migration waits for it, and a closed policy is never blocked.

Two frozen profiles are held here, and both exist because an independent review returned BLOCK on the
previous arc:

  * `frozen/cleanup.json` (CU1..CU17) -- EG12 and F37. A failed boundary measurement left four egress
    proxies in state `Stopping`; the account that owned them could not remove them, the product's own
    sweep reported them as removed anyway, removing their networks left one resolver entry each,
    `aardvark-dns` then refused to start at all, and the egress proxy -- fail-closed on a resolve
    failure -- answered `403` for a host on its own allowlist. The cause was an ORDERING: the product
    rebuilt the rootless namespace before it reconciled, and the rebuild is what costs the account the
    ability to remove its own containers.
  * `frozen/activation.json` (PA1..PA7) -- F19 and F38, and `E0424`, which is the previous round's own
    parking of the control plane: `gateway egress --none` could not be measured because the worker was
    already down, so the host was parked with an allowlist still in force.

Every test here DRIVES the behaviour. The suite already learned that lesson the expensive way: "taking
a mechanism out and asking the suite showed the difference plainly -- removing the memory
classification entirely turned NOTHING red". So the doubles here can represent a removal, a refusal and
an unreadable answer, because a double that cannot do those cannot test a read-back.
"""
from __future__ import annotations

import subprocess

import pytest

from agentnode_sdk.sandbox import egress
from agentnode_sdk.sandbox.container_backend import ABSENT, PRESENT, UNKNOWN


class _Runtime:
    """A runtime that owns things, lets them be removed, and can refuse or answer unreadably.

    `refuses` names resources whose removal fails the way the real one failed in F37. `garbles` makes
    the next listing answer with a line that is not a row, which is the other half of CU7.
    """

    def __init__(self, containers=(), networks=(), refuses=(), garbles=False,
                 component="egress", run_label="", docker_like=False):
        self.containers = list(containers)
        self.networks = list(networks)
        self.refuses = set(refuses)
        self.garbles = garbles
        self.component = component
        self.run_label = run_label
        #: Answer `ps --format` the way DOCKER does: `.Labels` is a comma-separated string there, so a
        #: template that indexes it as a map is an error and the whole command exits non-zero. Podman
        #: accepts it. A runtime double that accepts both cannot tell the two apart, and the Linux lane
        #: could -- see test_the_inventory_asks_a_question_both_runtimes_answer.
        self.docker_like = docker_like
        self.calls: list = []

    # -- what a listing answers -------------------------------------------------
    def _filter_name(self, argv):
        for part in argv:
            if str(part).startswith("name="):
                return str(part).split("=", 1)[1]
        return ""

    def _rows(self, names, argv, labelled: bool):
        wanted = self._filter_name(argv)
        if wanted:
            names = [n for n in names if n == wanted]
        if labelled:
            rows = ["%s %s %s" % (n, self.component, self.run_label) for n in names]
        else:
            rows = list(names)
        if self.garbles:
            rows.append('time="2026-10-05T00:00:00Z" level=notice msg="something else entirely"')
        return "".join(r + "\n" for r in rows)

    def __call__(self, argv, timeout=30.0, **kw):
        argv = list(argv)
        self.calls.append(argv)
        labelled = any("label=" in str(p) for p in argv)
        if argv[1:3] == ["network", "ls"]:
            return self._cp(self._rows(self.networks, argv, labelled=False))
        if argv[1:3] == ["network", "inspect"]:
            import json

            return self._cp(json.dumps({"id": argv[3], "internal": True, "dns_enabled": False,
                                        "ipv6_enabled": False, "subnets": [],
                                        "labels": {"agentnode.component": self.component,
                                                   "agentnode.run": self.run_label}}))
        if argv[1:3] == ["ps", "-a"]:
            if self.docker_like and any("index .Labels" in str(part) for part in argv):
                raise subprocess.CalledProcessError(
                    1, argv, output="",
                    stderr=('template parsing error: template: :1:8: executing "" at <.Labels>: '
                            "can't give argument to non-function .Labels"))
            return self._cp(self._rows(self.containers, argv, labelled=False))
        if argv[1] == "inspect":
            # What both runtimes answer for a container: `.Config.Labels` is a mapping in each.
            import json

            return self._cp(json.dumps({"agentnode.component": self.component,
                                        "agentnode.run": self.run_label}))
        if argv[1:3] == ["network", "rm"]:
            return self._cp(self._remove(self.networks, argv[-1]))
        if argv[1] == "rm":
            return self._cp(self._remove(self.containers, argv[-1]))
        return self._cp("")

    def _remove(self, where, name):
        if name in self.refuses:
            raise subprocess.CalledProcessError(
                125, ["podman", "rm", "-f", name], output="",
                stderr=("Error: cannot remove container %s as it could not be stopped: sending "
                        "SIGKILL to container %s: operation not permitted" % (name, name)))
        if name in where:
            where.remove(name)
        return ""

    @staticmethod
    def _cp(out):
        class _CP:
            stdout = out
            stderr = ""
            returncode = 0
        return _CP()


@pytest.fixture()
def no_resolver(monkeypatch):
    """No resolver entries unless a test asks for them."""
    # Two channels, because the real one has two: the entries, and what could not be read. A fixture
    # that answered only the first is how an unreadable directory used to look like an empty one.
    monkeypatch.setattr(egress, "_resolver_entries", lambda runtime="": ([], []))


class TestAReadBackIsTheAnswer:
    """CU6: a removal is done when the runtime says the thing is gone, not when `rm` exits zero."""

    def test_a_removal_the_runtime_refused_is_not_reported_as_removed(self, monkeypatch,
                                                                     no_resolver):
        rt = _Runtime(containers=["c57c6b000001"], refuses=["c57c6b000001"])
        monkeypatch.setattr(egress, "_run", rt)
        got = egress.remove_everything_of_ours("podman")
        assert got["removed"] == [], got
        assert [x["name"] for x in got["failed"]] == ["c57c6b000001"], got
        assert got["clean"] is False
        # the runtime's own words travel with it, because "it failed" is not actionable
        assert "operation not permitted" in got["failed"][0]["why"]
        assert got["failed"][0]["state"] == PRESENT

    def test_a_removal_that_worked_is_reported_once_and_the_thing_is_gone(self, monkeypatch,
                                                                         no_resolver):
        rt = _Runtime(containers=["c1d100000001"], networks=["e7e100000001"])
        monkeypatch.setattr(egress, "_run", rt)
        got = egress.remove_everything_of_ours("podman")
        assert sorted(x["name"] for x in got["removed"]) == ["c1d100000001", "e7e100000001"], got
        assert got["failed"] == []
        assert got["clean"] is True
        assert rt.containers == [] and rt.networks == []

    def test_the_old_shape_still_answers_for_callers_that_only_read_it(self, monkeypatch,
                                                                      no_resolver):
        rt = _Runtime(containers=["c1d100000001"], networks=["e7e100000001"])
        monkeypatch.setattr(egress, "_run", rt)
        got = egress.remove_what_no_run_is_waiting_for("podman")
        assert got["containers"] == ["c1d100000001"] and got["networks"] == ["e7e100000001"]
        assert got["clean"] is True


class TestUnreadableIsNotEmpty:
    """CU7, and this one is here because a check of my own made exactly this mistake."""

    def test_a_listing_with_a_line_that_is_not_a_row_is_unknown(self, monkeypatch, no_resolver):
        rt = _Runtime(containers=[], networks=[], garbles=True)
        monkeypatch.setattr(egress, "_run", rt)
        clean, inventory = egress.nothing_of_ours_is_left("podman")
        assert inventory["containers"] == []
        assert inventory["unreadable"], inventory
        assert clean is False, "an answer that could not be read is not an empty answer"

    def test_and_a_clean_host_says_clean(self, monkeypatch, no_resolver):
        rt = _Runtime()
        monkeypatch.setattr(egress, "_run", rt)
        clean, inventory = egress.nothing_of_ours_is_left("podman")
        assert clean is True and inventory["unreadable"] == []

    def test_one_resource_state_can_be_unknown_without_being_absent(self, monkeypatch):
        def _raises(argv, timeout=30.0, **kw):
            raise OSError("the runtime is not there")

        monkeypatch.setattr(egress, "_run", _raises)
        assert egress.state_of("podman", "container", "whatever") == UNKNOWN


class TestTheResolverEntriesAreCleanedToo:
    """CU9. Nothing in the product mentioned these files before F37, and one of them stops the
    resolver starting at all."""

    def _entries(self, tmp_path, *names):
        where = tmp_path / "containers" / "networks" / "aardvark-dns"
        where.mkdir(parents=True)
        for name in names:
            (where / name).write_text("10.89.1.1\n", encoding="utf-8")
        (where / "aardvark.pid").write_text("1\n", encoding="utf-8")
        return where

    def test_only_entries_this_module_names_are_seen(self, tmp_path, monkeypatch):
        self._entries(tmp_path, "agentnode-egress-abcd1234-int", "somebody-elses-network")
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        seen = [name for name, _p in egress._resolver_entries()[0]]
        assert seen == ["agentnode-egress-abcd1234-int"], seen

    def test_an_entry_whose_network_is_gone_is_removed(self, tmp_path, monkeypatch):
        where = self._entries(tmp_path, "agentnode-egress-abcd1234-ext")
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        rt = _Runtime(containers=[], networks=[])
        monkeypatch.setattr(egress, "_run", rt)
        got = egress.remove_everything_of_ours("podman")
        assert [x["name"] for x in got["removed"]] == ["agentnode-egress-abcd1234-ext"], got
        assert not (where / "agentnode-egress-abcd1234-ext").exists()
        assert (where / "aardvark.pid").exists(), "the resolver's own pid file is not ours to remove"

    def test_an_entry_whose_network_still_exists_is_left_alone(self, tmp_path, monkeypatch):
        where = self._entries(tmp_path, "agentnode-egress-abcd1234-int")
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        # The network is still there, so the entry is not a leftover: removing it would take the
        # resolver away from a live network.
        rt = _Runtime(networks=["agentnode-egress-abcd1234-int"])
        monkeypatch.setattr(egress, "_run", rt)
        rt.networks = ["agentnode-egress-abcd1234-int"]
        egress.remove_everything_of_ours("podman", keep_runs=[""])
        # `or True` was here, which is an assertion that cannot fail -- the exact shape this project
        # has a standing rule about. The claim is that the entry of a LIVE network survives.
        assert (where / "agentnode-egress-abcd1234-int").exists(), (
            "the resolver entry of a LIVE network was removed, which is a second reconciliation "
            "changing an already clean state")
        # and the inventory counts it while it is there
        monkeypatch.setattr(egress, "_run", _Runtime(networks=["agentnode-egress-abcd1234-int"]))
        inventory = egress.what_is_left_of_ours("podman")
        assert [e["name"] for e in inventory["resolver_entries"]] == [
            "agentnode-egress-abcd1234-int"]


class TestTheTeardownSaysWhatHappened:
    """CU10 and CU13: one cleanup path for every ending, and it reports rather than swallowing."""

    def test_it_reports_each_resource(self, monkeypatch, no_resolver):
        rt = _Runtime(containers=["proxy-1"], networks=["int-1", "ext-1"])
        monkeypatch.setattr(egress, "_run", rt)
        said = egress._teardown("podman", "proxy-1", ["int-1", "ext-1"])
        assert said["complete"] is True
        assert sorted(x["name"] for x in said["resources"]) == ["ext-1", "int-1", "proxy-1"]

    def test_a_refusal_makes_it_incomplete_and_names_what_is_left(self, monkeypatch, no_resolver):
        rt = _Runtime(containers=["proxy-1"], networks=["int-1"], refuses=["proxy-1"])
        monkeypatch.setattr(egress, "_run", rt)
        said = egress._teardown("podman", "proxy-1", ["int-1"])
        assert said["complete"] is False
        assert said["still_there"] == ["proxy-1"]

    def test_it_is_idempotent(self, monkeypatch, no_resolver):
        """CU12: a second teardown removes nothing and still says complete."""
        rt = _Runtime(containers=["proxy-1"], networks=["int-1"])
        monkeypatch.setattr(egress, "_run", rt)
        first = egress._teardown("podman", "proxy-1", ["int-1"])
        second = egress._teardown("podman", "proxy-1", ["int-1"])
        assert first["complete"] is True and second["complete"] is True
        assert second["still_there"] == []


class TestAMigrationWaitsForTheCleanup:
    """CU1, CU2, CU3 and CU8, driven. This is the ordering EG12 failed on."""

    class _Worker:
        ITS_OWN_PREFIXES = ("agentnode-run-",)

        def __init__(self, clean=True, egress_clean=True, failed=()):
            self.clean = clean
            self.egress_clean = egress_clean
            self.failed = list(failed)
            self.asked = 0

        def remove_what_a_previous_worker_left(self):
            self.asked += 1
            return {"runtime": "podman", "found": [], "removed": [],
                    "failed": self.failed, "unreadable": [],
                    "egress": {"asked": True, "clean": self.egress_clean, "failed": [],
                               "removed": [], "unreadable": []},
                    "clean": bool(self.clean), "why": ""}

    def test_it_does_not_rebuild_while_anything_of_ours_is_left(self):
        from agentnode_sdk.worker import reconcile as rc

        tried: list = []
        worker = self._Worker(clean=False, failed=[{"name": "c57c6b000001", "state": PRESENT,
                                                    "why": "operation not permitted"}])
        got = rc.reconcile_then_rebuild(worker, rebuild=lambda rt: tried.append(rt) or True)
        assert tried == [], "it migrated while something of ours was still there"
        assert got.clean is False and got.rebuilt is False
        assert "c57c6b000001" in got.why and "operation not permitted" in got.why

    def test_it_rebuilds_on_a_clean_answer_and_asks_again_afterwards(self):
        from agentnode_sdk.worker import reconcile as rc

        tried: list = []
        worker = self._Worker(clean=True)
        got = rc.reconcile_then_rebuild(worker, rebuild=lambda rt: tried.append(rt) or True)
        assert tried == ["podman"]
        assert got.clean is True and got.rebuilt is True
        assert worker.asked == 2, "the inventory has to be taken again after the rebuild (CU8)"

    def test_a_rebuild_that_did_not_work_is_not_clean(self):
        from agentnode_sdk.worker import reconcile as rc

        got = rc.reconcile_then_rebuild(self._Worker(clean=True), rebuild=lambda rt: False)
        assert got.clean is False and got.rebuilt is False
        assert "would not rebuild" in got.why

    def test_the_refusal_is_a_cleanup_state_and_not_a_policy_one(self):
        """CU5. In F37 the symptom reached an operator as a 403 from the egress proxy, which is
        indistinguishable from an allowlist refusal. The words this refusal may not use are the
        words of a policy decision, and the sentence it DOES use is produced by the product rather
        than written out in the test -- so a change to that sentence is caught here."""
        from agentnode_sdk.worker import reconcile as rc

        worker = TestAMigrationWaitsForTheCleanup._Worker(
            clean=False, failed=[{"kind": "container", "name": "c57c6b000001", "state": PRESENT,
                                  "why": "operation not permitted"}])
        said = rc.reconcile(worker)
        assert said.clean is False
        assert "c57c6b000001" in said.why and "operation not permitted" in said.why
        for word in ("403", "forbidden", "allowlist", "destination", "policy"):
            assert word not in said.why.lower(), said.why


class TestTheWorkerRefusesRatherThanServing:
    """CU3 and CU4, read where they are decided: `serve` raises before it opens anything."""

    def test_serve_reconciles_first_and_raises_on_leftovers(self):
        import inspect

        from agentnode_sdk.worker import service

        source = inspect.getsource(service.serve)
        assert "reconcile(the_worker)" in source
        assert "raise LeftoversRemain(swept.why, swept.report)" in source
        # before the ceiling proof, before the rebuild, and before anything listens
        assert source.index("reconcile(the_worker)") < source.index("prove_its_ceilings()")
        assert source.index("raise LeftoversRemain") < source.index("serve_forever")

    def test_the_old_advisory_sweep_is_gone(self):
        import inspect

        from agentnode_sdk.worker import service

        source = inspect.getsource(service.serve)
        assert "Could not account for every leftover" not in source, (
            "the sweep that reported what it could not do and carried on is back")


class TestClosingAPolicyIsNeverBlocked:
    """PA2 of frozen/activation.json, driven against the transaction."""

    def test_a_policy_that_grants_nothing_is_recognised(self):
        from agentnode_sdk.gateway import operator_policy as opol
        from agentnode_sdk.gateway.server import GatewayService

        closed = opol.build(opol.NONE, (), {}, None)
        restricted = opol.build(opol.RESTRICTED, ("pypi.org",), {}, None)
        assert GatewayService._grants_nothing(closed) is True
        assert GatewayService._grants_nothing(restricted) is False

    def test_the_verdict_can_say_in_force_without_saying_ready(self):
        from agentnode_sdk.gateway.readiness import Readiness

        said = Readiness(ready=False, reason="not measured", in_force=True)
        assert said.ready is False and said.in_force is True
        assert said.as_dict()["in_force"] is True

    def test_the_command_does_not_say_nothing_was_changed_when_something_was(self):
        import inspect

        from agentnode_sdk.cli import gateway_commands

        source = inspect.getsource(gateway_commands)
        in_force = source.index('if not verdict.ready and getattr(verdict, "in_force", False):')
        nothing = source.index("The previous policy remains in force. Nothing was changed.",
                               in_force)
        between = source[in_force:nothing]
        assert "grants nothing, and it is now in force" in between, (
            "the in-force branch has to come before the one that says nothing was changed")


class TestContentionIsNotAFailedMeasurement:
    """PA5. F38 measured the lock being held by the measuring process itself."""

    def test_an_activation_error_is_not_published_as_a_verdict_about_the_worker(self):
        import inspect

        from agentnode_sdk.gateway import health

        # Counter-check 15 stayed GREEN against the first version of this, which asked whether the
        # word "ActivationError" appears -- and it does, in the comment that explains the branch.
        # The test now reads the CODE: the isinstance check and the early return.
        source = inspect.getsource(health.HealthWatch.remeasure_if_needed)
        assert "isinstance(failed, ActivationError)" in source, (
            "a contended change is published as a verdict about the worker again")
        assert (source.index("isinstance(failed, ActivationError)")
                < source.index("MEASUREMENT_FAILED"))

    def test_and_the_doctor_reports_what_admission_uses(self):
        """Counter-check 14 stayed GREEN against the first version of this test, which asked whether
        `service.readiness_now()` appears in `cmd_doctor` at all -- and it does, in the branch that
        runs WITHOUT `--measure`. A test that a mutation cannot break is not a test. The claim is
        about the MEASURE branch, so the measure branch is what is read."""
        import inspect

        from agentnode_sdk.cli import gateway_commands

        source = inspect.getsource(gateway_commands.cmd_doctor)
        start = source.index("proved = service.measure()")
        end = source.index("    else:", start)
        measure_branch = source[start:end]
        assert "readiness = service.readiness_now()" in measure_branch, (
            "after measuring, the doctor reports what the measurement proved rather than what "
            "admission uses, which is how it exited 0 while every submission was refused")


class TestTheWorkerRefusesForReal:
    """CU3 and CU4 driven rather than read: `serve` raises before anything else happens."""

    class _Reached(RuntimeError):
        pass

    class _Worker:
        ITS_OWN_PREFIXES = ("agentnode-run-",)

        def remove_what_a_previous_worker_left(self):
            return {"runtime": "podman", "found": ["agentnode-run-stuck"], "removed": [],
                    "failed": [{"kind": "container", "name": "agentnode-run-stuck",
                                "state": PRESENT, "why": "operation not permitted"}],
                    "unreadable": [],
                    "egress": {"asked": True, "clean": True, "failed": [], "removed": [],
                               "unreadable": []},
                    "clean": False, "why": ""}

        def prove_its_ceilings(self):
            raise TestTheWorkerRefusesForReal._Reached("the ceiling proof was reached")

    def test_it_refuses_before_it_proves_a_ceiling_or_rebuilds_anything(self, tmp_path):
        from agentnode_sdk.worker import service
        from agentnode_sdk.worker.reconcile import LeftoversRemain

        # A door is required before any of this, and rightly: a worker nobody can reach is not one.
        # The reconciliation comes after that validation and before everything else, so nothing has
        # been created when it refuses -- no socket file, no ceiling probe, no namespace rebuild.
        door = str(tmp_path / "worker.sock")
        with pytest.raises(LeftoversRemain) as refused:
            service.serve(address=door, key_path=str(tmp_path / "key"), only_uid=1000,
                          worker=self._Worker())
        assert not (tmp_path / "worker.sock").exists(), "it opened a door before refusing"
        assert "agentnode-run-stuck" in str(refused.value)
        assert "operation not permitted" in str(refused.value)
        # and the resources travel with it, which is what an operator has to act on
        assert [r["name"] for r in refused.value.resources] == ["agentnode-run-stuck"]


class TestAnAbortedCleanupIsFinishedByTheNextStart:
    """CU10's fifth ending: the process died between two cleanup steps."""

    def test_a_network_left_without_its_container_is_removed_at_the_next_reconciliation(
            self, monkeypatch, no_resolver):
        # The shape an abort leaves: the proxy container went, the networks did not.
        rt = _Runtime(containers=[], networks=["d3ad12340001", "d3ad12340002"])
        monkeypatch.setattr(egress, "_run", rt)
        got = egress.remove_everything_of_ours("podman")
        assert sorted(x["name"] for x in got["removed"]) == [
            "d3ad12340001", "d3ad12340002"], got
        assert got["clean"] is True
        assert rt.networks == []


class TestEveryEndingTearsTheRouteOutDown:
    """CU10: one cleanup path, and each of the five endings DRIVEN into it, not asserted about.

    An independent review found that cancel was the exception -- named first by the criterion and
    the only one of the five not driven anywhere. It is driven below, through the entry point a
    gateway's cancel actually uses. Where each of the five is driven:

    * a timeout and an error -- the parametrised test here, through `LocalWorker.run`;
    * a cancel -- `test_a_cancellation_is_driven_into_the_same_cleanup`, through `stop()`;
    * a killed process -- `TestAnAbortedCleanupIsFinishedByTheNextStart`, where the process is gone
      between two cleanup steps and the next start finishes it;
    * a restart -- `test_a_rebooted_worker_comes_back.py`, where the unit's own start reconciles
      before anything else, and the acceptance drives it on a real worker with no root help.

    A normal exit is driven here too. It is not one of the five, but a cleanup that only happens
    when something went wrong is the other way to get this wrong.
    """

    class _Handle:
        int_net = "agentnode-egress-aaaa1111-int"
        ext_net = "agentnode-egress-aaaa1111-ext"
        proxy_name = "agentnode-egress-aaaa1111-proxy"
        runtime = "podman"
        proxy_id = "pid"
        int_net_id = "i"
        ext_net_id = "e"

        class spec:
            proxy_url = "http://10.89.0.1:8888"
            allowed_domains = ("pypi.org",)

        def as_record(self):
            return {"proxy": {"name": self.proxy_name}}

    def _job(self):
        from agentnode_sdk.worker import Job, Limits

        return Job(run_id="r-1", container_name="agentnode-run-1", command=("true",),
                   artifact=b"", stdin="", network="egress", allowed_domains=("pypi.org",),
                   limits=Limits(wall_clock_s=5), owner_label="acct", epoch="e1")

    @pytest.mark.parametrize("ending", ["a normal exit", "a failure", "a timeout"])
    def test_the_route_out_is_torn_down_for_every_ending(self, monkeypatch, ending):
        from agentnode_sdk.sandbox import egress as eg
        from agentnode_sdk.sandbox import egress_verify as ev
        from agentnode_sdk.worker import local as wl

        torn: list = []
        monkeypatch.setattr(eg, "start_egress_proxy", lambda *a, **k: self._Handle())
        monkeypatch.setattr(ev, "verify_the_boundary", lambda *a, **k: ())
        monkeypatch.setattr(eg, "stop_egress_proxy",
                            lambda handle: torn.append(handle.proxy_name) or
                            {"resources": [], "complete": True, "still_there": []})

        class _Backend:
            runtime = "podman"
            native_platform = "linux"

            def check_available(self):
                return type("A", (), {"backend": "podman", "available": True, "reason": ""})()

            def run_process(self, spec, input_text=None, timeout=120.0):
                if ending == "a failure":
                    raise RuntimeError("the payload could not be started")
                if ending == "a timeout":
                    from agentnode_sdk.gateway.protocol import TIMED_OUT
                    from agentnode_sdk.sandbox.backend import Outcome

                    return Outcome(None, "", "[sandbox timed out]", reason=TIMED_OUT,
                                   native_status=137, platform="linux-container")
                from agentnode_sdk.sandbox.backend import Outcome

                return Outcome(0, "done", "", reason="", native_status=0,
                               platform="linux-container")

        worker = wl.LocalWorker(_Backend())
        monkeypatch.setattr(worker, "_egress_gone", lambda handle: True)
        try:
            worker.run(self._job())
        except Exception:                                         # noqa: BLE001
            pass
        assert torn == ["agentnode-egress-aaaa1111-proxy"], (
            "the route out was not torn down for ending=%s" % ending)


class TestASurvivingResolverEntryIsNotACleanCleanup:
    """CU13: a run whose resolver entry is still there is not a confirmed cleanup."""

    def test_the_read_back_says_no_while_an_entry_of_this_runs_network_remains(
            self, tmp_path, monkeypatch):
        from agentnode_sdk.worker import local as wl

        where = tmp_path / "containers" / "networks" / "aardvark-dns"
        where.mkdir(parents=True)
        (where / "agentnode-egress-aaaa1111-int").write_text("10.89.1.1\n", encoding="utf-8")
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

        class _Backend:
            def check_available(self):
                return type("A", (), {"backend": "podman", "available": True, "reason": ""})()

        worker = wl.LocalWorker(_Backend())
        said = worker._egress_gone(TestEveryEndingTearsTheRouteOutDown._Handle())
        assert said is False, (
            "a surviving resolver entry was reported as a clean cleanup, which is the file that "
            "stopped aardvark-dns starting at all in F37")


class TestNothingInTheCleanupPathAsksForPrivilege:
    """CU11: no root, no privileged helper, no human. The account removes its own resources."""

    def test_no_cleanup_function_invokes_a_privilege_tool(self):
        import inspect

        from agentnode_sdk.worker import local as wl
        from agentnode_sdk.worker import reconcile as rc

        forbidden = ("sudo", "pkexec", "doas", "runuser", "setpriv", "machinectl")
        sources = [inspect.getsource(egress.remove_everything_of_ours),
                   inspect.getsource(egress._remove_one),
                   inspect.getsource(egress._remove_resolver_entry),
                   inspect.getsource(egress._teardown),
                   inspect.getsource(wl.LocalWorker.remove_what_a_previous_worker_left),
                   inspect.getsource(rc)]
        for source in sources:
            for word in forbidden:
                assert word not in source, (
                    "the cleanup path reaches for %s; in F37 a human with root had to signal each "
                    "container's init, and that is exactly what must not be needed" % word)


class TestWhatIsPendingIsNamedAndWhatIsClosedIsReadBack:
    """PA1, PA3 and the sixth counter-check of PA7, against the real store."""

    def _a_root(self, tmp_path):
        from agentnode_sdk.gateway.identity import GatewayState

        state = GatewayState(str(tmp_path), version="test")
        return state.root if hasattr(state, "root") else tmp_path

    def test_a_pending_change_is_named_by_the_report(self, tmp_path, capsys):
        from agentnode_sdk.cli import gateway_commands as gw
        from agentnode_sdk.gateway import operator_policy as opol
        from agentnode_sdk.gateway.activation import ActivationStore

        store = ActivationStore(tmp_path)
        store.write_pending(opol.build(opol.RESTRICTED, ("pypi.org",), {}, None))
        gw._egress_show(tmp_path, verbose=True)
        said = capsys.readouterr().out
        assert "PENDING" in said and "NOT in force" in said, said
        assert "pypi.org" in said, said

    def test_and_a_report_with_nothing_pending_does_not_say_so(self, tmp_path, capsys):
        from agentnode_sdk.cli import gateway_commands as gw

        gw._egress_show(tmp_path, verbose=True)
        said = capsys.readouterr().out
        assert "PENDING" not in said, said

    def test_a_close_that_did_not_take_effect_is_not_reported_as_one(self, tmp_path):
        """PA3. `E0424` is this exact situation in the sealed record: the close could not be
        measured, the previous policy stayed in force, and nothing read it back."""
        from agentnode_sdk.cli import gateway_commands as gw
        from agentnode_sdk.gateway import operator_policy as opol
        from agentnode_sdk.gateway.activation import ActivationStore

        store = ActivationStore(tmp_path)
        still_granting = opol.build(opol.RESTRICTED, ("pypi.org", "files.pythonhosted.org"),
                                    {}, None)
        store.activate(still_granting, {"results": []}, {}, 1.0)
        took, granted = gw._the_close_took_effect(tmp_path)
        assert took is False, "a policy that still grants two destinations was called closed"
        assert sorted(granted) == ["files.pythonhosted.org", "pypi.org"], granted

    def test_and_a_policy_that_grants_nothing_reads_back_as_closed(self, tmp_path):
        from agentnode_sdk.cli import gateway_commands as gw
        from agentnode_sdk.gateway import operator_policy as opol
        from agentnode_sdk.gateway.activation import ActivationStore

        ActivationStore(tmp_path).activate(opol.build(opol.NONE, (), {}, None),
                                           {"results": []}, {}, 1.0)
        took, granted = gw._the_close_took_effect(tmp_path)
        assert took is True and granted == []

    def test_closing_twice_says_the_same_thing_the_second_time(self, tmp_path, capsys):
        """The sixth counter-check of PA7, and it found a real defect while being written: the
        unmeasured close activated the policy and left its PENDING record behind, so every later
        report said a change was proposed and not in force -- and the second close said something
        different from the first."""
        from agentnode_sdk.cli import gateway_commands as gw
        from agentnode_sdk.gateway import operator_policy as opol
        from agentnode_sdk.gateway.activation import ActivationStore

        store = ActivationStore(tmp_path)
        closed = opol.build(opol.NONE, (), {}, None)
        for _ in (1, 2):
            store.write_pending(closed)
            store.activate(closed, {"not_measured": True, "results": []}, {}, 1.0)
            store.clear_pending()
        gw._egress_show(tmp_path, verbose=True)
        first = capsys.readouterr().out
        gw._egress_show(tmp_path, verbose=True)
        second = capsys.readouterr().out
        assert "PENDING" not in first and "PENDING" not in second, (first, second)
        # The generation advances, which is what a generation is for; what must not differ is what
        # the two reports SAY about what is granted.
        def granted_lines(text):
            return [ln for ln in text.splitlines() if "may reach" in ln or "grants" in ln]
        assert granted_lines(first) == granted_lines(second), (first, second)

    def test_the_unmeasured_close_leaves_no_pending_record(self):
        """The same property where it is decided, so a mutation there is caught."""
        import inspect

        from agentnode_sdk.gateway.server import GatewayService

        # Counter-check 16 stayed GREEN against the first version of this: `_transact` clears the
        # pending record on three paths, and slicing from `_grants_nothing` to the end caught one of
        # the others. The slice is the unmeasured branch itself -- from the predicate to the verdict
        # it returns.
        source = inspect.getsource(GatewayService._transact)
        start = source.index("_grants_nothing(envelope)")
        end = source.index("return Readiness(", start)
        branch = source[start:end]
        assert "store.clear_pending()" in branch, (
            "the unmeasured activation leaves its pending record behind, so every later report says "
            "a change is proposed and not in force")


class TestTheInventoryAsksAQuestionBothRuntimesAnswer:
    """The Docker lane found this, and the frozen profile is why it was looked for there.

    The container inventory used to read its labels out of `ps --format '{{index .Labels "k"}}'`.
    `.Labels` is a MAP in podman and a comma-separated STRING in docker, so that template makes docker
    exit non-zero; the inventory then reported that it could not be asked, and the gate -- correctly, for
    what it was told -- refused to serve on the grounds that this worker owned something it could not
    remove. On a host that owned nothing. One template turned a clean machine into an unavailable one.
    """

    def test_a_runtime_that_rejects_a_map_template_can_still_be_inventoried(self, monkeypatch,
                                                                           no_resolver):
        rt = _Runtime(containers=["abcd00000001"], docker_like=True)
        monkeypatch.setattr(egress, "_run", rt)

        got = egress.what_is_left_of_ours("docker")

        assert got["asked"] is True, (
            "the inventory could not be asked of a docker-shaped runtime: %s" % got.get("reason"))
        assert [c["id"] for c in got["containers"]] == ["abcd00000001"]
        assert got["unreadable"] == [], got["unreadable"]

    def test_and_it_never_sends_that_template_at_all(self, monkeypatch, no_resolver):
        """Not a check on the source: the double RAISES on it, so a send would fail the test above.

        This one states the narrower fact that no call carried it, which is what makes the test above
        pass for the right reason rather than because the error was swallowed somewhere.
        """
        rt = _Runtime(containers=["abcd00000001"], docker_like=True)
        monkeypatch.setattr(egress, "_run", rt)

        egress.what_is_left_of_ours("docker")

        offending = [argv for argv in rt.calls
                     if any("index .Labels" in str(part) for part in argv)]
        assert offending == [], "a podman-only template was sent: %r" % (offending[:1],)

    def test_and_a_container_whose_labels_cannot_be_read_is_unreadable_not_foreign(
            self, monkeypatch, no_resolver):
        """CU7 at the new seam. An id that cannot be placed is not an id that belongs to somebody else."""
        rt = _Runtime(containers=["abcd00000001"])
        real = rt.__call__

        def refusing_inspect(argv, timeout=30.0, **kw):
            if list(argv)[1] == "inspect":
                raise subprocess.CalledProcessError(125, list(argv), output="", stderr="no such object")
            return real(argv, timeout=timeout, **kw)

        monkeypatch.setattr(egress, "_run", refusing_inspect)

        got = egress.what_is_left_of_ours("podman")

        assert got["containers"] == [], "it must not claim a container it could not place"
        assert got["left_alone"] == [], "and must not write it off as somebody else's"
        assert any("could not be inspected" in u for u in got["unreadable"]), got["unreadable"]
        assert egress.nothing_of_ours_is_left("podman")[0] is False


class TestTheRefusalSaysWhichOfTheTwoStatesThisIs:
    """CU6: the refusal is reported as the state it IS, and there are two of them.

    `it owns something it could not remove` and `it cannot say what is on this host` send an operator to
    different places. The first is a container to go and look at; the second is a runtime to go and fix.
    Printing the first for the second is what happened when a listing template docker rejects made the
    inventory unaskable: the headline named a container, and there was no container.

    What is driven here is the COMMAND, so the sentences a reader will actually see are the ones
    asserted. Two other gates of `cmd_serve` are stood down because they are not what this is about and
    each has its own tests: the runtime pin, which refuses on any machine whose installed artefact
    records no digest, and `serve` itself, which is replaced by the refusal it would raise.
    """

    OWNS = {"runtime": "podman", "found": ["agentnode-run-stuck"], "removed": [],
            "failed": [{"kind": "container", "name": "agentnode-run-stuck",
                        "state": PRESENT, "why": "operation not permitted"}],
            "unreadable": [],
            "egress": {"asked": True, "clean": True, "failed": [], "removed": [], "unreadable": []},
            "clean": False, "why": ""}

    CANNOT_SAY = {"runtime": "docker", "found": [], "removed": [], "failed": [], "unreadable": [],
                  "egress": {"asked": False, "clean": False, "failed": [], "removed": [],
                             "unreadable": [],
                             "reason": "the runtime would not list its containers"},
                  "clean": False, "why": ""}

    def _what_it_said(self, report, tmp_path, capsys, monkeypatch):
        import agentnode_sdk.worker.service as svc
        from agentnode_sdk.cli import worker_commands
        from agentnode_sdk.worker.reconcile import LeftoversRemain, _why_not

        class Args:
            socket = "unix://" + str(tmp_path / "worker.sock")
            key = str(tmp_path / "key")
            for_user = 1000
            listen = ""
            tls_config = ""
            topology = ""
            keyring = ""
            journal = ""

        def refusing(*_rest, **_kw):
            raise LeftoversRemain(_why_not(report), report)

        monkeypatch.setattr(worker_commands, "_refuse_unless_pinned", lambda *a, **k: False)
        monkeypatch.setattr(svc, "serve", refusing)
        code = worker_commands.cmd_serve(Args())
        return code, capsys.readouterr().out

    def test_a_worker_that_owns_something_says_so(self, tmp_path, capsys, monkeypatch):
        code, said = self._what_it_said(self.OWNS, tmp_path, capsys, monkeypatch)
        assert code == 1
        assert "it owns something it could not remove" in said, said[-900:]
        assert "agentnode-run-stuck" in said
        assert "operation not permitted" in said

    def test_a_worker_that_could_not_ask_does_not_claim_to_own_anything(
            self, tmp_path, capsys, monkeypatch):
        code, said = self._what_it_said(self.CANNOT_SAY, tmp_path, capsys, monkeypatch)
        assert code == 1
        assert "it owns something it could not remove" not in said, (
            "it named a resource it does not have:" + said[-900:])
        assert "cannot say what is on this host" in said, said[-900:]
        # Asserted as the two printed lines it is, not as one phrase: it is wrapped in the output.
        assert "Nothing is named above because nothing could be listed" in said, said[-900:]
        assert "An answer nobody can give" in said, said[-900:]
        assert "the runtime would not list its containers" in said, said[-900:]

    def test_and_either_way_it_is_not_a_policy_refusal(self, tmp_path, capsys, monkeypatch):
        for report in (self.OWNS, self.CANNOT_SAY):
            _code, said = self._what_it_said(report, tmp_path, capsys, monkeypatch)
            assert "not a policy refusal and not a 403" in said
            assert "NOT rebuilt" in said


class TestACancellationIsDrivenIntoTheSameCleanup:
    """RR-02 of the independent review: CU10 names cancel, and nothing drove a cancel anywhere.

    A cancel is the one ending the run does not choose for itself. It arrives from outside, through
    `LocalWorker.stop`, which removes this run's container from under the run that is still in flight --
    and the run then ends the way a run whose container has gone ends. Both halves are real here: the
    worker's own `stop()` is called, with its deadline loop and its `rm`, and the run is a real
    `LocalWorker.run` in a thread. Only the runtime underneath is doubled.
    """

    def test_the_route_out_is_torn_down_when_a_run_is_cancelled(self, monkeypatch):
        import threading

        from agentnode_sdk.sandbox import egress as eg
        from agentnode_sdk.sandbox import egress_verify as ev
        from agentnode_sdk.worker import local as wl

        handle = TestEveryEndingTearsTheRouteOutDown._Handle()
        torn: list = []
        monkeypatch.setattr(eg, "start_egress_proxy", lambda *a, **k: handle)
        monkeypatch.setattr(ev, "verify_the_boundary", lambda *a, **k: ())
        monkeypatch.setattr(eg, "stop_egress_proxy",
                            lambda h: torn.append(h.proxy_name) or
                            {"resources": [], "complete": True, "still_there": []})

        running = threading.Event()
        cancelled = threading.Event()

        class _Backend:
            runtime = "podman"
            native_platform = "linux"

            def check_available(self):
                return type("A", (), {"backend": "podman", "available": True, "reason": ""})()

            def run_process(self, spec, input_text=None, timeout=120.0):
                # The payload is in flight when the cancel arrives, and comes back the way a payload
                # whose container was taken out from under it comes back.
                running.set()
                assert cancelled.wait(timeout=20), "the cancel never arrived"
                raise RuntimeError("the container was removed from under the run")

        worker = wl.LocalWorker(_Backend())
        monkeypatch.setattr(worker, "_egress_gone", lambda h: True)

        job = TestEveryEndingTearsTheRouteOutDown()._job()

        # What the RUNTIME answers while the cancel is carried out: the container is there, and `rm`
        # takes it away. `stop()` itself is not doubled -- its loop, its listing and its removal run.
        removals: list = []

        def _runtime_said(argv, *rest, **kw):
            argv = list(argv)
            if argv[1:2] == ["rm"]:
                removals.append(argv)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(wl.subprocess, "run", _runtime_said)
        monkeypatch.setattr(worker, "gone",
                            lambda container_name, patiently=True: wl.Gone(
                                answered=True, left=(container_name,)))

        ended: list = []

        def _run_it():
            try:
                ended.append(worker.run(job))
            except BaseException as exc:                          # noqa: BLE001 - reported below
                ended.append(exc)

        thread = threading.Thread(target=_run_it, daemon=True)
        thread.start()
        assert running.wait(timeout=20), "the payload never started"

        # THE CANCEL, through the product's own entry point.
        assert worker.stop(job.run_id, job.container_name, 1.0) is True, (
            "the cancel did not remove this run's container")
        assert removals, "stop() issued no removal"
        cancelled.set()
        thread.join(timeout=30)
        assert not thread.is_alive(), "the cancelled run never ended"

        assert torn == [handle.proxy_name], (
            "a cancelled run did not reach the one cleanup path: %r" % (torn,))


class TestADiagnosticIsNotAResource:
    """RR-01 of the independent review, first half: CU7 as an allow-list rather than a deny-list.

    A listing was parsed by EXCLUDING lines whose first token began with one of a handful of runtime
    notice prefixes. Any other line was a row -- so `Cannot connect to the runtime` was a container, and
    an inventory that reported a resource on an empty host was the mild version of the fault. The severe
    one is the reverse, which the second test here is about: a single word that happens to look like a
    name used to be read as somebody ELSE's container, and that is an inventory saying the host is clean.
    """

    def test_a_diagnostic_sentence_is_unreadable_and_not_a_container(self, monkeypatch, no_resolver):
        said = {"ps": "Cannot connect to the runtime: is the daemon running?\n"}

        def _run(argv, timeout=30.0, **kw):
            argv = list(argv)
            text = said["ps"] if argv[1:3] == ["ps", "-a"] else ""
            return type("R", (), {"returncode": 0, "stdout": text, "stderr": ""})()

        monkeypatch.setattr(egress, "_run", _run)
        got = egress.what_is_left_of_ours("podman")

        assert got["containers"] == [], "a diagnostic was counted as a container"
        assert any("Cannot connect" in u for u in got["unreadable"]), got["unreadable"]
        clean, _inventory = egress.nothing_of_ours_is_left("podman")
        assert clean is False, "an unreadable listing was read as provably empty"

    def test_and_one_word_that_is_not_an_id_does_not_make_a_resource_absent(self, monkeypatch):
        """`state_of` asks a listing FILTERED by one name. A row that does not contain that name cannot
        have come from the filter, so it says nothing about the resource -- in either direction."""
        from agentnode_sdk.sandbox.container_backend import UNKNOWN

        def _run(argv, timeout=30.0, **kw):
            return type("R", (), {"returncode": 0, "stdout": "Error\n", "stderr": ""})()

        monkeypatch.setattr(egress, "_run", _run)
        assert egress.state_of("podman", "container", "agentnode-egress-aaaa1111-proxy") == UNKNOWN
        assert egress.state_of("podman", "network", "agentnode-egress-aaaa1111-int") == UNKNOWN


class TestAnUnlistableResolverDirectoryIsNotAnEmptyOne:
    """RR-01, second half: three outcomes where there were two.

    A directory that is not there is an answer -- nothing is in it, which is the ordinary case under
    docker. A directory that is there and cannot be listed is not an answer, and neither is a rootless
    podman whose runtime directory cannot even be located. Both of those returned an empty list, so an
    inventory nobody could read looked provably empty and would have let a migration through.
    """

    def test_a_directory_that_is_not_there_is_an_answer(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))      # exists, holds no resolver directory
        entries, cannot_read = egress._resolver_entries("podman")
        assert entries == [] and cannot_read == [], cannot_read

    def test_a_directory_that_cannot_be_listed_is_not(self, monkeypatch, tmp_path):
        where = tmp_path / "containers" / "networks" / "aardvark-dns"
        where.mkdir(parents=True)
        real = egress.os.listdir

        def _refusing(path):
            if str(path) == str(where):
                raise OSError(13, "Permission denied")
            return real(path)

        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        monkeypatch.setattr(egress.os, "listdir", _refusing)

        entries, cannot_read = egress._resolver_entries("podman")
        assert entries == []
        assert any("could not be listed" in u for u in cannot_read), cannot_read

    def test_and_a_podman_whose_runtime_directory_is_unknown_is_not_either(self, monkeypatch):
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        entries, cannot_read = egress._resolver_entries("podman")
        assert entries == []
        assert any("could not be located" in u for u in cannot_read), cannot_read
        # and docker, which has no such resolver at all, is not made unreadable by the same absence
        entries, cannot_read = egress._resolver_entries("docker")
        assert entries == [] and cannot_read == [], cannot_read

    def test_and_the_inventory_carries_it_so_nothing_is_provably_empty(self, monkeypatch):
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

        def _run(argv, timeout=30.0, **kw):
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(egress, "_run", _run)
        got = egress.what_is_left_of_ours("podman")

        assert got["asked"] is True
        assert any("resolver" in u for u in got["unreadable"]), got["unreadable"]
        clean, _inventory = egress.nothing_of_ours_is_left("podman")
        assert clean is False, "an inventory with an unreadable part was called provably empty"
