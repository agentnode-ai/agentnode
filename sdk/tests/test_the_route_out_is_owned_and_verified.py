"""What a run's route out belongs to, and what is measured about it before any foreign code runs.

Three properties, and each of them exists because of something that was found rather than imagined:

  * **ownership is by label and by runtime id.** A name is a string anybody can choose. An independent
    review of the decision this implements said so: inferring ownership from a name means a resource that
    happens to share one is adopted and one that was renamed is orphaned.
  * **the payload network carries no resolver.** FINDING-EGRESS-1, measured on the real worker host: on a
    default internal network a payload can reach the container DNS resolver on the bridge's own gateway
    address, UDP/53, and it answers -- a host-side process. With DNS disabled the runtime assigns that
    bridge no gateway address at all, so there is nothing host-side in the payload's subnet to reach.
  * **what was created is read back, and disagreement is a refusal.** Building the arrangement is not the
    same statement as the boundary being there, and only one of the two is worth starting somebody's code
    on.

Nothing here needs a container runtime: the runtime is a double that answers the way one answers, and the
tests that want a refusal construct a double that answers differently.
"""
from __future__ import annotations

import json

import pytest

from agentnode_sdk.sandbox import egress, egress_verify
from agentnode_sdk.sandbox.types import SandboxAvailability, SandboxRequiredError


class _Backend:
    def __init__(self, runtime="podman"):
        self.runtime = runtime

    def check_available(self):
        return SandboxAvailability(available=True, backend=self.runtime, reason="")


class _Runtime:
    """Answers what a runtime answers. Every deviation a test wants is a constructor argument."""

    def __init__(self, internal=True, dns=False, attached=None, address="10.89.0.2",
                 networks_listed="", containers_listed=""):
        self.calls = []
        self.internal = internal
        self.dns = dns
        self.attached = attached
        self.address = address
        self.networks_listed = networks_listed
        self.containers_listed = containers_listed

    def _answer(self, argv):
        if argv[1:3] == ["network", "inspect"]:
            name = argv[3]
            return json.dumps({"id": "netid-" + name, "internal": self.internal,
                               "dns_enabled": self.dns, "ipv6_enabled": False,
                               "subnets": [{"subnet": "10.89.0.0/24", "gateway": "10.89.0.1"}],
                               "labels": {"agentnode.component": "egress",
                                          "agentnode.run": "r-7"}})
        if argv[1:3] == ["network", "ls"]:
            return self.networks_listed
        if argv[1:3] == ["ps", "-a"]:
            return self.containers_listed
        if argv[1] == "inspect" and "{{json .NetworkSettings.Networks}}" in argv:
            nets = self.attached
            if nets is None:
                # The two networks created MOST RECENTLY, not every network this double ever saw. A
                # double reused across two arrangements answered with all four and the product refused
                # it, correctly: a proxy on four networks is not a proxy on its own two.
                created = [c[-1] for c in self.calls if c[1:3] == ["network", "create"]]
                nets = sorted(created[-2:])
            return json.dumps({n: {"NetworkID": "netid-" + n,
                                   "IPAddress": (self.address if n.endswith("-int") else "10.89.1.2"),
                                   "GlobalIPv6Address": ""} for n in nets})
        if argv[1] == "inspect" and "{{.Id}}" in argv:
            return "containerid"
        return ""

    def __call__(self, argv, timeout=30.0):
        self.calls.append(list(argv))
        answer = self._answer(list(argv))

        class _CP:
            stdout = answer
            stderr = ""
        return _CP()

    def created(self, suffix):
        """The argv of the `network create` whose name ends in `suffix`."""
        return next(c for c in self.calls
                    if c[1:3] == ["network", "create"] and c[-1].endswith(suffix))


@pytest.fixture(autouse=True)
def _no_health_poll(monkeypatch):
    monkeypatch.setattr(egress, "_wait_healthy", lambda *a, **k: None)
    egress._live.clear()
    yield
    egress._live.clear()


