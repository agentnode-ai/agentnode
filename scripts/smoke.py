#!/usr/bin/env python3
"""Run the smoke suite: the core user paths, end to end, in under ten minutes.

    python scripts/smoke.py

It runs two pytest sessions, because backend and SDK have separate environments:

    backend/tests/smoke   core paths 1 (register/login -> API key) and 6 (publish + verify)
    sdk/tests/smoke       core paths 2 (remote MCP), 3 (local MCP), 4 (cloud quota), 5 (own gateway)

Interpreters, in this order:
    SMOKE_BACKEND_PYTHON / SMOKE_SDK_PYTHON, then backend/.venv and sdk/.venv of this checkout,
    then the interpreter running this script.

The backend part needs the same local Postgres as the backend test suite (DATABASE_URL).
Nothing else: no network, no real credentials, no container runtime.

Exit status 0 only if every smoke test passed, none was skipped, and the whole run stayed
under the time budget (SMOKE_BUDGET_SECONDS, default 600).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUDGET = float(os.environ.get("SMOKE_BUDGET_SECONDS", "600"))


def _python(env_var: str, part: str) -> str:
    if os.environ.get(env_var):
        return os.environ[env_var]
    for candidate in (ROOT / part / ".venv" / "Scripts" / "python.exe",
                      ROOT / part / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


PARTS = [
    ("backend", "SMOKE_BACKEND_PYTHON", "tests/smoke"),
    ("sdk", "SMOKE_SDK_PYTHON", "tests/smoke"),
]


def main() -> int:
    started = time.monotonic()
    failed = False
    summary = []
    for part, env_var, target in PARTS:
        python = _python(env_var, part)
        t0 = time.monotonic()
        proc = subprocess.run(
            [python, "-m", "pytest", target, "-p", "no:cacheprovider", "-rA", "-q"],
            cwd=ROOT / part, capture_output=True, text=True,
        )
        took = time.monotonic() - t0
        out = proc.stdout + proc.stderr
        print(out)
        tail = out.strip().splitlines()[-1] if out.strip() else ""
        skipped = re.search(r"(\d+) skipped", tail)
        if proc.returncode != 0 or (skipped and int(skipped.group(1)) > 0):
            failed = True
        summary.append(f"  {part:<8} {took:7.1f} s   exit {proc.returncode}   {tail}   ({python})")
    total = time.monotonic() - started
    over = total > BUDGET
    print("SMOKE SUMMARY")
    print("\n".join(summary))
    print(f"  total    {total:7.1f} s   budget {BUDGET:.0f} s" + ("   OVER BUDGET" if over else ""))
    verdict = "FAILED" if (failed or over) else "PASSED"
    print(f"SMOKE {verdict}")
    return 1 if (failed or over) else 0


if __name__ == "__main__":
    raise SystemExit(main())
