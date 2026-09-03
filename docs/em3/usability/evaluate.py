#!/usr/bin/env python3
"""Apply the frozen thresholds to a completed results sheet. No judgement, no interpretation.

    python evaluate.py results.csv

The thresholds were fixed in EXEC_MODEL_PLAN_EM3.md §16 before anyone was recruited, and this script
is deliberately unable to relax them: they are constants, and every refusal below names the rule it
comes from. If the run fails, the finding is about the product.

Nothing here reads or accepts a name. The sheet carries U01–U10 and screening facts only.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

COHORT = 10                 # exactly ten, fixed prospectively — not "ten or more"
MUST_COMPLETE = 8           # 8 of exactly 10
MAX_SECONDS = 180           # three minutes, excluding downloads and model latency
MIN_ASSISTIVE = 2           # at least two participants using their own assistive technology
IDS = [f"U{i:02d}" for i in range(1, COHORT + 1)]

YES, NO = "yes", "no"


def fail(msg: str) -> None:
    print(f"  FAIL  {msg}")


def load(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if (r.get("participant") or "").strip()]
    return rows


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    path = Path(argv[1])
    if not path.is_file():
        print(f"  FAIL  no such file: {path}")
        return 2
    rows = load(path)
    problems = 0

    # ---- the sheet itself has to be a valid measurement before its numbers mean anything
    ids = [r["participant"].strip() for r in rows]
    if ids != IDS:
        fail(f"the sheet must contain exactly {IDS[0]}..{IDS[-1]}, once each, in order; found {ids}")
        problems += 1

    for r in rows:
        for col in ("completed_unaided", "seconds_to_result", "moderator_intervened",
                    "q1_where_did_it_run", "q2_unprotected_belief", "assistive"):
            if col not in r:
                fail(f"the sheet is missing the column {col!r}")
                return 1

    fingerprint = next((r.get("roster_fingerprint", "").strip() for r in rows
                        if r.get("roster_fingerprint", "").strip()), "")
    if not fingerprint:
        fail("no roster fingerprint: the participant list must be committed with commit_roster.py "
             "BEFORE the first session, so the ten cannot be chosen after the results are known")
        problems += 1

    # ---- the absolute rule, checked first because nothing else can rescue it
    believed_exposed = [r["participant"] for r in rows
                        if r["q2_unprotected_belief"].strip().lower() == YES]
    if believed_exposed:
        fail(f"{len(believed_exposed)} participant(s) believed a stranger's code ran unprotected on "
             f"their computer: {', '.join(believed_exposed)}. §16.4 makes a single 'yes' fail the "
             f"whole run — someone who finishes but feels exposed has not been given a product they "
             f"can trust.")
        problems += 1

    # ---- completion, with an intervened session counting as a failed measurement
    completed = []
    for r in rows:
        pid = r["participant"]
        unaided = r["completed_unaided"].strip().lower() == YES
        intervened = r["moderator_intervened"].strip().lower() == YES
        try:
            secs = float(r["seconds_to_result"] or 0)
        except ValueError:
            secs = -1.0
        if intervened:
            print(f"  note  {pid}: moderator intervened — counted as a failure of the measurement")
            continue
        if not unaided:
            continue
        if secs <= 0 or secs > MAX_SECONDS:
            print(f"  note  {pid}: finished in {secs or 'unrecorded'}s, over the {MAX_SECONDS}s bound")
            continue
        completed.append(pid)

    if len(completed) < MUST_COMPLETE:
        fail(f"{len(completed)} of {COHORT} completed the first safe run unaided within "
             f"{MAX_SECONDS}s; the threshold is {MUST_COMPLETE}")
        problems += 1

    # ---- the accessibility participants are part of the same ten, not a softer group
    assistive = [r["participant"] for r in rows
                 if r["assistive"].strip().lower() in ("screenreader", "keyboard", "magnification")]
    if len(assistive) < MIN_ASSISTIVE:
        fail(f"only {len(assistive)} participant(s) used assistive technology; at least "
             f"{MIN_ASSISTIVE} of the ten must")
        problems += 1

    # ---- reported, never a gate: believing yourself unsafe is the failure, not being wrong
    right = sum(1 for r in rows
                if r["q1_where_did_it_run"].strip().lower() in ("agentnode", "own_server", "local"))
    print()
    print(f"  completed unaided within {MAX_SECONDS}s : {len(completed)}/{COHORT} "
          f"(threshold {MUST_COMPLETE})")
    print(f"  believed themselves exposed             : {len(believed_exposed)} (threshold 0)")
    print(f"  using assistive technology              : {len(assistive)} (minimum {MIN_ASSISTIVE})")
    print(f"  named where it ran, reported only       : {right}/{COHORT}")
    print(f"  roster fingerprint                      : {fingerprint or '(missing)'}")
    print()
    if problems:
        print(f"  RESULT: FAIL — {problems} threshold(s) not met.")
        print("  The thresholds do not move. Fix the product and run the ten again.")
        return 1
    print("  RESULT: PASS — the usability half of EM-3A is evidenced.")
    print("  Hand over this sheet as the raw outcome; do not summarise it away.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