class TestThePayloadNetworkCarriesNoResolver:
    def test_on_podman_dns_is_disabled_and_the_proxy_is_reached_by_address(self, monkeypatch):
        rt = _Runtime()
        monkeypatch.setattr(egress, "_run", rt)
        handle = egress.start_egress_proxy(["example.com"], backend=_Backend("podman"))

        assert "--disable-dns" in rt.created("-int"), rt.created("-int")
        assert "--disable-dns" not in rt.created("-ext")
        assert handle.spec.proxy_url == "http://10.89.0.2:8888", handle.spec.proxy_url
        assert dict(handle.readings)["proxy_reached_by"] == "address"
        # and no alias was asked for, because there is nothing left to resolve it
        connect = next(c for c in rt.calls if c[1:3] == ["network", "connect"])
        assert "--alias" not in connect

    def test_on_docker_the_alias_is_kept_because_there_is_no_such_flag(self, monkeypatch):
        rt = _Runtime()
        monkeypatch.setattr(egress, "_run", rt)
        handle = egress.start_egress_proxy(["example.com"], backend=_Backend("docker"))

        assert "--disable-dns" not in rt.created("-int")
        assert handle.spec.proxy_url == "http://egress-proxy:8888"
        assert dict(handle.readings)["proxy_reached_by"] == "alias"
        connect = next(c for c in rt.calls if c[1:3] == ["network", "connect"])
        assert "--alias" in connect and "egress-proxy" in connect

    def test_a_resolver_on_the_payload_network_is_refused_on_podman(self, monkeypatch):
        rt = _Runtime(dns=True)
        monkeypatch.setattr(egress, "_run", rt)
        with pytest.raises(SandboxRequiredError, match="still carries a resolver"):
            egress.start_egress_proxy(["example.com"], backend=_Backend("podman"))
        assert not egress._live
        # and what it had already created was removed rather than left standing
        assert [c for c in rt.calls if c[1:3] == ["network", "rm"]], rt.calls


class TestOwnershipIsByLabelAndId:
    def test_every_resource_carries_the_run_the_account_and_the_epoch(self, monkeypatch):
        rt = _Runtime()
        monkeypatch.setattr(egress, "_run", rt)
        who = egress.EgressOwner(run="run-9", account="acct-2", epoch="g3")
        handle = egress.start_egress_proxy(["example.com"], backend=_Backend("podman"), owner=who)

        for call in rt.calls:
            if call[1:3] == ["network", "create"] or call[1:3] == ["run", "-d"]:
                joined = " ".join(call)
                assert "agentnode.component=egress" in joined
                assert "agentnode.run=run-9" in joined
                assert "agentnode.account=acct-2" in joined
                assert "agentnode.epoch=g3" in joined
        assert handle.owner.run == "run-9"

    def test_a_label_value_is_only_what_reads_back(self):
        who = egress.EgressOwner(run="run 9;rm -rf /", account="", epoch="x" * 400)
        labels = " ".join(who.as_labels())
        assert "agentnode.run=run_9_rm_-rf__" in labels, labels
        assert "agentnode.account=unattributed" in labels
        assert len(who._clean("x" * 400)) == 128

    def test_the_record_binds_the_ids_and_the_readings(self, monkeypatch):
        rt = _Runtime()
        monkeypatch.setattr(egress, "_run", rt)
        handle = egress.start_egress_proxy(["example.com"], backend=_Backend("podman"))
        account = handle.as_record()

        assert account["internal_network"]["id"].startswith("netid-")
        assert account["external_network"]["id"].startswith("netid-")
        assert account["proxy"]["id"] == "containerid"
        assert account["allowed_destinations"] == ["example.com"]
        assert account["readings"]["internal_network_internal"] is True
        assert account["readings"]["internal_network_dns_enabled"] is False
        assert account["runtime"] == "podman"


class TestWhatIsCreatedIsReadBack:
    def test_a_network_that_does_not_report_itself_internal_is_refused(self, monkeypatch):
        rt = _Runtime(internal=False)
        monkeypatch.setattr(egress, "_run", rt)
        with pytest.raises(SandboxRequiredError, match="does not report itself internal"):
            egress.start_egress_proxy(["example.com"], backend=_Backend("podman"))

    def test_a_proxy_on_a_third_network_is_refused(self, monkeypatch):
        rt = _Runtime(attached=["a-int", "b-ext", "somebody-elses"])
        monkeypatch.setattr(egress, "_run", rt)
        with pytest.raises(SandboxRequiredError, match="rather than to exactly its own two networks"):
            egress.start_egress_proxy(["example.com"], backend=_Backend("podman"))

    def test_a_proxy_with_no_address_on_the_payload_network_is_refused(self, monkeypatch):
        rt = _Runtime(address="")
        monkeypatch.setattr(egress, "_run", rt)
        with pytest.raises(SandboxRequiredError, match="no address on the payload's network"):
            egress.start_egress_proxy(["example.com"], backend=_Backend("podman"))


