"""The timing measurement, built to the specification frozen in docs/review/TIMING-PROTOCOL-V2.md.

Nothing in this file chooses a number. Every constant is read from the frozen document's values,
restated here as literals so that a reader can diff the two and see they agree. If they ever
disagree, the document is right and this file is wrong.

Three modes:

    python -m tests.timing_v2 process --pairs-seed N [--sensitive]   one fresh process, JSON out
    python -m tests.timing_v2 run --out run.json [--sensitive]       spawns the processes, decides
    python -m tests.timing_v2 combine run1.json run2.json ...        Fisher over the runs

`run` is what a lane invokes. `combine` is what turns nine runs into one verdict.

## The statistic, and why it is paired

Each of the 64 pairs is one foreign identifier and one absent identifier, generated identically
and assigned by coin. The quantity for a pair is the difference of its two medians. The process's
statistic is the median over its pairs; the run's statistic is the median over its processes.

The null is built by SIGN-FLIPPING within pairs -- the paired permutation -- and by doing so
within each process, so process-to-process variation stays in the null where it belongs. A null
built by shuffling across processes would be wider than the statistic it judges and would pass
anything.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import random
import statistics
import subprocess
import sys
import time
import uuid

# ---------------------------------------------------------------- frozen, from the document
PAIRS = 64
PROBES = 256
WARMUP = 2048
PROCESSES = 5
PERMUTATIONS = 10_000
BOOTSTRAPS = 10_000
#: The gate: a combined p at or above this, and a 99% interval containing zero.
P_FLOOR = 0.01
INTERVAL = 0.99
#: The sensitivity control has to be seen far more clearly than the gate has to be clear.
SENSITIVE_P_CEILING = 0.001

#: How many probes are inside ONE timing sample. Not a number the frozen document fixes -- it
#: fixes the protocol and the statistic, not how a clock is read -- and it exists because the
#: first calibration VOIDED ITSELF: `time.perf_counter_ns()` ticks in ~100ns steps on Windows,
#: the inserted control effect is one dictionary probe, and a quantity below the resolution of
#: the instrument measuring it is not measurable. Per-pair medians came out on tick boundaries
#: -- 100, 250, 275 -- and the control did not fire (p 0.442).
#:
#: Timing a batch and dividing is the ordinary answer to a coarse clock. Both groups are batched
#: identically, so the comparison is unchanged; what changes is that a 100ns tick now buys about
#: 3ns of resolution per probe. Recorded here rather than adjusted quietly, because "the
#: calibration voided the run and the instrument was fixed" is exactly what §5 of the frozen
#: document is for.
#:
#: 32 was not enough either. It brought the control from p 0.442 to p 0.0022 with the right
#: sign, and the acceptance rule wants p < 0.001 AND an interval clear of zero -- so that run
#: voided as well. The signal is about 3% of a probe that spends most of its time constructing
#: and raising a refusal, and resolving 3% needs the per-sample noise to be smaller than the
#: effect in EVERY process, not on average across them: the run statistic is a median over five,
#: and one indifferent process drags a five-value median around.
#:
#: 128 is four times the samples inside one reading, so about half the per-sample noise. It is
#: chosen to make the control visible, which is what a sensitivity control is for -- it is NOT
#: chosen against the gate's data, which does not exist yet.
BATCH = 128


def _median(values):
    return statistics.median(values)


# ---------------------------------------------------------------- one process


def one_process(seed: int, *, sensitive: bool) -> dict:
    """Collect this process's paired observations and return its per-pair differences."""
    import tempfile

    from agentnode_sdk.access import dispatch
    from agentnode_sdk.gateway.identity import GatewayState
    from agentnode_sdk.gateway.server import GatewayService
    from tests.test_em3c_gateway import StandInBackend, _store_measurement
    from tests.test_two_accounts import _a_customer, _a_run_by

    rng = random.Random(seed)
    state = GatewayState(tempfile.mkdtemp(), version="timing")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    alice, bob = _a_customer(service, "alice"), _a_customer(service, "bob")

    # 2N candidates, generated identically; N made real BY COIN. Anything intrinsic to an
    # identifier is balanced in expectation rather than confounded with the label.
    candidates = [uuid.uuid4().hex for _ in range(PAIRS * 2)]
    rng.shuffle(candidates)
    foreign_ids = [_a_run_by(service, alice) for _ in candidates[:PAIRS]]
    absent_ids = candidates[PAIRS:]

    extra = {}
    if sensitive:
        # THE SENSITIVITY CONTROL, defined as work: one additional probe of a populated mapping
        # on the foreign side only. It is the smallest unit the mechanism under test could
        # plausibly leak. A nanosecond figure here would be a statement about a machine.
        extra = {run_id: True for run_id in foreign_ids}

    def probe(run_id, is_foreign):
        """One SAMPLE: BATCH lookups, timed together, reported per lookup.

        The clock is coarser than the thing being measured, so a single lookup lands on a tick
        boundary and the signal disappears into the quantisation. Both groups are batched the
        same way, so nothing about the comparison changes.
        """
        started = time.perf_counter_ns()
        for _ in range(BATCH):
            try:
                dispatch._a_run_of_this_caller(service, bob, run_id)
            except dispatch.Refused:
                pass
            if sensitive and is_foreign:
                extra.get(run_id)
        return (time.perf_counter_ns() - started) / BATCH

    for i in range(WARMUP):                                   # discarded
        probe(absent_ids[i % PAIRS], False)

    held = {"foreign": [[] for _ in range(PAIRS)], "absent": [[] for _ in range(PAIRS)]}
    was = gc.isenabled()
    gc.disable()
    try:
        for _ in range(PROBES):
            order = list(range(PAIRS))
            rng.shuffle(order)                                # randomly interleaved
            for at in order:
                if rng.random() < 0.5:                        # and randomly ordered within a pair
                    held["foreign"][at].append(probe(foreign_ids[at], True))
                    held["absent"][at].append(probe(absent_ids[at], False))
                else:
                    held["absent"][at].append(probe(absent_ids[at], False))
                    held["foreign"][at].append(probe(foreign_ids[at], True))
    finally:
        if was:
            gc.enable()
        state.close()

    return {"seed": seed, "sensitive": sensitive,
            "differences": [_median(held["foreign"][at]) - _median(held["absent"][at])
                            for at in range(PAIRS)]}


