"""R8 and R11: a policy a running gateway was not started with.

## What these are about

`beta-readiness-r3/frozen/repair-profile.json` is the prospective profile for this repair, frozen before
any code was changed. Two findings of acceptance run `r5-20261006`:

* **R8** -- a running gateway admits against the policy it was STARTED with. `cli/gateway_commands._service`
  built every service with `operator_policy=_operator_policy(root)`, read from `config.json` at
  construction, so `GatewayService.configured_envelope()` returned that start-up list for the life of the
  process. A policy set through the published command was written, measured, activated and reported in
  force, and every job was refused with *"what this gateway is configured to allow is not what was measured
  and put into force"* until the service was restarted. `gateway doctor --measure`, which the refusal
  prescribes, could not clear it.
* **R11** -- the same cause reaching the operator commands. `_transact` ends a successful activation by
  returning `self._what_the_measurement_proves()`, which compares `configured_envelope()` against the
  snapshot it has just written. With the envelope frozen at construction -- before the command wrote the
  new config -- a change to a DIFFERENT list reported *"Measurement failed"* and *"The previous policy
  remains in force. Nothing was changed."* while the snapshot had in fact advanced a generation. A change to
  the SAME list reported success, because there was nothing to differ about.

## How these tests are built, and why not through the CLI's own command

Every test here constructs its service through **the published path**, `gateway_commands._service(root)`,
rather than by calling `GatewayService(...)` itself. That matters: the defect was in HOW the CLI built the
object, so a test that builds its own service cannot see it, and three of these would pass against the
unrepaired product if they did.

What they do NOT do is run `cmd_egress` end to end, because putting a restricted policy in force for real
needs a measurement, and a measurement needs a container runtime the unit suite does not have. So a
measurement is placed with `_store_measurement` from `test_em3c_gateway`, which builds a REAL
`ConformanceReport` out of REAL `CheckResult`s and activates through the real `ActivationStore` -- the
established way in this suite to exercise the readiness gate instead of bypassing it. Whether a real
measurement reaches those verdicts is the container lane's job.

Each test names the requirement of the frozen profile it answers.
"""
from __future__ import annotations

import json
import threading

import pytest

from agentnode_sdk.cli import gateway_commands
from agentnode_sdk.gateway import operator_policy as opol
from agentnode_sdk.gateway.activation import ActivationStore
from tests.test_em3c_gateway import _store_measurement

ONE = ("pypi.org",)
TWO = ("files.pythonhosted.org", "pypi.org")