class TestWhatNoRunIsWaitingForIsRemoved:
    def test_it_selects_by_label_and_keeps_a_live_run(self, monkeypatch):
        rt = _Runtime(containers_listed="cid-1 run-live\ncid-2 run-dead\n",
                      networks_listed="netid-1\n")
        monkeypatch.setattr(egress, "_run", rt)
        got = egress.remove_what_no_run_is_waiting_for("podman", keep_runs=["run-live"])

        assert got["asked"] is True
        assert got["containers"] == ["cid-2"], got
        # the listing asked by label, never by a name pattern
        listing = next(c for c in rt.calls if c[1:3] == ["ps", "-a"])
        assert "label=agentnode.component=egress" in listing
        # the network it found belongs to run-7 in this double, which is not live, so it goes
        assert got["networks"] == ["netid-1"], got

    def test_a_network_of_a_live_run_is_kept(self, monkeypatch):
        rt = _Runtime(networks_listed="netid-1\n")
        monkeypatch.setattr(egress, "_run", rt)
        got = egress.remove_what_no_run_is_waiting_for("podman", keep_runs=["r-7"])
        assert got["networks"] == [], got


class TestTheBoundaryIsMeasuredBeforeThePayload:
    class _Handle:
        int_net = "an-int"
        runtime = "podman"
        readings = (("internal_network_subnets",
                     json.dumps([{"subnet": "10.89.0.0/24", "gateway": ""}])),)

        class spec:
            proxy_url = "http://10.89.0.2:8888"

    def _probe_says(self, monkeypatch, said, returncode=0):
        import subprocess

        def fake(argv, input=None, capture_output=False, text=False, timeout=None):
            class _CP:
                stdout = ("AGENTNODE_VERIFY " + json.dumps(said)) if said is not None else "nothing"
                stderr = ""
                returncode = 0
            _CP.returncode = returncode
            return _CP()

        monkeypatch.setattr(subprocess, "run", fake)

    def test_it_passes_when_everything_that_must_fail_failed(self, monkeypatch):
        self._probe_says(monkeypatch, {
            "must_fail": {"public_ipv4": {"outcome": "refused"},
                          "public_ipv6": {"outcome": "refused"},
                          "a_public_name": {"outcome": "no-dns"}},
            "must_work": {"the_proxy": {"outcome": "connected"}}})
        readings = egress_verify.verify_the_boundary(self._Handle())
        assert dict(readings)["boundary_probe_exit"] == 0

    def test_a_destination_that_was_reachable_refuses_the_run(self, monkeypatch):
        self._probe_says(monkeypatch, {
            "must_fail": {"public_ipv4": {"outcome": "connected", "detail": "1.1.1.1"}},
            "must_work": {"the_proxy": {"outcome": "connected"}}})
        with pytest.raises(SandboxRequiredError, match="public_ipv4 was reachable"):
            egress_verify.verify_the_boundary(self._Handle())

    def test_a_silent_send_is_not_a_refusal(self, monkeypatch):
        # "nothing came back" is not "it was blocked", and treating the two as the same is how a
        # boundary that is not there passes a check.
        self._probe_says(monkeypatch, {
            "must_fail": {"udp_resolver": {"outcome": "silent"}},
            "must_work": {"the_proxy": {"outcome": "connected"}}})
        with pytest.raises(SandboxRequiredError, match="udp_resolver was reachable"):
            egress_verify.verify_the_boundary(self._Handle())

    def test_a_proxy_that_does_not_answer_refuses_the_run(self, monkeypatch):
        self._probe_says(monkeypatch, {
            "must_fail": {"public_ipv4": {"outcome": "refused"}},
            "must_work": {"the_proxy": {"outcome": "refused"}}})
        with pytest.raises(SandboxRequiredError, match="does not answer"):
            egress_verify.verify_the_boundary(self._Handle())

    def test_no_reading_at_all_refuses_the_run(self, monkeypatch):
        self._probe_says(monkeypatch, None, returncode=1)
        with pytest.raises(SandboxRequiredError, match="no reading"):
            egress_verify.verify_the_boundary(self._Handle())

    def test_the_probe_asks_about_both_families_and_about_udp(self):
        """IPv6 forgotten is the classic hole, so the probe's own content is asserted.

        A boundary that is only measured on IPv4 is a boundary nobody measured: this host has a public
        IPv6 address and a default route on both families. The probe is a string, so this is a reading
        of it rather than a run -- which is the point: the run cannot tell you what it failed to ask.
        """
        source = egress_verify.PROBE
        assert "AF_INET6" in source, "the probe does not ask about IPv6 at all"
        assert "2606:4700:4700::1111" in source, "no public IPv6 literal is tried"
        assert "::ffff:1.1.1.1" in source, "an IPv4-mapped IPv6 address is not tried"
        assert "SOCK_DGRAM" in source, "UDP is never tried, so a QUIC-shaped path is unmeasured"
        assert "169.254.169.254" in source, "the metadata address is not tried"


