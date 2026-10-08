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
                 networks_listed="", containers_listed="", refuses=()):
        self.calls = []
        self.internal = internal
        self.dns = dns
        self.attached = attached
        self.address = address
        # A DOUBLE THAT CAN REPRESENT A REMOVAL. It could not before, and a read-back cannot be tested
        # against a double that answers the same listing after a removal as before it: every removal
        # would look refused. The rows are mutable now, `rm` takes one out, and `refuses` names the ones
        # whose removal fails the way a real runtime failed in F37 -- which is the case the product has
        # to notice.
        self.container_rows = [r for r in (containers_listed or "").splitlines() if r.strip()]
        self.network_rows = [r for r in (networks_listed or "").splitlines() if r.strip()]
        self.refuses = set(refuses or ())

    @staticmethod
    def _first(row):
        return row.split()[0] if row.split() else ""

    def _listing(self, rows, argv):
        """What a listing answers, honouring `--filter name=` the way a runtime does."""
        wanted = ""
        for part in argv:
            if str(part).startswith("name="):
                wanted = str(part).split("=", 1)[1]
        if wanted:
            rows = [r for r in rows if self._first(r) == wanted]
        return "".join(r + "\n" for r in rows)

    def _remove(self, rows, name):
        import subprocess as _sp

        if name in self.refuses:
            raise _sp.CalledProcessError(
                125, ["podman", "rm", "-f", name], output="",
                stderr=("Error: cannot remove container %s as it could not be stopped: sending "
                        "SIGKILL to container %s: operation not permitted" % (name, name)))
        rows[:] = [r for r in rows if self._first(r) != name]

    def _answer(self, argv):
        if argv[1:3] == ["network", "inspect"]:
            name = argv[3]
            return json.dumps({"id": "netid-" + name, "internal": self.internal,
                               "dns_enabled": self.dns, "ipv6_enabled": False,
                               "subnets": [{"subnet": "10.89.0.0/24", "gateway": "10.89.0.1"}],
                               "labels": {"agentnode.component": "egress",
                                          "agentnode.run": "r-7"}})
        if argv[1:3] == ["network", "ls"]:
            return self._listing(self.network_rows, argv)
        if argv[1:3] == ["ps", "-a"]:
            # IDS ONLY, because that is all the product asks `ps` for now. Its labels used to come from
            # the same template, which docker rejects -- see the cross-runtime tests in
            # test_cleanup_is_the_products_own.py. The rows these tests are written with still carry the
            # labels in fields two and three, and `inspect` below is where they are answered from.
            return self._listing([self._first(r) for r in self.container_rows], argv)
        if argv[1] == "inspect" and "{{json .Config.Labels}}" in [str(a) for a in argv]:
            import subprocess as _sp

            for row in self.container_rows:
                parts = row.split()
                if parts and parts[0] == argv[2]:
                    return json.dumps({"agentnode.component": parts[1] if len(parts) > 1 else "",
                                       "agentnode.run": parts[2] if len(parts) > 2 else ""})
            raise _sp.CalledProcessError(125, list(argv), output="",
                                         stderr="Error: no such object: %s" % argv[2])
        if argv[1:3] == ["network", "rm"]:
            self._remove(self.network_rows, argv[-1])
            return ""
        if argv[1] == "rm":
            self._remove(self.container_rows, argv[-1])
            return ""
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

    @property
    def networks_listed(self):
        return "".join(r + "\n" for r in self.network_rows)

    @property
    def containers_listed(self):
        return "".join(r + "\n" for r in self.container_rows)

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
        # THREE FIELDS: the id, the COMPONENT label and the run label. The component is there because
        # the sweep no longer trusts `--filter` to have been honoured -- see the test below, and the
        # finding in test_endings that produced it.
        rt = _Runtime(containers_listed="c1d100000001 egress run-live\nc1d200000002 egress run-dead\n",
                      networks_listed="e7e1d0000001\n")
        monkeypatch.setattr(egress, "_run", rt)
        got = egress.remove_what_no_run_is_waiting_for("podman", keep_runs=["run-live"])

        assert got["asked"] is True
        assert got["containers"] == ["c1d200000002"], got
        # the listing asked by label, never by a name pattern
        listing = next(c for c in rt.calls if c[1:3] == ["ps", "-a"])
        assert "label=agentnode.component=egress" in listing
        # and it asked for the label back as well, which is what makes the check below possible
        assert any('agentnode.component' in str(part) for part in listing), listing
        # the network it found belongs to run-7 in this double, which is not live, so it goes
        assert got["networks"] == ["e7e1d0000001"], got

    def test_a_container_that_does_not_say_it_is_ours_is_left_alone(self, monkeypatch):
        """The fail-open `test_a_sweep_leaves_alone_what_this_sdk_did_not_name` found.

        The first version asked the runtime to filter by label and removed whatever came back. A
        runtime that ignored the filter, did not support it, or was asked by something that did not pass
        it would have had this remove containers belonging to whoever else uses that account's runtime.
        The label is read BACK now, and a candidate that does not say it is this component's is left
        alone and recorded as such.
        """
        rt = _Runtime(containers_listed="a11e0f000001 egress run-dead\nb0b0b0000002  \nc0c0c0000003 other\n",
                      networks_listed="")
        monkeypatch.setattr(egress, "_run", rt)
        got = egress.remove_what_no_run_is_waiting_for("podman")

        assert got["containers"] == ["a11e0f000001"], got
        assert sorted(got["left_alone"]) == ["b0b0b0000002", "c0c0c0000003"], got
        # And nothing was removed for the two that were left alone.
        removals = [c for c in rt.calls if c[1:2] == ["rm"]]
        assert all("b0b0b0000002" not in c and "c0c0c0000003" not in c for c in removals), removals

    def test_a_network_of_a_live_run_is_kept(self, monkeypatch):
        rt = _Runtime(networks_listed="e7e1d0000001\n")
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

    def test_the_gateways_own_silence_is_not_reachability(self, monkeypatch):
        """The one reading whose silence is an answer, and why.

        The EM-3C gateway lane runs on Docker, where an `--internal` bridge still has a gateway
        address and nothing answers on it. Reading that silence as reachability refused every
        restricted run on that runtime -- the lane went red on exactly these two tests, and the
        two-machine measurements never saw it because podman gives such a network no gateway
        address at all. The question this probe asks is whether a host-side resolver ANSWERS in the
        payload's own subnet; for that question silence is a measured no.
        """
        self._probe_says(monkeypatch, {
            "must_fail": {"public_ipv4": {"outcome": "refused"},
                          "the_networks_gateway": {"outcome": "silent", "detail": "172.18.0.1"},
                          "the_networks_gateway_tcp": {"outcome": "refused"}},
            "must_work": {"the_proxy": {"outcome": "connected"}}})
        readings = egress_verify.verify_the_boundary(self._Handle())
        assert dict(readings)["boundary_probe_exit"] == 0

    def test_a_gateway_that_answers_still_refuses_the_run(self, monkeypatch):
        """FINDING-EGRESS-1 itself: a resolver answering on the payload's own subnet."""
        self._probe_says(monkeypatch, {
            "must_fail": {"the_networks_gateway": {"outcome": "answered", "detail": "10.89.0.1"}},
            "must_work": {"the_proxy": {"outcome": "connected"}}})
        with pytest.raises(SandboxRequiredError, match="the_networks_gateway was reachable"):
            egress_verify.verify_the_boundary(self._Handle())

    def test_a_gateway_that_takes_a_tcp_connection_still_refuses_the_run(self, monkeypatch):
        """The half of the question that CAN be refused is read by the general rule, so anything
        listening on that address -- a resolver that speaks TCP or something else entirely -- is
        still a reason not to start."""
        self._probe_says(monkeypatch, {
            "must_fail": {"the_networks_gateway": {"outcome": "silent"},
                          "the_networks_gateway_tcp": {"outcome": "connected",
                                                       "detail": "172.18.0.1"}},
            "must_work": {"the_proxy": {"outcome": "connected"}}})
        with pytest.raises(SandboxRequiredError, match="the_networks_gateway_tcp was reachable"):
            egress_verify.verify_the_boundary(self._Handle())

    def test_the_probe_asks_the_gateway_both_ways(self):
        """A test on the probe's own source, because the two readings above are only meaningful if
        the probe produces both."""
        source = egress_verify.PROBE
        assert 'said["must_fail"]["the_networks_gateway"]' in source
        assert 'said["must_fail"]["the_networks_gateway_tcp"]' in source
        assert "SOCK_DGRAM, A_DNS_QUERY" in source

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