# ---------------------------------------------------------------- one run, over processes


def _statistic(per_process) -> float:
    """Median over processes of the median over that process's pairs."""
    return _median([_median(one) for one in per_process])


def decide(per_process, seed: int) -> dict:
    rng = random.Random(seed)
    observed = _statistic(per_process)

    # THE NULL: sign-flip within pairs, within each process. Under "no difference" the sign of a
    # pair's difference is a coin, so flipping it is a relabelling that the data cannot tell from
    # the truth. Ten thousand of them give a p-value with resolution 1e-4.
    beyond = 0
    for _ in range(PERMUTATIONS):
        flipped = [[d if rng.random() < 0.5 else -d for d in one] for one in per_process]
        if abs(_statistic(flipped)) >= abs(observed):
            beyond += 1
    p = (beyond + 1) / (PERMUTATIONS + 1)                     # never zero: an estimate, not a proof

    # And a 99% interval on the effect, by resampling PAIRS with replacement inside each process.
    spread = []
    for _ in range(BOOTSTRAPS):
        again = [[one[rng.randrange(len(one))] for _ in one] for one in per_process]
        spread.append(_statistic(again))
    spread.sort()
    low = spread[int((1 - INTERVAL) / 2 * BOOTSTRAPS)]
    high = spread[min(BOOTSTRAPS - 1, int((1 + INTERVAL) / 2 * BOOTSTRAPS))]

    return {"effect_ns": observed, "p": p, "low_ns": low, "high_ns": high,
            "interval_contains_zero": low <= 0 <= high,
            "processes": len(per_process), "pairs": len(per_process[0])}


def a_run(*, sensitive: bool, seed: int) -> dict:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    per_process = []
    for which in range(PROCESSES):
        done = subprocess.run(
            [sys.executable, "-m", "tests.timing_v2", "process",
             "--pairs-seed", str(seed + which)] + (["--sensitive"] if sensitive else []),
            capture_output=True, text=True, cwd=here, timeout=3600)
        if done.returncode != 0:
            raise SystemExit("a measuring process failed:\n%s" % done.stderr[-2000:])
        per_process.append(json.loads(done.stdout.strip().splitlines()[-1])["differences"])
    said = decide(per_process, seed)
    said.update({"sensitive": sensitive, "python": ".".join(map(str, sys.version_info[:3])),
                 "platform": sys.platform, "seed": seed})
    return said


# ---------------------------------------------------------------- the verdict over runs


def combine(runs) -> dict:
    """Fisher's method over the runs' p-values, plus the interval over their pooled effect.

    One decision over all of them rather than one per run: nine independent tests at 1% would
    fail about 9% of the time on a gateway with no channel at all. Fisher is also stricter
    against what matters -- a small consistent effect that never crosses in any single run still
    combines to a small p.
    """
    import math

    ps = [one["p"] for one in runs]
    chi = -2.0 * sum(math.log(p) for p in ps)
    freedom = 2 * len(ps)
    combined = _chi_square_tail(chi, freedom)

    effects = [one["effect_ns"] for one in runs]
    lows = [one["low_ns"] for one in runs]
    highs = [one["high_ns"] for one in runs]
    pooled_contains_zero = min(lows) <= 0 <= max(highs)

    sensitive = [one for one in runs if one.get("sensitive")]
    gate = [one for one in runs if not one.get("sensitive")]

    verdict = {
        "runs": len(gate), "sensitivity_runs": len(sensitive),
        "combined_p": combined, "p_floor": P_FLOOR,
        "median_effect_ns": _median(effects) if effects else 0.0,
        "pooled_low_ns": min(lows) if lows else 0.0,
        "pooled_high_ns": max(highs) if highs else 0.0,
        "interval_contains_zero": pooled_contains_zero,
    }
    verdict["passes"] = bool(combined >= P_FLOOR and pooled_contains_zero)
    return verdict


def _chi_square_tail(x: float, k: int) -> float:
    """P(X > x) for a chi-square with k degrees of freedom, k even. Exact, no dependency.

    Fisher's statistic always has an even number of degrees of freedom -- two per p-value -- so
    the closed form for even k is all that is needed and `scipy` is not.
    """
    import math

    if x <= 0:
        return 1.0
    half, total, term = k // 2, 0.0, 1.0
    for i in range(half):
        if i:
            term *= (x / 2) / i
        total += term
    return min(1.0, math.exp(-x / 2) * total)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="timing_v2")
    ap.add_argument("mode", choices=("process", "run", "combine"))
    ap.add_argument("files", nargs="*")
    ap.add_argument("--pairs-seed", type=int, default=1)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--sensitive", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.mode == "process":
        print(json.dumps(one_process(args.pairs_seed, sensitive=args.sensitive)))
        return 0

    if args.mode == "run":
        said = a_run(sensitive=args.sensitive, seed=args.seed)
        text = json.dumps(said, indent=2, sort_keys=True)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
        print(text)
        return 0

    runs = []
    for name in args.files:
        with open(name, encoding="utf-8") as fh:
            runs.append(json.load(fh))
    said = combine(runs)
    print(json.dumps(said, indent=2, sort_keys=True))
    return 0 if said["passes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
