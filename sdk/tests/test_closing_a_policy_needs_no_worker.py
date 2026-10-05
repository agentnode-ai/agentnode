"""PA2, driven against the real transaction -- which is what the tests it replaces did not do.

## Why this file exists at all

`a9-repair/frozen/binding.json` is a repair profile for `A9` of the sealed acceptance bundle
`beta-readiness-r2`. PA2 of the activation profile says a policy that grants NO destination is put in
force without a worker measurement, "nothing is granted, so there is nothing to prove". The code for that
was present and its predicate was right, and it could not run: the branch called `report_binding()`,
which asks the worker five questions, so the refusal escaped and the close was lost. `E0280` of that
bundle is a real host parked with an allow-list still in force because of it.

The three tests that were supposed to cover PA2 lived in `TestClosingAPolicyIsNeverBlocked` in
`test_cleanup_is_the_products_own.py`, under the docstring "driven against the transaction". One asked
`_grants_nothing` about two envelopes. One built a `Readiness` and read its own field back. One asserted
that two strings appear in a certain ORDER in the CLI's source text. **None called `activate()`**, so all
three passed for eleven weeks while the path they belonged to could not execute. They are deleted, not
kept alongside: a test that cannot fail occupies the place a real one would have gone.

So every test here calls `activate()` on a real `GatewayService` with a real state directory, and asserts
on what the gateway then STORES and ANSWERS -- never on what its source text says.

## What a worker is, in each test

Three shapes, and which one a test uses is the test's whole setup:

* `a_worker_that_is_gone()` -- raises on EVERY attribute access, including ones nobody has thought of.
  B1 is about not depending on which exception a worker error happens to be, so the test must not be able
  to pass by naming the right methods. The one exception is `topology`, which the gateway reads from its
  own configuration and which needs nothing on the far side.
* `a_worker_that_counts()` -- the product's own worker, wrapped so the test can say whether `measure` was
  called. B4 needs to show the measured path is still taken when there is something to measure.
* the product's own worker, untouched -- the control.
"""
from __future__ import annotations

import pytest

from agentnode_sdk.gateway import operator_policy as opol
from agentnode_sdk.gateway.activation import ActivationStore
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.readiness import ReportBinding
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend

#: The worker's own words on the host where this was found, kept so a stand-in refuses the way the real
#: one refused. `worker/tls.py` wraps the PKI's `PeerRefused` from the revoked check into a
#: `WorkerUnreachable` carrying exactly this.
REFUSED = ("the sandbox worker at tcps://10.0.0.2:8443 was refused: the peer was refused at the revoked "
           "check (it presented agentnode://r4-20261005/worker/w5): its certificate is revoked. Nothing "
           "was sent, and nothing else was tried.")

#: The five fields only the worker can answer. Every one of them is a request over the wire when the
#: worker is on its own host: `can_it_isolate`, `image_digest`, `boot_id`, `runtime_version` and
#: `configuration_sha256` all go through the remote worker's `_describe()`.
ONLY_THE_WORKER_KNOWS = ("backend", "backend_version", "image_digest", "worker_boot_id",
                         "worker_configuration_sha256")


class ARefusalNobodyCatchesByType(BaseException):
    """A BaseException that is not an Exception, for the case B1 is really about.

    THE FIRST VERSION OF THAT CASE RAISED `KeyboardInterrupt`, which made the point and broke the
    measurement. pytest treats `KeyboardInterrupt` as "stop this session", so against the UNREPAIRED
    product -- where the repair is not there to catch it -- the whole run aborted after two tests and
    B6's accounting could not be taken at all (`E0088`). On the repaired product the branch catches it
    before pytest ever sees it, which is exactly why the problem stayed invisible until the full
    reversion was run.

    The claim being tested is that the repair depends on no exception TYPE: not that it survives the
    one exception the test runner has reserved for itself.
    """


class AWorkerThatIsGone:
    """Refuses every attribute access. `topology` is the one thing the gateway owns itself."""

    def __init__(self, topology: str) -> None:
        self._topology = topology
        self.what_was_asked: list = []

    @property
    def topology(self) -> str:
        return self._topology

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        self.what_was_asked.append(name)
        raise RuntimeError(REFUSED)


