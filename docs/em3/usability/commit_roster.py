#!/usr/bin/env python3
"""Prove the participant list existed before the first session, without the list leaving your machine.

    python commit_roster.py my-participants.txt

Prints a fingerprint. Put the fingerprint in results.csv and keep the file itself local.

The frozen protocol asks for a named list written before the first session, so the ten cannot be
chosen after the outcomes are known. It also forbids names in the repository, in logs and in review
evidence. A hash satisfies both: it is worthless for identifying anyone, and it cannot be produced
after the fact from a different list.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    path = Path(argv[1])
    if not path.is_file():
        print(f"no such file: {path}")
        return 2
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(lines) < 10:
        print(f"the list has {len(lines)} entries; the cohort is exactly ten, plus your reserves")
        return 1
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    print()
    print(f"  entries      : {len(lines)}  (ten participants, the rest are your ordered reserves)")
    print(f"  fingerprint  : {digest}")
    print()
    print("  Put the fingerprint in the roster_fingerprint column of results.csv.")
    print("  Keep this file on your machine. It must not be committed, pasted or uploaded.")
    print("  Replacing someone is allowed only BEFORE their session starts, from the reserve list,")
    print("  for a recorded reason — then re-run this and note both fingerprints.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