class TestTheOrderAndTheTeardown:
    """The arrangement is measured BEFORE the payload, and taken down after it, on every path."""

    class _Backend:
        native_platform = "linux"

        def __init__(self, order):
            self.order = order

        def check_available(self):
            return SandboxAvailability(available=True, backend="podman", reason="")

        def run_process(self, spec, input_text="", timeout=0.0):
            self.order.append("the payload ran")
            return (0, "", "")

    def _worker(self, order, monkeypatch, *, fail_in_payload=False):
        from agentnode_sdk.worker import Job, Limits
        from agentnode_sdk.worker import local as local_mod

        handle = type("H", (), {
            "int_net": "an-int", "ext_net": "an-ext", "proxy_name": "an-proxy",
            "runtime": "podman", "readings": (), "int_net_id": "i", "ext_net_id": "e",
            "proxy_id": "p", "owner": egress.EgressOwner(run="r"),
            "spec": type("S", (), {"network_name": "an-int", "proxy_url": "http://10.89.0.2:8888",
                                   "allowed_domains": ("example.com",)})(),
            "as_record": lambda self=None: {"proxy": {"id": "p"}},
        })()
        monkeypatch.setattr(local_mod, "CouldNotRestrictTheNetwork",
                            local_mod.CouldNotRestrictTheNetwork, raising=False)
        import agentnode_sdk.sandbox.egress as egress_mod
        import agentnode_sdk.sandbox.egress_verify as verify_mod

        def started(domains, **kw):
            order.append("the arrangement was built")
            return handle

        def verified(h, **kw):
            order.append("the boundary was measured")
            return (("boundary_probe_exit", 0),)

        def stopped(h):
            order.append("the arrangement was taken down")

        monkeypatch.setattr(egress_mod, "start_egress_proxy", started)
        monkeypatch.setattr(verify_mod, "verify_the_boundary", verified)
        monkeypatch.setattr(egress_mod, "stop_egress_proxy", stopped)
        backend = self._Backend(order)
        if fail_in_payload:
            def boom(spec, input_text="", timeout=0.0):
                order.append("the payload ran")
                raise RuntimeError("the runtime refused")
            backend.run_process = boom
        worker = local_mod.LocalWorker(backend=backend)
        monkeypatch.setattr(worker, "_egress_gone", lambda h: True)
        job = Job(run_id="r-1", container_name="c-1", command=("true",), artifact=b"",
                  stdin="", network="egress", allowed_domains=("example.com",),
                  limits=Limits(wall_clock_s=5), owner_label="acc7acc7acc7acc7", epoch="g3")
        return worker, job

    def test_the_boundary_is_measured_before_the_payload_ever_runs(self, monkeypatch):
        order = []
        worker, job = self._worker(order, monkeypatch)
        out = worker.run(job)
        assert order == ["the arrangement was built", "the boundary was measured",
                         "the payload ran", "the arrangement was taken down"], order
        assert out.egress_record and out.egress_record["proxy"]["id"] == "p"
        assert out.egress_record["verified_before_the_payload"]["boundary_probe_exit"] == 0

    def test_it_is_taken_down_even_when_the_payload_could_not_run(self, monkeypatch):
        from agentnode_sdk.worker import JobFailed

        order = []
        worker, job = self._worker(order, monkeypatch, fail_in_payload=True)
        with pytest.raises(JobFailed):
            worker.run(job)
        assert "the arrangement was taken down" in order, order

    def test_a_boundary_that_cannot_be_measured_stops_the_run_and_takes_it_down(self, monkeypatch):
        import agentnode_sdk.sandbox.egress_verify as verify_mod
        from agentnode_sdk.worker import local as local_mod

        order = []
        worker, job = self._worker(order, monkeypatch)

        def refuse(h, **kw):
            order.append("the boundary was measured")
            raise SandboxRequiredError("public_ipv4 was reachable")

        monkeypatch.setattr(verify_mod, "verify_the_boundary", refuse)
        with pytest.raises(local_mod.CouldNotRestrictTheNetwork):
            worker.run(job)
        assert order == ["the arrangement was built", "the boundary was measured",
                         "the arrangement was taken down"], order
        assert "the payload ran" not in order