class TestTheMatrixMeasuresTheBoundaryAndNotTheDestination:
    """The gateway could not activate ANY policy naming more than one destination, and the reason was
    in the matrix probe: a destination that answered with an HTTP error status was recorded as
    `refused`, as though the boundary had not let it through.

    Measured on the two machines: a two-host policy of example.com and example.net failed its
    measurement because example.net answers with an error status on `/`. An operator could not have
    allowed an artefact host that answers 403 on `/`, which is most of them. `google.com` failed for a
    second reason: it answers 301 to `www.google.com`, the probe followed the redirect, and nobody had
    allowed where it went -- so the matrix measured a host the policy had not named.

    These read the probe's SOURCE, because it runs inside a container by design and what is under test
    is which answer it records for which outcome.
    """

    def _the_source(self):
        from agentnode_sdk.conformance import probe

        return probe.egress_matrix_source(["example.com"], "example.net")

    def test_an_http_status_from_the_destination_counts_as_reached(self):
        source = self._the_source()
        assert "except urllib.error.HTTPError as exc:" in source, (
            "the probe does not tell an HTTP status from a refused tunnel, so a destination's 404 is "
            "recorded as a boundary that does not work")
        after = source.split("except urllib.error.HTTPError as exc:", 1)[1]
        assert 'R[key] = "ALLOWED:" + str(exc.code)' in after, after[:300]

    def test_a_refused_tunnel_is_still_refused(self):
        """The clause that must NOT be widened. A proxy refusal fails the CONNECT, which raises
        URLError, and that has to stay `refused` or the whole matrix means nothing."""
        source = self._the_source()
        assert 'R[key] = "refused:" + type(exc).__name__' in source
        assert source.index("except urllib.error.HTTPError") < source.rindex(
            'R[key] = "refused:" + type(exc).__name__')

    def test_a_redirect_is_not_followed(self):
        source = self._the_source()
        assert "class _TheNamedHostAndNoOther" in source, (
            "the probe follows redirects, so it measures wherever the named host chose to send it")
        assert "def redirect_request" in source
        assert "_TheNamedHostAndNoOther()" in source, (
            "the handler exists and is not given to the opener, so nothing changed")

    def test_the_probe_imports_what_that_clause_needs(self):
        """`import urllib.request` happens to make urllib.error reachable. A clause that depends on
        another module's imports is one line away from an AttributeError inside a container, where the
        traceback goes to a log nobody reads."""
        source = self._the_source()
        assert "urllib.error" in source.split("def via_proxy", 1)[0], (
            "urllib.error is used and never imported by name")

    def test_and_the_check_that_reads_the_matrix_still_needs_the_denied_control_refused(self):
        """The safety net under all of the above: if the HTTPError clause ever did swallow a proxy
        refusal, the DENIED control would come back ALLOWED and the property would fail, not pass."""
        from agentnode_sdk.conformance import checks

        matrix = {"allowed_hosts": ["example.com"], "allowed:example.com": "ALLOWED:404",
                  "denied_via_proxy": "ALLOWED:200"}

        class _Ctx:
            host = {"egress_matrix": matrix, "egress_expected": ["example.com"]}
            readings = {}
            probe_failure = None
            inspect = {}
            argv = []
            declared = {}
            stress = {}

        from agentnode_sdk.conformance.report import Outcome

        assert checks.check_egress_allowlist(_Ctx()).outcome is Outcome.FAIL, (
            "a matrix whose denied control came back ALLOWED passed; the net under the HTTPError "
            "clause is not there")
        # The same matrix with the control refused passes, so the assertion above is about the control
        # and not about something else in the matrix being wrong.
        matrix["denied_via_proxy"] = "refused:URLError"
        assert checks.check_egress_allowlist(_Ctx()).outcome is Outcome.PASS


