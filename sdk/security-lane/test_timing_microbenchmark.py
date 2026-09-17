"""ARCHIVIERT, HISTORISCH UNGÜLTIG -- und ausdrücklich nicht gelöscht.

Dieser Test war das Per-Commit-Tor für Teil (c) der Existenz-Isolation. Er ist es nicht mehr, und
das hat zwei voneinander unabhängige Gründe, die beide belegt sind:

1. DIE ERSTE FASSUNG MASS MIT EINEM VERZERRTEN INSTRUMENT. Die Statistik -- die beste
   Klassifikationsgenauigkeit über alle Schwellen -- ist ein Maximum über rund 4000 Kandidaten
   und damit nach oben verzerrt. Auf einem reinen Nullbefund, wo es nichts zu finden GIBT,
   erreichte sie 0.608 und 0.598 unter Linux. Belegt in `timing-cause.txt`.

2. DIE ERSETZUNG WAR METHODISCH SAUBER UND TROTZDEM NICHT DER RICHTIGE NACHWEIS. Der
   Permutationstest ist unverzerrt, aber der geforderte Kontrolleffekt -- eine zusätzliche
   Dictionary-Abfrage, rund 40 ns -- ist für einen entfernten Angreifer über TLS bedeutungslos.
   `THREAT-MODEL-CRITERION-DECISION-0001` hat das Kriterium deshalb an das Bedrohungsmodell
   gebunden: `docs/review/EXISTENCE-ISOLATION-CRITERION-V2.md`.

Was hier steht, ist trotzdem nützlich und wird deshalb aufgehoben: als DIAGNOSE für die
Gleichheit des Lookup-Mechanismus, und als die richtige Frage für einen Angreifer auf DERSELBEN
Maschine -- für den die Nanosekunden wieder zählen. Teil (7) des Kriteriums behält ihn genau
dafür.

Er wird nicht gesammelt: `pyproject.toml` setzt `testpaths = ["tests"]`, und dieses Verzeichnis
liegt daneben. Ausführen von Hand:

    python -m pytest security-lane/ -p no:randomly

Was er NICHT etabliert, und nie etabliert hat: konstante Laufzeit in CPython.
"""
from __future__ import annotations

import gc
import statistics
import time
import uuid

import pytest

from agentnode_sdk.access import dispatch
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement
from tests.test_two_accounts import _a_customer, _a_run_by