class TestTheSweepSeesMoreThanContainers:
    def test_what_a_dead_worker_left_includes_its_networks(self, monkeypatch):
        from agentnode_sdk.worker import local as local_mod
        import agentnode_sdk.sandbox.egress as egress_mod

        class _Backend:
            def check_available(self):
                return SandboxAvailability(available=True, backend="podman", reason="")

        import subprocess

        def fake_run(argv, capture_output=False, text=False, timeout=None):
            class _CP:
                returncode = 0
                stdout = ""
                stderr = ""
            return _CP()

        monkeypatch.setattr(subprocess, "run", fake_run)
        asked = {}

        def sweep(runtime="", *, keep_runs=()):
            asked["runtime"] = runtime
            return {"asked": True, "containers": ["cid"], "networks": ["nid"], "kept": []}

        monkeypatch.setattr(egress_mod, "remove_what_no_run_is_waiting_for", sweep)
        got = local_mod.LocalWorker(backend=_Backend()).remove_what_a_previous_worker_left()
        assert got["egress"]["asked"] is True, got
        assert got["egress"]["networks"] == ["nid"], got
        assert asked["runtime"] == "podman"


class TestTwoRunsDoNotShareAnything:
    def test_two_arrangements_have_no_resource_in_common(self, monkeypatch):
        rt = _Runtime()
        monkeypatch.setattr(egress, "_run", rt)
        first = egress.start_egress_proxy(["example.com"], backend=_Backend("podman"),
                                         owner=egress.EgressOwner(run="r-1"))
        second = egress.start_egress_proxy(["example.com"], backend=_Backend("podman"),
                                           owner=egress.EgressOwner(run="r-2"))
        names = {first.int_net, first.ext_net, first.proxy_name}
        others = {second.int_net, second.ext_net, second.proxy_name}
        assert not (names & others), (names, others)
        assert first.owner.run != second.owner.run


class TestTheSignedLineHasSomewhereToSayIt:
    def test_the_two_egress_fields_are_part_of_the_declared_schema(self):
        from agentnode_sdk.gateway import meter

        assert "egress" in meter.FIELDS
        assert "egress_sha256" in meter.FIELDS

    def test_a_line_carries_them_and_the_schema_check_binds_it(self, tmp_path):
        from agentnode_sdk.gateway import meter

        meter.record(
            tmp_path, run_id="r-1", client_id="c", account_id="a",
            started_at=1.0, finished_at=2.0, queued_at=0.5,
            cpu=1.0, memory_mb=64, wall_clock_s=5, state="finished", outcome="succeeded",
            bytes_out=0, worker_topology="single-host-development", allowance_sha256="x" * 64,
            worker_id="w", operator_policy_sha256="p" * 64, operator_policy_version=1,
            egress="allowlist", egress_sha256="d" * 64)
        lines = meter.read(tmp_path)
        assert lines and lines[-1]["egress"] == "allowlist"
        assert lines[-1]["egress_sha256"] == "d" * 64
