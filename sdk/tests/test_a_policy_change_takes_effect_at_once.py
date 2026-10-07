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
    root = tmp_path / "state"
    root.mkdir(parents=True, exist_ok=True)
    write_the_config(root, ONE)
    state, service = gateway_commands._service(root)
    try:
        yield root, service
    finally:
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

    WHAT A MEASUREMENT FAILURE LOOKS LIKE DEPENDS ON THE MACHINE, and the first version of these two tests
    assumed one shape. Asking for a restricted policy drives the real egress path, which starts a proxy
    container; where there is no container runtime that RAISES out of `_transact` rather than returning a
    not-ready verdict. On a machine with podman it measures and returns one. Both are failures of the
    measurement and RQ6 is the same requirement in either -- so these assert the INVARIANT and accept
    either outcome, instead of requiring the shape this workstation happens to produce.
    """

    @staticmethod
    def a_measurement_that_cannot_succeed(service, hosts):
        try:
            return service.activate(opol.build(opol.RESTRICTED, hosts)), None
        except BaseException as why:                                  # noqa: BLE001
            return None, why

    def test_the_config_and_the_snapshot_are_both_left_alone(self, serving):
        root, service = serving
        _store_measurement(service)
        before_file = (root / "config.json").read_text(encoding="utf-8")
        before_force, before_gen = in_force(service), generation(service)

        verdict, raised = self.a_measurement_that_cannot_succeed(service, TWO)

        assert verdict is not None or raised is not None
        if verdict is not None:
            assert verdict.ready is False, "a measurement that did not succeed was reported as ready"
        assert in_force(service) == before_force, "a failed measurement moved the policy in force"
        assert generation(service) == before_gen, "a failed measurement advanced the generation"
        assert (root / "config.json").read_text(encoding="utf-8") == before_file, (
            "a failed measurement left the operator's intent changed, so the next restart would build "
            "from a policy that was never activated")

    def test_and_nothing_is_left_pending(self, serving):
        root, service = serving
        _store_measurement(service)

        self.a_measurement_that_cannot_succeed(service, TWO)

        assert not ActivationStore(service.state.root).pending_path.is_file(), (
            "a pending record left behind makes every later report say a change is proposed")


# ------------------------------------------------------------------------------------------- RQ7, TD5


class TestConcurrentUpdatesLoseNoGeneration:
    """RQ7: no generation lost, reused, moved backwards, and no two policies mixed."""

    def test_two_transactions_at_once_each_get_their_own_generation(self, serving):
        """Two REAL transactions, racing, through the path that takes the lock.

        THE FIRST VERSION OF THIS TEST DROVE `ActivationStore.activate` DIRECTLY FROM TWO THREADS and
        failed: both claimed generation 2. That is not a defect, it is my test driving a path the product
        never drives unlocked -- `GatewayService._transact` holds `ActivationLock` around the whole change,
        and the store's own method is the inside of that lock. A test that removes the product's protection
        and then reports the absence of protection has measured nothing.

        So this races the transaction. A CLOSED policy is used because it is the one change that completes
        without a worker (PA2), which is what makes a real two-thread race possible in a unit suite at all.

        AND WHAT THE PRODUCT GUARANTEES IS A REFUSAL, NOT A QUEUE, which this test learned from the product
        rather than assuming: a change that finds the lock held is told *"another change to this gateway's
        policy is already running. Nothing was changed. Wait for it to finish, then try again."* That
        satisfies RQ7 more simply than serialising would -- a change that never started cannot lose a
        generation, reuse one, or mix itself into another -- and the sentence it is refused with has to be
        true, which is asserted here rather than taken.

        THE THIRD VERSION OF THIS TEST STOPPED RACING TWO THREADS, and the reason is worth keeping. Its
        second version raced two real transactions over a closed policy, on the assumption that a closed
        policy always completes without a worker. It does not: that branch is for a worker that RAISES, and
        a `ContainerBackend` with no runtime does not raise -- it reports itself unavailable, the
        measurement returns a report that is not conformant, and the close is refused by the gate. So one
        thread reported no activation and the test failed for a reason that had nothing to do with
        concurrency. Holding the lock explicitly asks the same question with no race in it: whether a second
        change can begin while one is in progress, and what it is told.
        """
        from agentnode_sdk.gateway.activation import ActivationError, ActivationLock

        root, service = serving
        _store_measurement(service)
        start = generation(service)
        before = (root / "config.json").read_text(encoding="utf-8")

        refused: list = []
        with ActivationLock(service.state.root):
            # One change is in progress, by definition: this is what `_transact` holds while it works.
            def try_to_change():
                try:
                    service.activate(opol.build(opol.RESTRICTED, TWO))
                    refused.append(None)
                except ActivationError as busy:
                    refused.append(str(busy))
                except BaseException as why:                           # noqa: BLE001
                    refused.append(why)

            second = threading.Thread(target=try_to_change)
            second.start()
            second.join(timeout=120)

        assert refused, "the second change never finished"
        said = refused[0]
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
