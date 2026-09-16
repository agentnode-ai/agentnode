"""Why the first timing observation came back red. An investigation, NOT a measurement.

Deliberately not named `test_*`: nothing here is authoritative and nothing here may be used to
choose a threshold. The first observation (0.569 against a null of 0.512, sdk 3.12, ubuntu) is
preserved unchanged in `timing-first-observation.txt`, and the rule is that no bound may be
picked after seeing the value it has to clear.

What this establishes instead is WHY that number appeared. Two candidate explanations, and they
are distinguished by experiment rather than argued:

  (1) a residual channel in the product -- foreign and absent really do cost different work;
  (2) a bias in the harness -- the harness can "detect" a difference where there provably is
      none.

(2) is testable directly and cheaply, and it is the one this file tests first: run the SAME
statistic over three groups that are ALL absent. There is no difference to find. Anything the
harness reports is the harness.

Run it:  python -m tests.timing_investigation          (from sdk/)
"""
from __future__ import annotations

import gc
import random
import statistics
import time
import uuid

from agentnode_sdk.access import dispatch
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService

SAMPLES = 2000
IDENTIFIERS = 16
ROUNDS = 8


def _accuracy(one, other) -> float:
    """The statistic the first observation used, unchanged, so the comparison is like for like."""
    marked = sorted([(v, 0) for v in one] + [(v, 1) for v in other])
    total = len(one) + len(other)
    best, below_one, below_other = 0.0, 0, 0
    for _value, which in marked:
        below_one += which == 0
        below_other += which == 1
        best = max(best,
                   (below_one + (len(other) - below_other)) / total,
                   (below_other + (len(one) - below_one)) / total)
    return best


def _a_gateway(where):
    from tests.test_em3c_gateway import StandInBackend, _store_measurement
    from tests.test_two_accounts import _a_customer

    state = GatewayState(str(where), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    return service, _a_customer(service, "alice"), _a_customer(service, "bob")


def _probe(service, who, run_id):
    started = time.perf_counter_ns()
    try:
        dispatch._a_run_of_this_caller(service, who, run_id)
    except dispatch.Refused:
        pass
    return time.perf_counter_ns() - started


def _collect(service, who, pools, *, shuffled: bool, rng) -> dict:
    """`pools` is {name: [ids]}. Returns {name: [latencies]}."""
    out = {name: [] for name in pools}
    order = list(pools)
    was = gc.isenabled()
    gc.disable()
    try:
        for i in range(SAMPLES):
            if shuffled:
                rng.shuffle(order)
            for name in order:
                pool = pools[name]
                out[name].append(_probe(service, who, pool[i % len(pool)]))
    finally:
        if was:
            gc.enable()
    return out


def _fresh_ids(how_many):
    return [uuid.uuid4().hex for _ in range(how_many)]


def experiment_one(service, bob, rng):
    """THREE GROUPS THAT ARE ALL ABSENT. There is nothing to find.

    Same shape as the first observation: fixed order within each iteration, the first group
    always probed first. If the harness reports a separation here, the separation is the harness.
    """
    print("\n=== 1. a PURE NULL, in the first observation's own fixed order")
    print("    three groups of ids that exist nowhere, labelled as if they were different")
    fixed, shuffled = [], []
    for _ in range(ROUNDS):
        pools = {"first": _fresh_ids(IDENTIFIERS), "second": _fresh_ids(IDENTIFIERS),
                 "third": _fresh_ids(IDENTIFIERS)}
        got = _collect(service, bob, pools, shuffled=False, rng=rng)
        fixed.append(_accuracy(got["first"], got["second"]))
        got = _collect(service, bob, pools, shuffled=True, rng=rng)
        shuffled.append(_accuracy(got["first"], got["second"]))
    _say("fixed order,   first vs second (both absent)", fixed)
    _say("random order,  first vs second (both absent)", shuffled)
    return fixed, shuffled


def experiment_two(service, alice, bob, rng, heading="2. foreign versus absent, with the assignment RANDOMISED"):
    """The real comparison, both orders, with ids assigned to groups AT RANDOM.

    2N candidate ids are generated identically; N are submitted as runs in Alice's account and N
    are not. Which is which is decided by the coin, so anything intrinsic to an identifier --
    its hash, its dictionary bucket -- is balanced in expectation rather than confounded with
    the thing being measured.
    """
    from tests.test_two_accounts import _a_run_by

    print("\n=== 2. foreign versus absent, with the assignment RANDOMISED")
    fixed, shuffled = [], []
    for _ in range(ROUNDS):
        candidates = _fresh_ids(IDENTIFIERS * 2)
        rng.shuffle(candidates)
        will_exist, will_not = candidates[:IDENTIFIERS], candidates[IDENTIFIERS:]
        foreign = [_a_run_by(service, alice) for _ in will_exist]
        pools = {"foreign": foreign, "absent": will_not, "null": _fresh_ids(IDENTIFIERS)}
        got = _collect(service, bob, pools, shuffled=False, rng=rng)
        fixed.append((_accuracy(got["foreign"], got["absent"]),
                      _accuracy(got["absent"], got["null"])))
        got = _collect(service, bob, pools, shuffled=True, rng=rng)
        shuffled.append((_accuracy(got["foreign"], got["absent"]),
                         _accuracy(got["absent"], got["null"])))
    _say("fixed order,   foreign vs absent", [a for a, _ in fixed])
    _say("fixed order,   absent  vs null  ", [n for _, n in fixed])
    _say("random order,  foreign vs absent", [a for a, _ in shuffled])
    _say("random order,  absent  vs null  ", [n for _, n in shuffled])
    return fixed, shuffled


def experiment_three(service, alice, bob, rng):
    """And the same, for a caller who OWNS something -- so the lookup is in a populated map.

    Bob owning nothing means `owned_by` hands back the shared empty mapping every time. A
    customer with runs of their own probes a real dictionary, and a miss in a populated table is
    where bucket effects would live if they lived anywhere.
    """
    from tests.test_two_accounts import _a_run_by

    print("\n=== 3. the same, for a caller who owns runs of their own")
    for _ in range(IDENTIFIERS):
        _a_run_by(service, bob)
    return experiment_two(service, alice, bob, rng)


def _say(label, values):
    print("    %-38s median %.4f   min %.4f   max %.4f   over %d rounds"
          % (label, statistics.median(values), min(values), max(values), len(values)))


def main() -> int:
    import tempfile

    rng = random.Random(20260916)          # fixed, so this is repeatable by anybody
    where = tempfile.mkdtemp()
    service, alice, bob = _a_gateway(where)
    print("Python", __import__("sys").version.split()[0], "| samples", SAMPLES,
          "| identifiers", IDENTIFIERS, "| rounds", ROUNDS)
    print("The statistic is the first observation's, unchanged. Nothing here sets a threshold.")
    try:
        experiment_one(service, bob, rng)
        experiment_two(service, alice, bob, rng)
        experiment_three(service, alice, bob, rng)
    finally:
        service.state.close()
    print("\nRead this as: if the PURE NULL separates under fixed order and stops separating "
          "under\nrandom order, the first observation is the harness and not the product.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
