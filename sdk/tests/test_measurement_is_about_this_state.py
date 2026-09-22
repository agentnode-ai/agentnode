"""A stored measurement stops being evidence the moment anything it was taken under changes.

The earlier evidence showed `ReportBinding.mismatches` noticing each field. A reviewer was right
that this is a claim about a comparison function rather than about the gateway: what matters is
whether the GATE, asked afterwards, refuses to be ready -- and whether the gateway, asked to run
something, refuses to run it.

So every test here changes one bound input, then asks `readiness_now()` and `submit()`. The field
list is read off `ReportBinding` rather than typed out, so a field added later is covered without
anybody remembering to come back.
"""
from __future__ import annotations

import dataclasses

import pytest

from agentnode_sdk.access import dispatch
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.readiness import ReportBinding
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement
from tests.test_two_accounts import _a_customer, _a_run_by


@pytest.fixture()
def gateway(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    try:
        yield service
    finally:
        state.close()


#: What a measurement is about. Read off the dataclass so a field added later is covered.
BOUND = tuple(f.name for f in dataclasses.fields(ReportBinding))


class TestTheBindingCoversWhatTheWorkOrderNames:

    def test_each_of_them_is_actually_in_the_binding(self):
        for named in ("operator_policy_digest", "operator_policy_version",
                      "worker_configuration_sha256", "backend", "backend_version",
                      "image_digest", "worker_topology", "gateway_id", "gateway_version",
                      "boot_id", "conformance_schema"):
            assert named in BOUND, "%s is not part of what a measurement is about" % named

    def test_and_the_gateway_fills_in_everything_it_can_answer(self, gateway):
        """Blank only where the WORKER has nothing to say, and asked of the worker.

        The stand-in backend has no image and no runtime version, and on a gateway with a real
        container runtime both are filled -- the deployed alpha reports a pinned image digest.
        So the exemption is derived from the worker rather than written as a list of names,
        which is what stops it becoming a place to hide a field nobody fills in.
        """
        said = gateway.report_binding().as_dict()
        cannot_answer = set()
        if not gateway.worker.image_digest():
            cannot_answer.add("image_digest")
        if not gateway.runtime_version():
            cannot_answer.add("backend_version")
        # An installation with no runtime pin cannot name the commit it was built from or the
        # build identity derived from it. Derived from the machine, exactly like the two above,
        # rather than written as a list of names -- a list is where a field nobody fills in goes
        # to hide, and this test exists to stop that.
        from agentnode_sdk.gateway import runtime_pin

        try:
            runtime_pin.read_pin(gateway.state.root)
        except Exception:                                         # noqa: BLE001
            cannot_answer |= {"commit", "build_id"}
            if not runtime_pin.installed_artefact_digest():
                cannot_answer.add("artefact_sha256")

        empty = {name for name, value in said.items() if not str(value or "").strip()}
        assert empty <= cannot_answer, (
            "these are part of the binding and this gateway leaves them blank although its "
            "worker can answer them, so a change to them would not be noticed: %s"
            % sorted(empty - cannot_answer))

    def test_and_a_blank_one_is_still_BOUND_rather_than_ignored(self, gateway):
        """A field this worker cannot answer must still be compared, or it is a hole."""
        import dataclasses as _d

        was = gateway.report_binding()
        for name in ("image_digest", "backend_version"):
            moved = _d.replace(was, **{name: "now-there-is-one"})
            assert name in was.mismatches(moved), (
                "%s is blank here and comparing it does nothing, so a gateway that gained one "
                "would go on using a report taken without it" % name)
            assert not gateway.readiness.evaluate(
                moved, gateway.operator_envelope().required_properties).ready


class TestChangingAnyBoundValueStopsTheMeasurementCounting:

    @pytest.mark.parametrize("field_name", BOUND)
    def test_the_gate_refuses(self, gateway, field_name):
        """Through the GATE, not through the comparison function."""
        assert gateway.readiness_now().ready, "the fixture did not start ready"

        moved = dataclasses.replace(gateway.report_binding(),
                                    **{field_name: "something-else-entirely"})
        said = gateway.readiness.evaluate(moved, gateway.operator_envelope()
                                               .required_properties)
        assert not said.ready, (
            "%s changed and the stored measurement was still read as describing this gateway"
            % field_name)
        # The REASON is prose a person reads -- "this machine has restarted since it was last
        # measured" rather than "boot_id". Which field moved is `mismatches`, tested below;
        # what is required here is that the refusal says something and names a way out.
        assert said.reason.strip(), field_name
        assert said.next_steps, "refused with nothing to do about it: %s" % field_name

    @pytest.mark.parametrize("field_name", BOUND)
    def test_and_says_which_one_moved(self, gateway, field_name):
        was = gateway.report_binding()
        moved = dataclasses.replace(was, **{field_name: "something-else-entirely"})
        assert field_name in was.mismatches(moved)


class TestAChangeTheGatewayCanReallyMake:
    """Not a synthesised binding: change the thing, and ask the gateway."""

    def test_changing_the_operator_policy_invalidates_the_measurement(self, gateway):
        from agentnode_sdk.gateway import operator_policy as opol

        assert gateway.readiness_now().ready
        was = gateway.report_binding().operator_policy_digest
        assert was, "the measurement is not bound to any policy at all"

        # A different policy: one that allows an allowlist where the gateway had none.
        opened = opol.build(opol.RESTRICTED, ("api.example",))
        assert opened.digest() != was
        moved = dataclasses.replace(gateway.report_binding(),
                                    operator_policy_digest=opened.digest())
        said = gateway.readiness.evaluate(moved, opened.required_properties)
        assert not said.ready, (
            "a report taken while this gateway allowed no network was accepted as evidence "
            "after an allowlist was opened")

    def test_and_the_ORDINAL_moves_with_it(self, gateway):
        from agentnode_sdk.gateway import operator_policy as opol
        from agentnode_sdk.gateway import policy_version

        root = gateway.state.root
        first = policy_version.version_for(root, opol.build(opol.NONE).digest())
        second = policy_version.version_for(
            root, opol.build(opol.RESTRICTED, ("api.example",)).digest())
        assert first != second and first > 0 and second > 0
        # And the same policy keeps its own number rather than being counted twice.
        assert policy_version.version_for(root, opol.build(opol.NONE).digest()) == first

    def test_a_reader_does_not_assign_a_number_by_looking(self, gateway):
        """`known_version` exists so verifying does not create an ordering entry."""
        from agentnode_sdk.gateway import policy_version

        unseen = "f" * 64
        assert policy_version.known_version(gateway.state.root, unseen) == policy_version.UNKNOWN
        assert policy_version.known_version(gateway.state.root, unseen) == policy_version.UNKNOWN

    def test_changing_the_worker_configuration_invalidates_it(self, gateway):
        was = gateway.report_binding()
        assert was.worker_configuration_sha256, "the measurement names no worker configuration"
        moved = dataclasses.replace(was, worker_configuration_sha256="0" * 64)
        assert not gateway.readiness.evaluate(
            moved, gateway.operator_envelope().required_properties).ready

    def test_and_a_job_is_REFUSED_rather_than_run_on_stale_evidence(self, gateway):
        """The end a customer meets: not "not ready" in a status, a refused submission."""
        who = _a_customer(gateway, "alice")
        _a_run_by(gateway, who)                               # it works while the report holds

        # Replace the stored report with one taken under a different image.
        _store_measurement(gateway, binding=dataclasses.replace(
            gateway.report_binding(), image_digest="sha256:" + "0" * 64))
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, who)
        assert refused.value.refusal in ("sandbox_unavailable", "gateway_stopped")
        assert refused.value.what_to_do

    def test_a_measurement_from_another_gateway_is_somebody_elses(self, gateway):
        moved = dataclasses.replace(gateway.report_binding(), gateway_id="0" * 32)
        assert not gateway.readiness.evaluate(
            moved, gateway.operator_envelope().required_properties).ready