class TestNoHostNetworkAndNoOtherJobsNetwork:
    """EG10, which had no test. The stand shows every container on a named bridge of its own run
    (`E0149`, `E0177`), and that is what it looks like when this holds -- but a reading of two runs is
    not the same as a property, and the two ways it could stop holding are both one word long.

    `--network host` puts a payload on the host's own stack, where the allowlist is not a boundary at
    all and loopback, the gateway and the metadata endpoint are all simply there. And a payload put on
    ANOTHER run's network is inside that run's boundary, which is somebody else's.
    """

    def _argv_for(self, network, egress=None, backend_runtime="podman"):
        from agentnode_sdk.sandbox.container_backend import ContainerBackend
        from agentnode_sdk.sandbox.types import ProcessSpec

        backend = ContainerBackend.__new__(ContainerBackend)
        backend._runtime = backend_runtime
        backend._image = "an-image"
        spec = ProcessSpec(command=["true"], network=network, egress=egress, clean_home=True,
                           name="agentnode-test-one")
        return backend.wrap_command(spec)

    def test_nothing_the_backend_builds_ever_asks_for_the_host_network(self):
        from agentnode_sdk.sandbox.types import EgressSpec

        handle = EgressSpec(network_name="agentnode-egress-aaaa-int",
                            proxy_url="http://10.89.0.1:8888",
                            allowed_domains=("example.com",))
        for network, eg in (("none", None), ("egress", handle)):
            argv = self._argv_for(network, eg)
            flat = " ".join(argv)
            assert "--network host" not in flat and "--net=host" not in flat, flat
            assert "--privileged" not in flat, flat
            # And the one that is easy to miss: `host` as the VALUE of --network, however it is spelled.
            for i, word in enumerate(argv):
                if word in ("--network", "--net"):
                    assert argv[i + 1] != "host", argv
                if word.startswith("--network=") or word.startswith("--net="):
                    assert word.split("=", 1)[1] != "host", argv

    def test_the_network_a_payload_is_put_on_is_its_own_runs(self):
        """The name comes from the handle this run built, so there is no path by which one run's argv
        carries another run's network."""
        from agentnode_sdk.sandbox.types import EgressSpec

        mine = EgressSpec(network_name="agentnode-egress-1111-int",
                          proxy_url="http://10.89.0.1:8888",
                          allowed_domains=("example.com",))
        theirs = EgressSpec(network_name="agentnode-egress-2222-int",
                            proxy_url="http://10.89.1.1:8888",
                            allowed_domains=("example.com",))
        argv = self._argv_for("egress", mine)
        flat = " ".join(argv)
        assert mine.network_name in flat
        assert theirs.network_name not in flat
        # Exactly one network is named, so a payload cannot be on two.
        assert flat.count("--network") == 1, argv

    def test_and_a_source_that_asks_for_the_host_network_is_not_in_the_product(self):
        """Read over the modules that build an argv, because the test above exercises one path and a
        second path could be added that this one would not see."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent / "agentnode_sdk"
        offenders = []
        for path in sorted(root.rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if '"host"' in stripped and ("--network" in stripped or "--net" in stripped):
                    offenders.append("%s: %s" % (path.name, stripped[:120]))
                if "--network=host" in stripped or "--net=host" in stripped:
                    offenders.append("%s: %s" % (path.name, stripped[:120]))
        assert offenders == [], offenders


class TestTheProxyWritesDownWhatItDecided:
    """EG17: a refusal has to be evidenced from the worker's side, not only from the job's own account.

    The kernel's half of that is strong by itself -- the payload's namespace has no default route at
    all, which the live observation on the two machines read with nsenter from outside the container.
    But a refusal BY NAME happens in the proxy, and until it wrote these lines the only record of one
    was the refused program saying it had been refused. That is a boundary taking its own word for it.

    The lines are driven here through the proxy's own handler with a socket double, because the
    alternative is a container and this property is about what the code says, not about podman.
    """

    class _Socket:
        """Just enough socket for one request: it hands over bytes once, then takes an answer."""

        def __init__(self, request: bytes):
            self._request = request
            self.sent = b""
            self.closed = False

        def recv(self, _n):
            out, self._request = self._request, b""
            return out

        def sendall(self, data):
            self.sent += data

        def close(self):
            self.closed = True

    def _decisions(self, request: bytes, allow=("example.com",), capsys=None, monkeypatch=None):
        """Returns the socket, the recognised lines, and EVERYTHING that was printed.

        The third one exists because of counter-check 15. A mutation that appended the whole request
        to the decision line left the secrets on the CONTINUATION lines -- which do not start with
        `egress-proxy ` and were therefore filtered out, so the leak test passed against a proxy that
        was printing a customer's cookie. A check that only inspects the lines it recognises cannot
        see a leak that spills past them.
        """
        from agentnode_sdk.sandbox import egress_proxy

        sock = self._Socket(request)
        egress_proxy._handle(sock, set(allow))
        printed = capsys.readouterr().out
        self.everything_printed = printed
        return sock, [line for line in printed.splitlines() if line.startswith("egress-proxy ")]

    def test_a_refused_destination_is_written_down_with_its_name(self, capsys):
        sock, lines = self._decisions(
            b"CONNECT nobody-allowed.invalid:443 HTTP/1.1\r\nHost: nobody-allowed.invalid\r\n\r\n",
            capsys=capsys)
        assert b"403" in sock.sent
        assert len(lines) == 1, lines
        assert "REFUSED" in lines[0] and "nobody-allowed.invalid:443" in lines[0], lines[0]

    def test_the_allowed_host_on_a_port_nobody_allowed_is_written_down_too(self, capsys):
        sock, lines = self._decisions(
            b"CONNECT example.com:22 HTTP/1.1\r\nHost: example.com:22\r\n\r\n", capsys=capsys)
        assert b"403" in sock.sent
        assert lines and "REFUSED" in lines[0] and "example.com:22" in lines[0], lines

    def test_something_that_is_not_a_connect_is_written_down_as_rejected(self, capsys):
        sock, lines = self._decisions(
            b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n", capsys=capsys)
        assert lines and "REJECTED" in lines[0], lines

    def test_a_name_that_resolves_somewhere_it_may_not_reach_says_SCREENED(self, capsys,
                                                                          monkeypatch):
        """The rebinding case, and it earns its own word: allowed by name, refused by address.

        "refused" and "refused although it was allowed" send a reader to different places, and this
        is the one that means somebody pointed a permitted name at something private.
        """
        from agentnode_sdk.sandbox import egress_proxy

        def _blocked(host, port):
            raise egress_proxy.EgressBlocked("it resolves to 127.0.0.1")

        monkeypatch.setattr(egress_proxy, "resolve_and_screen", _blocked)
        sock, lines = self._decisions(
            b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n", capsys=capsys)
        assert b"403" in sock.sent
        assert lines and "SCREENED" in lines[0], lines
        assert "example.com:443" in lines[0]

    def test_a_screening_says_WHICH_screening_it_was(self, capsys, monkeypatch):
        """Two different events, two different lines -- which is what EG8 needed this log to be.

        `resolve_and_screen` raises `EgressBlocked` for a name that could not be resolved AND for a
        name that resolved to an address this proxy will not reach. The line said the second in both
        cases, so a reader could not tell a rebinding attempt from a broken resolver, and a test
        asserting only "it was screened" passed either way. That matters here more than it looks: EG8
        of the frozen acceptance is specifically about rebinding, and this line is the evidence for it.
        """
        from agentnode_sdk.sandbox import egress_proxy

        def raising(message):
            def _blocked(host, port):
                raise egress_proxy.EgressBlocked(message)
            return _blocked

        monkeypatch.setattr(egress_proxy, "resolve_and_screen",
                            raising("non-public address resolved: 10.0.0.5"))
        sock, lines = self._decisions(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n", capsys=capsys)
        assert b"403" in sock.sent
        assert lines and "SCREENED" in lines[0], lines
        assert "non-public address resolved: 10.0.0.5" in lines[0], (
            "a screening that will not say what it resolved to cannot evidence rebinding: %r"
            % (lines[0],))

        monkeypatch.setattr(egress_proxy, "resolve_and_screen", raising("resolve failed: gaierror"))
        sock, lines = self._decisions(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n", capsys=capsys)
        assert b"403" in sock.sent
        assert lines and "SCREENED" in lines[0], lines
        assert "resolve failed" in lines[0], (
            "a name that could not be resolved is reported as one that resolved somewhere bad: %r"
            % (lines[0],))
        assert "non-public address resolved" not in lines[0], (
            "a resolution FAILURE is being described as a private address: %r" % (lines[0],))

    def test_a_screening_still_does_not_spill_the_request(self, capsys, monkeypatch):
        """And carrying the reason through must not carry anything else through with it.

        The reason now comes from an exception message, which is a string this process composed -- but
        the rule the proxy's own docstring sets is that a decision line holds the destination, the port,
        the decision and the reason, and nothing of the tunnel. Counter-check 15 of an earlier arc
        caught a mutation that spilled a cookie past the recognised lines, so this asks the same
        question of the new path, against everything printed and not only the lines that parse.
        """
        from agentnode_sdk.sandbox import egress_proxy

        def _blocked(host, port):
            raise egress_proxy.EgressBlocked("non-public address resolved: 10.0.0.5")

        monkeypatch.setattr(egress_proxy, "resolve_and_screen", _blocked)
        self._decisions(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n"
            b"Cookie: session=not-in-a-log-please\r\nProxy-Authorization: Basic c2VjcmV0\r\n\r\n", capsys=capsys)
        assert "not-in-a-log-please" not in self.everything_printed, self.everything_printed
        assert "c2VjcmV0" not in self.everything_printed, self.everything_printed
        assert "Cookie" not in self.everything_printed, self.everything_printed

    def test_an_allowed_one_says_so_and_names_the_address_it_went_to(self, capsys, monkeypatch):
        from agentnode_sdk.sandbox import egress_proxy

        monkeypatch.setattr(egress_proxy, "resolve_and_screen",
                            # (family, sockaddr) pairs, which is what `screen_addrinfos` returns -- NOT raw
        # addrinfo tuples. The first version of this double handed over the raw shape, the handler
        # took element [1][0] of it, the TypeError was swallowed by its own outer guard, and the
        # result looked like a proxy that had silently stopped answering. The shape is read off the
        # function rather than guessed.
        lambda host, port: [(2, ("93.184.216.34", 443))])
        monkeypatch.setattr(egress_proxy.socket, "create_connection",
                            lambda *a, **k: self._Socket(b""))
        monkeypatch.setattr(egress_proxy, "_tunnel", lambda a, b: None)
        sock, lines = self._decisions(
            b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n", capsys=capsys)
        assert b"200" in sock.sent
        assert lines and "ALLOWED" in lines[0], lines
        assert "93.184.216.34" in lines[0], (
            "the line does not say which address it actually connected to, which is the one fact "
            "that distinguishes a vetted connection from a second unchecked resolution")

    def test_and_nothing_of_the_request_itself_is_ever_written(self, capsys, monkeypatch):
        """The log is a record of DECISIONS, not of what somebody's code was doing.

        A proxy log that grew headers, paths or query strings would be a record of a customer's
        traffic on the machine that runs other people's code. The request below carries a path, a
        query, a header and a cookie; none of them may appear.
        """
        from agentnode_sdk.sandbox import egress_proxy

        monkeypatch.setattr(egress_proxy, "resolve_and_screen",
                            lambda host, port: [(2, ("93.184.216.34", 443))])
        monkeypatch.setattr(egress_proxy.socket, "create_connection",
                            lambda *a, **k: self._Socket(b""))
        monkeypatch.setattr(egress_proxy, "_tunnel", lambda a, b: None)
        secrets = ("/a-secret-path", "token=SHOULD-NOT-APPEAR", "Cookie:", "sess-abcdef")
        request = (b"CONNECT example.com:443 HTTP/1.1\r\n"
                   b"Host: example.com\r\n"
                   b"X-Where: /a-secret-path?token=SHOULD-NOT-APPEAR\r\n"
                   b"Cookie: sess-abcdef\r\n\r\n")
        _sock, _lines = self._decisions(request, capsys=capsys)
        # EVERYTHING that was printed, not only the lines this test recognises. See `_decisions`.
        everything = self.everything_printed
        for leak in secrets:
            assert leak not in everything, (
                "the proxy log carries %r out of the request" % leak)

    def test_every_decision_the_handler_can_reach_writes_exactly_one_line(self, capsys,
                                                                         monkeypatch):
        """Not "it logs": one line per decision, so a reader counting refusals counts requests.

        Two lines for one request would double every figure drawn from this log; none would make the
        refusal invisible again.
        """
        from agentnode_sdk.sandbox import egress_proxy

        monkeypatch.setattr(egress_proxy, "resolve_and_screen",
                            lambda host, port: [(2, ("93.184.216.34", 443))])

        def _refuses(*a, **k):
            raise OSError("the destination did not answer")

        monkeypatch.setattr(egress_proxy.socket, "create_connection", _refuses)
        sock, lines = self._decisions(
            b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n", capsys=capsys)
        assert b"502" in sock.sent
        assert len(lines) == 1 and "UNREACHED" in lines[0], lines


def _limits_for_the_tail():
    """What the gateway granted, which the usage line records beside the route out."""
    from agentnode_sdk.sandbox.contract import Limits, SandboxPolicy

    return SandboxPolicy(limits=Limits(cpu=1.0, memory_mb=256, wall_clock_s=30))


class TestTheSignedLineHasSomewhereToSayIt:
    def test_the_two_egress_fields_are_part_of_the_declared_schema(self):
        from agentnode_sdk.gateway import meter

        assert "egress" in meter.FIELDS
        assert "egress_sha256" in meter.FIELDS

    def test_the_gateway_writes_the_line_itself_without_raising(self, tmp_path):
        """THE ONE THIS FILE WAS MISSING, and the real two-host run is what found it.

        The test below calls `meter.record` directly. That proves the schema accepts the two fields
        and proves nothing about the code that fills them -- which lives in the gateway's own
        `write_down_what_it_used`, computes the word, computes the digest, and did so with a `hashlib`
        that was not imported. On the two machines a job ran, this tail raised NameError, the gateway
        could not write down what the run used, and it stopped taking work rather than run anything
        else it could not account for. That was the product behaving correctly about my defect.

        So this drives the gateway's own method, with a route out on the record, and asserts the line
        it wrote. A missing import, a renamed field or a digest over the wrong bytes all fail here.
        """
        import json

        from agentnode_sdk.gateway import meter
        from agentnode_sdk.gateway.identity import GatewayState
        from agentnode_sdk.gateway.server import GatewayService, RunRecord
        from tests.test_em3c_gateway import StandInBackend, _store_measurement

        root = tmp_path / "state"
        root.mkdir(parents=True, exist_ok=True)
        state = GatewayState(str(root), version="test")
        service = GatewayService(state, backend=StandInBackend())
        _store_measurement(service)
        try:
            the_route_out = {"owner": {"run": "r-tail", "account": "a1b2c3d4e5f60718",
                                      "epoch": "g9"},
                             "networks": ["int-id", "ext-id"], "proxy": "proxy-id",
                             "readings": [{"what": "the proxy", "kind": "reached"}],
                             "verified_before_the_payload": True, "gone_afterwards": True}
            record = RunRecord(run_id="r-tail", job_id="j", owner_client_id="c",
                               owner_account_id="acct-1")
            record.started_at = 1000.0
            record.finished_at = 1001.0
            record.queued_at = 999.5
            record.route_out = dict(the_route_out)
            service.runs[record.run_id] = record
            # The gateway's own tail. If it raises, this is the failure the two machines saw.
            service.write_down_what_it_used(record, _limits_for_the_tail(), "finished")
            lines = meter.read(state.root)
            mine = [line for line in lines if line.get("run_id") == "r-tail"]
            assert mine, "the gateway wrote no line at all for a run that finished"
            line = mine[-1]
            assert line["egress"] == "allowlist", line
            # The digest is over the worker's own account of what it built, canonically. Recomputed
            # here rather than copied from the line, so a digest over the wrong bytes fails.
            import hashlib

            expected = hashlib.sha256(json.dumps(
                the_route_out, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            assert line["egress_sha256"] == expected, (line["egress_sha256"], expected)
        finally:
            service.close()
            state.close()

    def test_and_a_run_that_never_started_gets_neither_field(self, tmp_path):
        """The other half of the same tail: no enforcement happened, so neither field says one did."""
        from agentnode_sdk.gateway import meter
        from agentnode_sdk.gateway.identity import GatewayState
        from agentnode_sdk.gateway.server import GatewayService, RunRecord
        from tests.test_em3c_gateway import StandInBackend, _store_measurement

        root = tmp_path / "state"
        root.mkdir(parents=True, exist_ok=True)
        state = GatewayState(str(root), version="test")
        service = GatewayService(state, backend=StandInBackend())
        _store_measurement(service)
        try:
            record = RunRecord(run_id="r-refused", job_id="j", owner_client_id="c",
                               owner_account_id="acct-1")
            record.queued_at = 999.0
            # started_at stays 0.0: this run never ran.
            service.runs[record.run_id] = record
            service.write_down_what_it_used(record, _limits_for_the_tail(), "refused")
            line = [x for x in meter.read(state.root) if x.get("run_id") == "r-refused"][-1]
            assert line["egress"] == "", (
                "a job that never ran is recorded as having had a boundary of %r" % line["egress"])
            assert line["egress_sha256"] == ""
        finally:
            service.close()
            state.close()

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