def write_the_config(root, hosts) -> None:
    """Write the operator's intent the way the product's own `_write_config_for` writes it.

    Not by calling that method: it belongs to a service, and what is under test is whether a service
    notices a file it did not read at construction. Writing the file directly is how an operator's change
    arrives from the point of view of an already-running process.
    """
    path = root / "config.json"
    config = {}
    if path.is_file():
        config = json.loads(path.read_text(encoding="utf-8"))
    if hosts:
        config["egress_allowed"] = list(hosts)
    else:
        config.pop("egress_allowed", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")


@pytest.fixture()
def serving(tmp_path):
    """A service built the way the published path builds one, over a config that already names a host.

    The order is the one that matters: the config exists FIRST, then the service is constructed, then the
    config changes. That is a running gateway meeting an operator's change.
    """
    from agentnode_sdk.gateway.server import GatewayService

    root = tmp_path / "state"
    root.mkdir(parents=True, exist_ok=True)
    write_the_config(root, ONE)
    state, service = gateway_commands._service(root)
    # THE CLASS PROPERTY IS PUT BACK, not deleted. Some tests here replace `GatewayService.worker` with a
    # worker that answers a chosen report, and a fixture that did not restore it would leave the next test
    # measuring through the previous one's stand-in -- one test deciding another. The same lesson is written
    # into `test_closing_a_policy_needs_no_worker.py`, whose first version used `del` and broke the case
    # after it.
    the_real_property = GatewayService.worker
    try:
        yield root, service
    finally:
        GatewayService.worker = the_real_property
        state.close()


def in_force(service):
    stored = ActivationStore(service.state.root).load_active()
    return () if stored is None else tuple(stored.policy.allowed_destinations or ())


def generation(service) -> int:
    stored = ActivationStore(service.state.root).load_active()
    return 0 if stored is None else int(stored.generation)


# ------------------------------------------------------------------------------------------- RQ2, TD1


class TestARunningServiceReadsTheConfigWhenItLooks:
    """RQ2: a running gateway does not keep a policy read at start-up as the truth."""

    def test_the_configured_envelope_follows_the_file(self, serving):
        """The narrowest statement of R8 there is, and it needs no measurement at all."""
        root, service = serving
        assert tuple(service.configured_envelope().allowed_destinations) == ONE

        write_the_config(root, TWO)

        assert tuple(service.configured_envelope().allowed_destinations) == TWO, (
            "the service is still reporting the list it was constructed with, so a policy change can "
            "never reach it without a restart -- finding R8")

    def test_and_its_digest_follows_the_file_too(self, serving):
        """A list that moved while its digest did not would be worse than either alone."""
        root, service = serving
        before = service.configured_envelope().digest()

        write_the_config(root, TWO)

        assert service.configured_envelope().digest() != before
        assert service.configured_envelope().digest() == opol.build(opol.RESTRICTED, TWO).digest()


class TestRQ2HoldsByConstructionAndNotByConvention:
    """RQ2, after an independent review found it satisfied only by nobody passing a policy.

    The first repair removed the CLI's `operator_policy=` argument, and every reading then came from the file
    and the snapshot -- but only because no caller handed one in any more. `configured_envelope` still
    short-circuited to the handed-in policy first, so any caller that derived one from a state directory
    would have recreated the defect exactly. The review's words: "RQ2 is satisfied only by caller
    convention, not by construction."

    The file now wins whenever there is one. A policy handed in is still the answer when there is no config
    file, because then it genuinely is the only source of record.
    """

    def test_a_handed_in_policy_does_not_override_a_config_file(self, serving):
        root, service = serving
        from agentnode_sdk.sandbox.contract import NetworkRules, SandboxPolicy

        service._operator_policy = SandboxPolicy(
            network=NetworkRules(enabled=True, allowed_destinations=frozenset({"example.org"})))

        assert tuple(service.configured_envelope().allowed_destinations) == ONE, (
            "a policy handed in at construction overrode the config file, so RQ2 holds only as long as "
            "nobody passes one")

        write_the_config(root, TWO)
        assert tuple(service.configured_envelope().allowed_destinations) == TWO, (
            "and it went on overriding the file after the file changed")

    def test_but_it_is_still_the_answer_when_there_is_no_config_file(self, serving):
        """The other side. A repair that ignored a handed-in policy entirely would break the caller the
        branch exists for -- an embedded gateway with no directory of its own -- and would make
        `configured_envelope` and `operator_policy` disagree about the same moment, which RQ3 forbids."""
        root, service = serving
        from agentnode_sdk.sandbox.contract import NetworkRules, SandboxPolicy

        (root / "config.json").unlink()
        service._operator_policy = SandboxPolicy(
            network=NetworkRules(enabled=True, allowed_destinations=frozenset({"example.org"})))

        assert tuple(service.configured_envelope().allowed_destinations) == ("example.org",)


class TestASnapshotIsReadLenientlyAndActedOnStrictly:
    """IR-05 of the independent review: a lenient read must not become a policy acted on.

    `from_document` deliberately does not refuse a snapshot naming a host an allowlist cannot hold, because a
    snapshot that cannot be READ is a gateway that cannot say what it enforces. The review was right that
    this leaves the other half open: the snapshot is what admission composes with.
    """

    @staticmethod
    def put_an_unenforceable_host_in_force(service, host):
        """Activate a snapshot naming `host`, going around the config path on purpose.

        The config path refuses such a host now, which is the point of this test: the question is what
        happens when a snapshot holds one anyway -- an older one, or one written before that check existed.
        """
        from agentnode_sdk.gateway import operator_policy as opol

        envelope = opol.build(opol.RESTRICTED, (host,))
        store = ActivationStore(service.state.root)
        active = store.load_active()
        store.activate(envelope, active.report, active.binding)
        return envelope

    @pytest.mark.parametrize("host", ["10.0.0.2", "localhost"])
    def test_what_is_in_force_becomes_the_closed_policy(self, serving, host):
        root, service = serving
        _store_measurement(service)
        self.put_an_unenforceable_host_in_force(service, host)

        from agentnode_sdk.gateway import operator_policy as opol

        assert service.operator_envelope().mode == opol.NONE, (
            "a snapshot naming %r is being acted on, so an allowlist the proxy would screen is what "
            "admission composes with" % (host,))
        assert not tuple(service.operator_policy().network.allowed_destinations or ()), (
            "the policy admission uses still grants that host")

    @pytest.mark.parametrize("host", ["10.0.0.2", "localhost"])
    def test_and_the_snapshot_is_still_readable(self, serving, host):
        """Fail-closed, not unreadable. A reader must still be able to see what the snapshot says."""
        root, service = serving
        _store_measurement(service)
        self.put_an_unenforceable_host_in_force(service, host)

        stored = ActivationStore(service.state.root).load_active()
        assert stored is not None, "the snapshot became unreadable, which is worse than the mistake in it"
        assert tuple(stored.policy.allowed_destinations) == (host,)


class TestTheRepairLosesNoValidation:
    """What the deleted construction-time call uniquely checked, checked where it moved to.

    `cli/gateway_commands._operator_policy` ran `sandbox.egress.validate_allowed_domains` over the file's
    hosts. That validator refuses an IP literal, `localhost` and raw non-ASCII; the envelope's own
    `_destination` accepts all three, because its hostname pattern is satisfied by `10.0.0.2` and by
    `localhost`. Removing the call for R8 would have dropped those refusals silently, so they are asserted
    here against the path the check moved to -- and these fail if someone later decides the move was
    unnecessary.

    They are about the CEILING, not about the proxy. A policy naming an address by hand cannot be enforced
    as an allowlist, because what the proxy screens is the address a NAME resolves to.
    """

    @pytest.mark.parametrize("host", ["10.0.0.2", "127.0.0.1", "localhost"])
    def test_a_config_naming_something_unenforceable_is_refused_when_read(self, serving, host):
        root, service = serving
        write_the_config(root, (host,))

        with pytest.raises(opol.OperatorPolicyError) as refused:
            service.configured_envelope()

        assert "cannot be enforced as an allowlist" in str(refused.value)

    def test_and_an_ordinary_hostname_is_still_read(self, serving):
        """The other half: a refusal that refuses everything would pass the test above and be useless."""
        root, service = serving
        write_the_config(root, ("files.pythonhosted.org", "pypi.org"))

        assert tuple(service.configured_envelope().allowed_destinations) == TWO

    @pytest.mark.parametrize("host", ["10.0.0.2", "localhost"])
    def test_and_the_published_show_path_refuses_it_too(self, serving, host, capsys):
        """RQ3, and a hole this repair opened before closing it.

        The check was first written as a method of `GatewayService`, called from `configured_envelope`. That
        left `cli/gateway_commands._egress_show` -- which calls `operator_policy.from_config` directly --
        computing a digest from an unvalidated envelope and comparing it with the snapshot, while admission
        refused the same config outright. Two published readings of one moment, disagreeing, which is the
        shape of the defect being repaired.

        So the check lives at the parse, and this asserts that the show path gets the same answer as
        admission rather than a different one.
        """
        root, service = serving
        _store_measurement(service)
        write_the_config(root, (host,))

        code = gateway_commands._egress_show(root, verbose=True)

        said = capsys.readouterr().out
        # THE REASON IS ASSERTED FIRST, and that ordering is the point. Without the check the show path
        # still exits non-zero -- it notices that a different policy is saved than the one in force -- so
        # the exit code alone cannot tell the two situations apart. What distinguishes them is whether it
        # says the saved policy cannot be read at all.
        assert "cannot be read" in said or "cannot be enforced" in said, (
            "the show path did not say that the saved policy is unusable: %r" % (said[-400:],))
        assert code != 0, "the show path reported a config that admission refuses as if it were usable"


# ------------------------------------------------------------------------------------------- RQ1, TD6


class TestAJobAfterAnActivationIsJudgedByThePolicyThatWasActivated:
    """RQ1 and TD6: what admission composes with, immediately before and immediately after."""

    def test_the_operator_envelope_is_the_snapshot_and_not_the_start_up_list(self, serving):
        root, service = serving
        _store_measurement(service)
        assert tuple(service.operator_envelope().allowed_destinations) == ONE
        assert in_force(service) == ONE

        write_the_config(root, TWO)
        _store_measurement(service)

        assert in_force(service) == TWO, "the second measurement did not put the new list in force"
        assert tuple(service.operator_envelope().allowed_destinations) == TWO, (
            "what admission composes with is still the list this process started with, so a job asking "
            "for the newly allowed host would be refused -- finding R8")

    def test_and_the_sandbox_policy_admission_uses_carries_the_new_host(self, serving):
        """One step further down: the object the fold actually composes, not the envelope."""
        root, service = serving
        _store_measurement(service)
        write_the_config(root, TWO)
        _store_measurement(service)

        allowed = service.operator_policy().network.allowed_destinations

        assert "files.pythonhosted.org" in set(allowed), (
            "the policy admission uses does not contain the host the operator allowed and the gateway "
            "measured")


# ------------------------------------------------------------------------------------------- RQ4, TD2


class TestAChangeToADifferentListIsReportedTruthfully:
    """RQ4 and RQ5, and TD2's two sides side by side."""

    def test_after_measuring_a_changed_list_the_gateway_says_it_is_ready(self, serving):
        """R11: this is the reading `_transact` returns, and it said the opposite.

        THE FIRST VERSION OF THIS TEST PASSED AGAINST THE UNREPAIRED PRODUCT, which is why the list in
        force is asserted first. `_store_measurement` activates `service.configured_envelope()` -- so on
        the unrepaired product it re-activated the START-UP list, the frozen envelope then agreed with the
        snapshot it had just written, and `ready` was True while the operator's new list had never gone
        anywhere. Reporting ready is only the right answer if the thing reported ready is the new policy.
        """
        root, service = serving
        _store_measurement(service)

        write_the_config(root, TWO)
        _store_measurement(service)

        assert in_force(service) == TWO, (
            "the measurement went in with the list this process started with, so 'ready' below would be "
            "about the wrong policy")
        proven = service._what_the_measurement_proves()
        assert proven.ready is True, (
            "a policy that was written, measured and activated is reported as not in force: %r"
            % (proven.reason,))
        assert "is not what was measured" not in (proven.reason or "")

    def test_an_unchanged_list_is_still_handled_correctly(self, serving):
        """RQ5. The changed case must not be fixed by breaking the identical case."""
        root, service = serving
        _store_measurement(service)
        was = generation(service)

        write_the_config(root, ONE)
        _store_measurement(service)

        assert in_force(service) == ONE
        assert generation(service) > was, "an identical policy still goes through a real activation"
        assert service._what_the_measurement_proves().ready is True

    def test_a_list_changed_and_NOT_measured_is_correctly_refused(self, serving):
        """The other half of RQ4, and it must keep failing. This is not the defect.

        A config written without an activation behind it is exactly what the refusal exists for: the file
        is an input and the snapshot is the decision. If this test ever goes green the repair has turned a
        fail-closed gateway into one that obeys whatever is on disk.
        """
        root, service = serving
        _store_measurement(service)

        write_the_config(root, TWO)

        proven = service._what_the_measurement_proves()
        assert proven.ready is False
        assert "is not what was measured" in (proven.reason or "")
        assert in_force(service) == ONE, "an unmeasured file change must not reach the snapshot"


# ------------------------------------------------------------------------------------------- RQ3, TD4


class TestStoredMeasuredReportedAndUsedAgree:
    """RQ3 and TD4: four readings of the same moment, which must be one answer."""

    def test_all_four_agree_after_an_activation(self, serving):
        """THE FIRST READING IS TAKEN BEFORE THE CHANGE, and that is not incidental.

        Its first version wrote the new config and only then asked anything, so a service that read the
        file once and cached the answer would have cached the NEW list and agreed with itself. The
        reversion that caches the envelope was therefore not caught by this test (`E0020`), which is the
        counter-check doing its job. A running gateway has already read the file by the time an operator
        changes it, so the test now reads first too.
        """
        root, service = serving
        asked_before_the_change = service.configured_envelope().digest()

        write_the_config(root, TWO)
        _store_measurement(service)

        assert service.configured_envelope().digest() != asked_before_the_change, (
            "the envelope is the one this service had already read, so the four readings below would "
            "agree on a policy nobody set")

        stored = ActivationStore(service.state.root).load_active()
        configured = service.configured_envelope()
        used = service.operator_envelope()

        assert configured.digest() == stored.policy_digest, "stored and configured disagree"
        assert used.digest() == stored.policy_digest, "what is used and what is stored disagree"
        assert tuple(configured.allowed_destinations) == tuple(used.allowed_destinations) == TWO
        assert service._what_the_measurement_proves().ready is True


# ------------------------------------------------------------------------------------------- RQ6, TD3


class TestAFailedMeasurementChangesNothing:
    """RQ6: the last demonstrably valid policy stays in force. No intermediate state, no fail-open.

    THE FAILURE IS MADE BY THE PRODUCT'S OWN GATE, not by this machine, and the two versions before this one
    are why. The first asked for a restricted policy and asserted on the returned verdict; on this
    workstation that call never returns, because the real egress path starts a proxy container and there is
    no runtime, so it raises out of `_transact`. The second accepted either outcome -- a verdict or an
    exception -- and an independent review was right that this is not good enough: it called a perfectly
    valid policy "a measurement that cannot succeed", took its failure from a missing runtime, and would
    invert on a machine that has one. Catching `BaseException` made it worse, letting any unrelated error
    pass for the failure under test.

    So the measurement is made to fail by the one thing that decides it: a worker that measures and reports
    NOTHING established. The report is built by the product's own `ConformanceReport` out of real
    `CheckResult`s, as `_store_measurement` does, so the readiness gate sees the shape it really sees. The
    policy is valid, the measurement runs, and the gate refuses it -- on any machine.
    """

    @staticmethod
    def a_worker_whose_measurement_establishes_nothing(service):
        """The real worker, with `measure` answering a report in which no property holds."""
        from agentnode_sdk.conformance.report import CheckResult, ConformanceReport, Vantage
        from agentnode_sdk.gateway.readiness import PROPERTY_CHECKS

        real = service.worker
        every_check = sorted({c for ids in PROPERTY_CHECKS.values() for c in ids})

        class ItMeasuresAndNothingHolds:
            def measure(self, *_a, **_k):
                results = tuple(CheckResult.measured(c, c, "test", False, Vantage.INSIDE,
                                                     "stated by the test")
                                for c in every_check)
                return ConformanceReport(
                    backend_identity="StandInBackend", backend_version="test", runtime="docker",
                    image="", generated_at="1970-01-01T00:00:00+00:00", results=results).to_dict()

            def measure_egress(self, *_a, **_k):
                # No matrix, which is what a closed or unmeasurable allowlist yields. The point of this
                # worker is the report above; this only keeps the call from reaching a real proxy.
                return None

            def __getattr__(self, name):
                return getattr(real, name)

        from agentnode_sdk.gateway.server import GatewayService

        GatewayService.worker = property(lambda _self: ItMeasuresAndNothingHolds())
        return real

    @staticmethod
    def a_measurement_that_the_gate_refuses(service, hosts):
        """A valid policy, measured, and refused by the gate. No exception is expected or caught."""
        return service.activate(opol.build(opol.RESTRICTED, hosts))

    def test_the_config_and_the_snapshot_are_both_left_alone(self, serving):
        root, service = serving
        _store_measurement(service)
        before_file = (root / "config.json").read_text(encoding="utf-8")
        before_force, before_gen = in_force(service), generation(service)
        self.a_worker_whose_measurement_establishes_nothing(service)

        verdict = self.a_measurement_that_the_gate_refuses(service, TWO)

        assert verdict.ready is False, "a measurement in which nothing held was reported as ready"
        assert in_force(service) == before_force, "a failed measurement moved the policy in force"
        assert generation(service) == before_gen, "a failed measurement advanced the generation"
        assert (root / "config.json").read_text(encoding="utf-8") == before_file, (
            "a failed measurement left the operator's intent changed, so the next restart would build "
            "from a policy that was never activated")

    def test_and_nothing_is_left_pending(self, serving):
        root, service = serving
        _store_measurement(service)
        self.a_worker_whose_measurement_establishes_nothing(service)

        self.a_measurement_that_the_gate_refuses(service, TWO)

        assert not ActivationStore(service.state.root).pending_path.is_file(), (
            "a pending record left behind makes every later report say a change is proposed")

    def test_and_a_measurement_in_which_everything_holds_is_NOT_refused(self, serving):
        """The other side, so the two tests above are not satisfied by a gate that refuses everything.

        Without this a repair that made `activate` always fail would pass both of them. The same worker
        answers a report in which every property DOES hold, and the policy goes in force.
        """
        root, service = serving
        _store_measurement(service)
        before_gen = generation(service)

        from agentnode_sdk.conformance.report import CheckResult, ConformanceReport, Vantage
        from agentnode_sdk.gateway.readiness import PROPERTY_CHECKS
        from agentnode_sdk.gateway.server import GatewayService

        real = service.worker
        every_check = sorted({c for ids in PROPERTY_CHECKS.values() for c in ids})

        class ItMeasuresAndEverythingHolds:
            def measure(self, *_a, **_k):
                results = tuple(CheckResult.measured(c, c, "test", True, Vantage.INSIDE,
                                                     "stated by the test")
                                for c in every_check)
                return ConformanceReport(
                    backend_identity="StandInBackend", backend_version="test", runtime="docker",
                    image="", generated_at="1970-01-01T00:00:00+00:00", results=results).to_dict()

            def measure_egress(self, *_a, **_k):
                return None

            def __getattr__(self, name):
                return getattr(real, name)

        GatewayService.worker = property(lambda _self: ItMeasuresAndEverythingHolds())

        verdict = service.activate(opol.build(opol.RESTRICTED, TWO))

        assert verdict.ready is True, (
            "a measurement in which every property held was still refused: %r" % (verdict.reason,))
        assert in_force(service) == TWO, "the policy did not go in force after a measurement that held"
        assert generation(service) > before_gen


# ------------------------------------------------------------------------------------------- RQ7, TD5


class TestConcurrentUpdatesLoseNoGeneration:
    """RQ7: no generation lost, reused, moved backwards, and no two policies mixed.

    THIS CLASS WAS REWRITTEN AFTER AN INDEPENDENT REVIEW CALLED ITS PREVIOUS FORM OUT, and the criticism was
    exact. The test was named for two concurrent updates while its body expressly removed the race -- it held
    the lock, attempted one change, and asserted that the generation did NOT move. That is a true statement
    about a refusal and the opposite of what RQ7 and TD5 ask, which is that two updates racing lose no
    generation, reuse none, move none backwards and mix nothing. A test that reports a stronger answer than
    it can produce is the shape this arc keeps finding in its own instruments, and that was its sixth
    instance.

    Both questions are asked here, as two tests that say what they are:

    * `test_two_updates_racing...` is the real race, and it is possible because a worker whose measurement
      holds makes two SUCCESSFUL activations reachable without a container runtime. Either both commit, in
      which case the generations must be distinct and ascending, or one is refused as busy, in which case the
      refusal must be truthful and nothing of it may remain.
    * `test_a_change_that_finds_the_lock_held_is_refused_and_changes_nothing` is the refusal on its own, which
      is what the previous version actually measured, under a name that says so.
    """

    @staticmethod
    def a_worker_whose_measurement_holds(service):
        """The real worker, with `measure` answering a report in which every property is observed to hold.

        This is what makes a race of two SUCCESSFUL updates reachable here at all: the real measurement
        starts a proxy container, and there is no runtime on this workstation. The report is built by the
        product's own `ConformanceReport` out of real `CheckResult`s, exactly as `_store_measurement` does,
        so the readiness gate reads the shape it really reads.
        """
        from agentnode_sdk.conformance.report import CheckResult, ConformanceReport, Vantage
        from agentnode_sdk.gateway.readiness import PROPERTY_CHECKS
        from agentnode_sdk.gateway.server import GatewayService

        real = service.worker
        every_check = sorted({c for ids in PROPERTY_CHECKS.values() for c in ids})

        class ItMeasuresAndEverythingHolds:
            def measure(self, *_a, **_k):
                results = tuple(CheckResult.measured(c, c, "test", True, Vantage.INSIDE,
                                                     "stated by the test")
                                for c in every_check)
                return ConformanceReport(
                    backend_identity="StandInBackend", backend_version="test", runtime="docker",
                    image="", generated_at="1970-01-01T00:00:00+00:00", results=results).to_dict()

            def measure_egress(self, *_a, **_k):
                return None

            def __getattr__(self, name):
                return getattr(real, name)

        GatewayService.worker = property(lambda _self: ItMeasuresAndEverythingHolds())

    def test_two_updates_that_really_overlap_lose_no_generation_and_mix_no_policies(self, serving):
        """TD5 and RQ7, with the overlap FORCED and then asserted to have happened.

        THE VERSION BEFORE THIS ONE STARTED TWO THREADS AND HOPED. An independent review's second round saw
        through it exactly: "it starts two threads around an instantaneous stand-in measurement but has no
        barrier inside the transaction. A fully sequential execution therefore passes while merely resembling
        the required race." That was the eighth time in this arc that an instrument of mine reported a
        stronger answer than it could produce, and it was right.

        So the first update is held OPEN, inside the transaction, by a worker whose `measure` blocks until the
        second has had its attempt. There is nothing to be lucky about:

        * the first thread enters `_transact`, takes the activation lock, and stops inside `measure`;
        * the second thread then calls `activate` while the first is demonstrably still in there;
        * the first is released, and both answers are read.

        And the overlap is ASSERTED rather than assumed: the test fails if the second attempt did not happen
        while the first was inside the measurement. A sequential execution cannot satisfy that.

        What the product guarantees under real overlap is a refusal, not a queue -- and that is what RQ7 asks
        for in the end: a change that never started cannot lose a generation, reuse one, or mix itself into
        another.
        """
        from agentnode_sdk.conformance.report import CheckResult, ConformanceReport, Vantage
        from agentnode_sdk.gateway.activation import ActivationError
        from agentnode_sdk.gateway.readiness import PROPERTY_CHECKS
        from agentnode_sdk.gateway.server import GatewayService

        root, service = serving
        _store_measurement(service)
        start = generation(service)
        before_file = (root / "config.json").read_text(encoding="utf-8")

        inside = threading.Event()          # the first update is in the measurement
        may_finish = threading.Event()      # the second has had its attempt
        # WHAT THE FIRST MEASUREMENT HAS DONE, which is the fact the overlap claim rests on. An independent
        # review's third round found the previous version of that claim incapable of being false: it recorded
        # `inside.is_set()` immediately after waiting successfully on `inside`, which is a tautology in the
        # language rather than an observation about the other thread. This event is set when `measure`
        # RETURNS, so the second thread can read a real answer: in a sequential execution the first
        # measurement would already have returned and the recorded value would be False. Finding F19.
        the_measurement_returned = threading.Event()
        the_second_tried_while_the_first_was_inside = []
        real = service.worker
        every_check = sorted({c for ids in PROPERTY_CHECKS.values() for c in ids})

        class ItBlocksInsideTheTransaction:
            def measure(self, *_a, **_k):
                inside.set()
                # Bounded, so a mistake in this test cannot hang the suite: if the second thread never
                # arrives the wait simply ends and the assertions below report that it did not overlap.
                may_finish.wait(timeout=60)
                results = tuple(CheckResult.measured(c, c, "test", True, Vantage.INSIDE,
                                                     "stated by the test")
                                for c in every_check)
                report = ConformanceReport(
                    backend_identity="StandInBackend", backend_version="test", runtime="docker",
                    image="", generated_at="1970-01-01T00:00:00+00:00", results=results).to_dict()
                # Last, so that a reader of the other thread's flag knows what it means: the measurement is
                # over only once this is set.
                the_measurement_returned.set()
                return report

            def measure_egress(self, *_a, **_k):
                return None

            def __getattr__(self, name):
                return getattr(real, name)

        GatewayService.worker = property(lambda _self: ItBlocksInsideTheTransaction())

        first = {}
        second = {}

        def the_first_update():
            try:
                verdict = service.activate(opol.build(opol.RESTRICTED, ("a.example", "b.example")))
                first["ready"] = bool(verdict.ready)
            except BaseException as why:                              # noqa: BLE001
                first["raised"] = why

        def the_second_update():
            # A precondition, not the overlap claim: if the first never arrived there is nothing to race.
            assert inside.wait(timeout=60), "the first update never reached the measurement"
            # THE OVERLAP CLAIM, recorded as something that can be false: has the first measurement
            # returned yet? In a sequential execution it has, and this is False.
            the_second_tried_while_the_first_was_inside.append(not the_measurement_returned.is_set())
            try:
                service.activate(opol.build(opol.RESTRICTED, ("c.example", "d.example")))
                second["committed"] = True
            except ActivationError as busy:
                second["refused"] = str(busy)
            except BaseException as why:                              # noqa: BLE001
                second["raised"] = why
            finally:
                may_finish.set()

        threads = [threading.Thread(target=the_first_update), threading.Thread(target=the_second_update)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=180)
        may_finish.set()

        # THE OVERLAP ITSELF, asserted before anything is concluded from it.
        assert the_second_tried_while_the_first_was_inside == [True], (
            "the first update's measurement had already returned when the second began, so the two did not "
            "overlap and whatever happened below is not about concurrency")
        assert "raised" not in first, "the first update failed in a way nothing here describes: %r" % (first,)
        assert "raised" not in second, (
            "the second update failed in a way nothing here describes: %r" % (second,))

        # WHAT THE PRODUCT GUARANTEES, ASSERTED BEFORE ANYTHING ELSE. The order is deliberate: this is the
        # only claim here that is specific to concurrency, and it must be what fails when the protection is
        # removed. With the activation lock taken out, counter-check 9 showed the FIRST update failing to
        # commit as well -- a true symptom of the same mutation, but one whose message says nothing about
        # overlapping updates. The sentence a failure prints has to name the thing that broke.
        assert "refused" in second, (
            "two updates overlapped and both were allowed to proceed: %r" % (second,))
        assert "already running" in second["refused"] and "Nothing was changed" in second["refused"], (
            "the refusal does not say what it did: %r" % (second["refused"],))
        assert first.get("ready") is True, "the first update did not commit, so there was nothing to race"

        # NO GENERATION LOST, REUSED OR MOVED BACKWARDS, and no two policies mixed.
        after = generation(service)
        assert after == start + 1, (
            "one update committed and the generation moved by %d" % (after - start))
        store = ActivationStore(service.state.root)
        assert store.protected.accepted_generation() >= after
        assert store.next_generation() > after
        assert tuple(sorted(in_force(service))) == ("a.example", "b.example"), (
            "what is in force is not the update that committed, whole: %r" % (in_force(service),))
        assert not store.pending_path.is_file()

        # AND THE REFUSED ONE LEFT THE OPERATOR'S INTENT ALONE. The previous version of this ended in
        # `or True`, which made it an assertion that could not fail, and checked only the first of the
        # refused policy's two hosts. An independent review's third round found both. What is asserted now is
        # the whole allowlist in the file, compared against the policy that committed, plus each refused host
        # by name -- so a file holding one of them, or holding both plus the right ones, fails. Finding F19.
        saved = json.loads((root / "config.json").read_text(encoding="utf-8"))
        assert tuple(sorted(saved.get("egress_allowed") or ())) == ("a.example", "b.example"), (
            "the config file is not the policy that committed, whole: %r" % (saved.get("egress_allowed"),))
        text = (root / "config.json").read_text(encoding="utf-8")
        for refused_host in ("c.example", "d.example"):
            assert refused_host not in text, (
                "the refused update's host %s reached the config file" % refused_host)


    def test_a_change_that_finds_the_lock_held_is_refused_and_changes_nothing(self, serving):
        """The other half, under a name that says what it does rather than implying a race.

        The lock is held explicitly, so there is no timing to be lucky about: a second change begins while
        one is in progress, and what it is told has to be true.
        """
        from agentnode_sdk.gateway.activation import ActivationError, ActivationLock

        root, service = serving
        _store_measurement(service)
        self.a_worker_whose_measurement_holds(service)
        start = generation(service)
        before = (root / "config.json").read_text(encoding="utf-8")

        answered: list = []
        with ActivationLock(service.state.root):
            def try_to_change():
                try:
                    service.activate(opol.build(opol.RESTRICTED, TWO))
                    answered.append(None)
                except ActivationError as busy:
                    answered.append(str(busy))
                except BaseException as why:                           # noqa: BLE001
                    answered.append(why)

            second = threading.Thread(target=try_to_change)
            second.start()
            second.join(timeout=180)

        assert answered, "the second change never finished"
        said = answered[0]
        assert isinstance(said, str), (
            "a change that began while another was in progress was not refused as busy: %r" % (said,))
        assert "already running" in said and "Nothing was changed" in said, (
            "the refusal does not say what it did: %r" % (said,))

        assert generation(service) == start, "a refused change advanced the generation"
        assert in_force(service) == ONE, "a refused change reached the snapshot"
        assert (root / "config.json").read_text(encoding="utf-8") == before, (
            "a refused change left the operator's intent altered, which is the state its own sentence "
            "says does not exist")
        assert not ActivationStore(service.state.root).pending_path.is_file()

    def test_the_generation_never_goes_backwards_once_accepted(self, serving):
        """The anchor is what makes a replaced older snapshot a rollback rather than a current state.

        Stated as the two things that must hold together, because either alone is satisfiable by a store
        that has lost track: the anchor is never behind what is in force, and the next generation is
        strictly ahead of both.
        """
        root, service = serving
        _store_measurement(service)
        store = ActivationStore(service.state.root)

        now = generation(service)
        anchor = store.protected.accepted_generation()

        assert anchor >= now, (
            "the rollback anchor is behind the snapshot in force (%d < %d), so putting an older snapshot "
            "back would read as current" % (anchor, now))
        assert store.next_generation() > max(anchor, now), (
            "the next generation does not advance past both the anchor and what is in force, so one "
            "could be reused")