@pytest.fixture()
def two_accounts(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    alice, bob = _a_customer(service, "alice"), _a_customer(service, "bob")
    try:
        yield service, alice, bob
    finally:
        state.close()


def _absent_id() -> str:
    return uuid.uuid4().hex


# ------------- die Zahlen, wie sie eingefroren waren. Unverändert, als historischer Satz.

#: Samples per group. Fixed here, before the measurement was written, per the binding next action
#: of EXISTENCE-ISOLATION-DECISION-0001.
SAMPLES = 2000

#: How many DIFFERENT identifiers each group draws from. One foreign id and one absent id would
#: characterise those two ids rather than the two situations -- finding F2 names exactly that.
IDENTIFIERS = 16

#: How much better than the null an attacker's best single-threshold guess may do. Fixed before
#: the measurement. 0.5 is chance.
MARGIN = 0.05



def _best_threshold_accuracy(one, other) -> float:
    """The best an attacker with a stopwatch can do: one threshold, both orientations.

    0.5 is chance. Computed the same way for the real comparison and for the null, by this same
    function, so the two numbers are comparable by construction.
    """
    marked = sorted([(v, 0) for v in one] + [(v, 1) for v in other])
    total = len(one) + len(other)
    best = 0.0
    below_one = 0
    below_other = 0
    for value, which in marked:
        below_one += which == 0
        below_other += which == 1
        # "at or below the threshold is `one`" and the mirror of it.
        forward = below_one + (len(other) - below_other)
        backward = below_other + (len(one) - below_one)
        best = max(best, forward / total, backward / total)
    return best




class TestNoTimingDifferenceSurvivesAboveTheNoiseOfTheMeasurement:
    """Pre-registered: 2000 interleaved samples per group, 16 identifiers each, margin 0.05.

    Measured at the FUNCTION rather than at the door, and that is the stronger choice: a
    dispatch appends to the audit file, and a millisecond of disk noise would bury a channel
    rather than disprove one. `_a_run_of_this_caller` is where the branch was. A null here is a
    null at the door, because the door's extra work is identical for both cases and only adds
    noise on top.
    """

    def _samples(self, service, who, foreign, absent, other_absent):
        """Interleaved round-robin, so drift and scheduling hit all three groups equally."""
        groups = {"foreign": [], "absent": [], "null": []}
        pick = [("foreign", foreign), ("absent", absent), ("null", other_absent)]
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            for i in range(SAMPLES):
                for name, pool in pick:
                    run_id = pool[i % len(pool)]
                    started = time.perf_counter_ns()
                    try:
                        dispatch._a_run_of_this_caller(service, who, run_id)
                    except dispatch.Refused:
                        pass
                    groups[name].append(time.perf_counter_ns() - started)
        finally:
            if was_enabled:
                gc.enable()
        return groups

    def test_an_attacker_with_a_stopwatch_does_no_better_than_chance(self, two_accounts):
        service, alice, bob = two_accounts
        foreign = [_a_run_by(service, alice) for _ in range(IDENTIFIERS)]
        absent = [_absent_id() for _ in range(IDENTIFIERS)]
        other_absent = [_absent_id() for _ in range(IDENTIFIERS)]

        groups = self._samples(service, bob, foreign, absent, other_absent)

        # The null: two situations that ARE identical, measured the same way in the same run.
        # Whatever separation this shows is what this machine's noise is worth today.
        null = _best_threshold_accuracy(groups["absent"], groups["null"])
        real = _best_threshold_accuracy(groups["foreign"], groups["absent"])

        assert real <= null + MARGIN, (
            "telling a FOREIGN run from an ABSENT one by timing alone succeeds %.3f of the time, "
            "against a null of %.3f on the same machine in the same run. The margin fixed before "
            "this measurement was %.2f." % (real, null, MARGIN))

    def test_and_the_measurement_can_tell_when_there_IS_a_difference(self, two_accounts):
        """Sensitivity, in the same run, so a passing result above is not a broken instrument.

        A deliberate existence-dependent branch -- the shape that was removed -- must be caught
        by the same statistic with the same margin. Without this, a measurement that always
        returns chance would pass the test above for ever.
        """
        service, alice, bob = two_accounts
        foreign = [_a_run_by(service, alice) for _ in range(IDENTIFIERS)]
        absent = [_absent_id() for _ in range(IDENTIFIERS)]
        other_absent = [_absent_id() for _ in range(IDENTIFIERS)]

        def with_the_branch_back(run_id):
            record = service.runs.get(str(run_id))          # the global lookup, restored
            if record is not None:
                # What the old code did with what it found: read two fields and compare them.
                if (record.owner_client_id, record.owner_account_id) != ("", ""):
                    raise dispatch.Refused("no_such_run", "no", "no")
            raise dispatch.Refused("no_such_run", "no", "no")

        groups = {"foreign": [], "absent": [], "null": []}
        pick = [("foreign", foreign), ("absent", absent), ("null", other_absent)]
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            for i in range(SAMPLES):
                for name, pool in pick:
                    started = time.perf_counter_ns()
                    try:
                        with_the_branch_back(pool[i % len(pool)])
                    except dispatch.Refused:
                        pass
                    groups[name].append(time.perf_counter_ns() - started)
        finally:
            if was_enabled:
                gc.enable()

        null = _best_threshold_accuracy(groups["absent"], groups["null"])
        real = _best_threshold_accuracy(groups["foreign"], groups["absent"])
        assert real > null + MARGIN, (
            "the instrument did not notice a deliberate existence-dependent branch: %.3f against "
            "a null of %.3f. A measurement that cannot see the defect it was built from is not "
            "evidence that the defect is gone." % (real, null))