class AWorkerThatCounts:
    """The real worker, with a tally of whether it was asked to measure."""

    def __init__(self, real) -> None:
        self._real = real
        self.measure_calls = 0

    def measure(self, *a, **k):
        self.measure_calls += 1
        return self._real.measure(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture()
def gateway(tmp_path):
    """A service, its real worker, and the class property put back afterwards.

    The property is restored rather than deleted. A first version of the reproduction used `del
    GatewayService.worker`, which removed the class's own property and made the NEXT case fail with "no
    attribute 'worker'" -- one test deciding the next.
    """
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    the_real_property = GatewayService.worker
    real = service.worker
    try:
        yield service, real
    finally:
        GatewayService.worker = the_real_property
        state.close()


def put_this_worker_on(service, worker):
    GatewayService.worker = property(lambda _self: worker)
    return worker


def the_closed_policy():
    return opol.build(opol.NONE, (), {}, None)


def the_stored(service) -> object:
    return ActivationStore(service.state.root).load_active()


# --------------------------------------------------------------------------------------------- B1


class TestAClosedPolicyGoesInForceWithNoWorkerAtAll:
    """B1. Not "while the worker raises WorkerUnreachable" -- while it answers nothing whatsoever."""

    def test_it_is_activated_and_the_state_says_so(self, gateway):
        service, real = gateway
        put_this_worker_on(service, AWorkerThatIsGone(real.topology))

        verdict = service.activate(the_closed_policy())

        assert verdict.in_force is True, "the close did not take effect, which is A9"
        assert verdict.ready is False, "an unmeasured policy must never be reported as proven"
        stored = the_stored(service)
        assert stored is not None
        assert stored.policy.mode == opol.NONE
        assert tuple(stored.policy.allowed_destinations or ()) == ()

    def test_the_worker_was_asked_nothing_that_needs_the_far_side(self, gateway):
        """The claim is not "it survived an error" but "it did not ask". Those differ."""
        service, real = gateway
        gone = put_this_worker_on(service, AWorkerThatIsGone(real.topology))

        service.activate(the_closed_policy())

        # `measure` and `measure_egress` ARE asked -- that attempt is what fails and sends the
        # transaction down this branch. What must not be asked is anything the BINDING wanted.
        for name in ("can_it_isolate", "image_digest", "boot_id", "runtime_version",
                     "configuration_sha256"):
            assert name not in gone.what_was_asked, (
                "the binding asked the worker for %s, which is the defect A9 names" % name)

    def test_no_exception_type_is_relied_on(self, gateway):
        """A worker whose failure is a BaseException nobody catches by name still closes."""
        service, real = gateway

        class AWorkerThatFailsStrangely(AWorkerThatIsGone):
            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(name)
                self.what_was_asked.append(name)
                raise ARefusalNobodyCatchesByType("not an exception anyone catches by type")

        put_this_worker_on(service, AWorkerThatFailsStrangely(real.topology))
        verdict = service.activate(the_closed_policy())
        assert verdict.in_force is True


# --------------------------------------------------------------------------------------------- B2


class TestTheBindingClaimsNothingAboutAWorkerNobodyAsked:
    """B2. An empty field is the honest value; a named image would be a worse defect."""

    def test_the_five_worker_fields_are_empty(self, gateway):
        service, real = gateway
        put_this_worker_on(service, AWorkerThatIsGone(real.topology))
        service.activate(the_closed_policy())

        binding = the_stored(service).binding
        for name in ONLY_THE_WORKER_KNOWS:
            assert binding.get(name, "") == "", (
                "%s names something about a worker that was never asked" % name)

    def test_and_the_gateway_s_own_fields_are_filled(self, gateway):
        service, real = gateway
        put_this_worker_on(service, AWorkerThatIsGone(real.topology))
        closed = the_closed_policy()
        service.activate(closed)

        binding = the_stored(service).binding
        assert binding.get("gateway_id"), "the binding does not say which gateway it is about"
        assert binding.get("gateway_boot_id"), "the binding does not say which boot signed it"
        assert binding.get("conformance_schema"), "the binding does not say which vocabulary it speaks"
        assert binding.get("operator_policy_digest") == closed.digest(), (
            "the binding is not about the policy that was activated")
        assert binding.get("worker_topology") == real.topology, (
            "the topology is this gateway's own configuration and should survive")

    def test_report_binding_asks_nothing_when_told_nothing_was_measured(self, gateway):
        """The unit underneath, so a failure here says which half broke."""
        service, real = gateway
        gone = put_this_worker_on(service, AWorkerThatIsGone(real.topology))

        binding = service.report_binding("adigest", measured=False)

        assert gone.what_was_asked == [], "report_binding(measured=False) reached for the worker"
        assert all(getattr(binding, name) == "" for name in ONLY_THE_WORKER_KNOWS)


# --------------------------------------------------------------------------------------------- B3


class TestAClosedPolicyCanNeverLookMeasured:
    """B3. Two independent reasons, each asserted on its own rather than as one verdict."""

    def test_it_is_not_ready_once_the_worker_is_back(self, gateway):
        service, real = gateway
        put_this_worker_on(service, AWorkerThatIsGone(real.topology))
        service.activate(the_closed_policy())

        # the worker comes back
        GatewayService.worker = property(lambda _self: real)
        verdict = service._what_the_measurement_proves()

        assert verdict.ready is False
        assert verdict.in_force is False or verdict.ready is False

    def test_reason_one_the_report_proves_no_property(self, gateway):
        service, real = gateway
        put_this_worker_on(service, AWorkerThatIsGone(real.topology))
        closed = the_closed_policy()
        service.activate(closed)

        report = the_stored(service).report
        assert report.get("is_conformant") is False, (
            "an unmeasured report claims to be conformant")
        assert not report.get("results"), "an unmeasured report must carry no results"
        assert closed.required_properties, "this policy requires properties, or B3 proves nothing"

    def test_reason_two_the_empty_binding_drifts_from_a_real_one(self, gateway):
        """Independent of the property gate: the stored binding cannot match a live one."""
        service, real = gateway
        put_this_worker_on(service, AWorkerThatIsGone(real.topology))
        closed = the_closed_policy()
        service.activate(closed)
        stored_dict = the_stored(service).binding

        GatewayService.worker = property(lambda _self: real)
        live = service.report_binding(closed.digest())
        stored = ReportBinding(**{k: str(v) for k, v in stored_dict.items()
                                  if k in ReportBinding.__dataclass_fields__})
        drift = live.mismatches(stored)

        assert drift, "an empty binding matched a live one, so a closed policy could look measured"
        assert set(drift) & set(ONLY_THE_WORKER_KNOWS), (
            "the drift is not in the fields the worker owns, so it is not the guarantee B3 claims")

    def test_admission_still_refuses(self, gateway):
        service, real = gateway
        put_this_worker_on(service, AWorkerThatIsGone(real.topology))
        service.activate(the_closed_policy())

        GatewayService.worker = property(lambda _self: real)
        assert service._what_the_measurement_proves().ready is False


# --------------------------------------------------------------------------------------------- B4


class TestNothingElseBecomesActivatableWithoutAMeasurement:
    """B4. The hole opened is exactly one policy wide, in exactly one direction."""

    def test_a_policy_that_grants_something_is_still_refused(self, gateway):
        """Asserted on the PROPERTY, not on the mechanism, and the difference was measured.

        A first version wrapped the call in `pytest.raises(BaseException)` and only then asked what
        was in force. Counter-check 5 -- `_grants_nothing` made to accept any policy -- then failed
        with `DID NOT RAISE BaseException` instead of with the reason the check is about, recorded at
        `E0033` as RED FOR THE WRONG REASON. The mutation had done exactly the dangerous thing, put a
        granting policy in force unmeasured, and the test reported the wrong complaint about it.
        Whether this refusal arrives as an exception or as a verdict is the mechanism; that nothing
        was granted is the property.
        """
        service, real = gateway
        put_this_worker_on(service, AWorkerThatIsGone(real.topology))

        try:
            service.activate(opol.build(opol.RESTRICTED, ("pypi.org",), {}, None))
        except BaseException:                                     # noqa: BLE001
            pass

        assert the_stored(service) is None, "a granting policy was put in force with no measurement"

    def test_the_previous_policy_is_left_in_force(self, gateway):
        """The close first, so there IS something to leave alone, then a refused widening."""
        service, real = gateway
        put_this_worker_on(service, AWorkerThatIsGone(real.topology))
        service.activate(the_closed_policy())
        before = the_stored(service).policy.digest()

        try:
            service.activate(opol.build(opol.RESTRICTED, ("pypi.org",), {}, None))
        except BaseException:                                     # noqa: BLE001
            pass

        assert the_stored(service).policy.digest() == before, (
            "a refused widening changed what was in force")

    def test_a_re_measurement_never_takes_the_unmeasured_path(self, gateway):
        """`_transact(None)` is a re-measurement of what is already configured, not a proposal.

        This is the one caller that could have turned the repair into something worse than the defect:
        the health watch re-measures in the background, and if a re-measurement of an
        already-configured grant-nothing policy could take the unmeasured path, a gateway whose worker
        went away would quietly re-activate its own policy with an empty binding, on a timer, with
        nobody asking. The guard is `proposed is None or not self._grants_nothing(envelope)` -- the
        first half -- and this is what holds it in place.
        """
        service, real = gateway
        put_this_worker_on(service, AWorkerThatIsGone(real.topology))

        try:
            service.activate(None)
        except BaseException:                                     # noqa: BLE001
            pass

        assert the_stored(service) is None, (
            "a re-measurement activated something without measuring it")

    def test_a_reachable_worker_is_still_measured_for_a_closed_policy(self, gateway):
        """The unmeasured path must not be taken merely because it is cheaper."""
        service, real = gateway
        counting = put_this_worker_on(service, AWorkerThatCounts(real))

        service.activate(the_closed_policy())

        assert counting.measure_calls == 1, (
            "a closed policy skipped the measurement although the worker could be measured")


# --------------------------------------------------------------------------------------------- B5


class TestTheRecordSaysItWasNotMeasuredAndWhy:
    """B5. In the stored report, not only in what the command printed."""

    def test_the_stored_report_says_it_was_not_measured_and_why(self, gateway):
        service, real = gateway
        put_this_worker_on(service, AWorkerThatIsGone(real.topology))
        service.activate(the_closed_policy())

        report = the_stored(service).report
        assert report.get("not_measured") is True
        why = str(report.get("why") or "")
        assert why, "the record does not say why it was activated without a measurement"
        assert "grants" in why and "nothing" in why, (
            "the reason does not say that nothing is granted, which is the whole justification")

    def test_no_pending_proposal_is_left_beside_a_policy_in_force(self, gateway):
        service, real = gateway
        put_this_worker_on(service, AWorkerThatIsGone(real.topology))
        service.activate(the_closed_policy())

        assert not ActivationStore(service.state.root).pending_path.is_file(), (
            "a pending record beside a policy in force makes every later report say a change is "
            "proposed and not in force")

    def test_closing_an_already_closed_policy_says_the_same_thing(self, gateway):
        service, real = gateway
        put_this_worker_on(service, AWorkerThatIsGone(real.topology))

        first = service.activate(the_closed_policy())
        second = service.activate(the_closed_policy())

        assert first.in_force is True and second.in_force is True
        assert the_stored(service).policy.mode == opol.NONE
        assert not ActivationStore(service.state.root).pending_path.is_file()


# --------------------------------------------------------------------------------------------- B5, the command


class TestTheCommandDoesNotSayNothingWasChanged:
    """What the old source-text test was reaching for, driven through the command instead.

    The test this replaces asserted that two strings appear in a certain order in
    `gateway_commands`' source. That is true of a file whose branches can never be reached. This runs
    the command and reads what it printed.
    """

    def test_it_says_the_policy_is_in_force(self, gateway, capsys, monkeypatch):
        service, real = gateway
        put_this_worker_on(service, AWorkerThatIsGone(real.topology))

        from agentnode_sdk.cli import gateway_commands

        monkeypatch.setattr(gateway_commands, "_service",
                            lambda root: (service.state, service), raising=True)
        monkeypatch.setattr(gateway_commands, "_root",
                            lambda args: service.state.root, raising=True)

        class Args:
            none = True
            allow = ()
            verbose = False
            dir = str(service.state.root)

        code = gateway_commands.cmd_egress(Args())
        said = capsys.readouterr().out

        assert "grants nothing, and it is now in force" in said
        assert "Nothing was changed" not in said, (
            "the command said nothing was changed while the policy had been changed")

        # EXIT 0, AND WHAT IT MEANS. A first version of this test asserted `code != 0`, reasoning
        # that a gateway which will not run anything must not report success. That was wrong, and
        # reading the command rather than arguing about it is what settled it: before returning,
        # `cmd_egress` calls `_the_close_took_effect(root)`, which reads back what is IN FORCE --
        # not what was asked -- and returns 1 if it still grants any destination. So 0 here is not
        # "all is well on this gateway"; it is "the close you asked for took effect, and I checked".
        # The gateway's unreadiness is reported in the text above and by readiness itself, which
        # the B3 tests drive. Asserting non-zero would have demanded the command lie about whether
        # it did what it was told.
        assert code == 0, (
            "the close took effect but the command reported failure, which would make every park "
            "script treat a good close as a bad one")
        took, granted = gateway_commands._the_close_took_effect(service.state.root)
        assert took is True and granted == [], (
            "the command returned 0 without the in-force policy actually granting nothing")