class TestUnmeasuredIsFalseRatherThanUnknown:

    def test_a_property_nothing_measured_does_not_hold(self, gateway):
        measured = gateway.measured_properties()
        assert measured.get("a-property-nobody-measured", False) is False

    def test_a_property_that_was_CLAIMED_rather_than_observed_does_not_hold(self, tmp_path):
        state = GatewayState(str(tmp_path / "claimed"), version="test")
        try:
            service = GatewayService(state, backend=StandInBackend())
            _store_measurement(service, observed=False)
            assert not service.readiness_now().ready, (
                "a report the SDK merely stated about itself was accepted as a measurement")
        finally:
            state.close()

    def test_a_property_that_was_measured_and_FAILED_does_not_hold(self, tmp_path):
        state = GatewayState(str(tmp_path / "failed"), version="test")
        try:
            service = GatewayService(state, backend=StandInBackend())
            _store_measurement(service, ok=False)
            assert not service.readiness_now().ready
        finally:
            state.close()

    def test_and_no_stored_report_at_all_is_not_a_passing_one(self, tmp_path):
        state = GatewayState(str(tmp_path / "bare"), version="test")
        try:
            said = GatewayService(state, backend=StandInBackend()).readiness_now()
            assert not said.ready and said.reason
        finally:
            state.close()
