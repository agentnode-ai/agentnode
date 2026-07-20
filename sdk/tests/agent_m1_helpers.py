"""Shared helpers for the Agent-Exec A1-M1 test suite.

Two kinds of test:
* pip-free logic tests build *synthetic* wheels (plain ZIPs) in memory and exercise
  the import-free validators / lock / CAS logic directly;
* integration tests need a pip-capable interpreter to build + install real wheels —
  ``pip_python()`` finds one (the CI interpreter has pip) or skips.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


def pip_python() -> str:
    """Return a Python interpreter that has pip, or skip the test.

    Prefers ``sys.executable`` (the CI test interpreter has pip). Falls back to a
    few well-known local interpreters so the suite is runnable on a dev machine
    whose pytest venv (uv-managed) lacks pip.
    """
    candidates = [sys.executable]
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        for ver in ("Python312", "Python311", "Python313"):
            candidates.append(os.path.join(local, "Programs", "Python", ver, "python.exe"))
    for c in candidates:
        if not c or not os.path.isfile(c):
            continue
        try:
            r = subprocess.run([c, "-m", "pip", "--version"], capture_output=True, timeout=30)
            if r.returncode == 0:
                return c
        except (OSError, subprocess.TimeoutExpired):
            continue
    pytest.skip("no pip-capable interpreter available for wheel build/install")


def make_target_venv(base_python: str, dest: Path) -> str:
    """Create a fresh venv (with pip) under *dest*; return its python path."""
    subprocess.run([base_python, "-m", "venv", str(dest)], check=True,
                   capture_output=True, timeout=180)
    py = dest / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(py)


# ---------------------------------------------------------------------------
# Synthetic wheel construction (no pip) — for import-free validator tests.
# ---------------------------------------------------------------------------

def make_wheel(
    path: Path,
    *,
    name: str,
    version: str,
    dist_info_name: str | None = None,
    dist_info_version: str | None = None,
    payload: dict[str, str] | None = None,
    top_level_txt: str | None = None,
    include_record: bool = True,
    extra_metadata: list[str] | None = None,
    extra_members: dict[str, str] | None = None,
) -> Path:
    """Write a minimal but structurally-valid wheel ZIP to *path*.

    ``payload`` maps archive member path → text (e.g. ``{"pkg/__init__.py": ""}``).
    The dist-info dir uses ``dist_info_name``/``dist_info_version`` when given (to
    forge an incoherent identity), else ``name``/``version``.
    """
    din = (dist_info_name or name).replace("-", "_")
    div = dist_info_version or version
    dist_info = f"{din}-{div}.dist-info"
    payload = payload or {}
    members: dict[str, str] = dict(payload)

    md = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    if extra_metadata:
        md.extend(extra_metadata)
    members[f"{dist_info}/METADATA"] = "\n".join(md) + "\n"
    members[f"{dist_info}/WHEEL"] = "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    if top_level_txt is not None:
        members[f"{dist_info}/top_level.txt"] = top_level_txt
    if include_record:
        rows = [f"{m},," for m in members]
        rows.append(f"{dist_info}/RECORD,,")
        members[f"{dist_info}/RECORD"] = "\n".join(rows) + "\n"
    if extra_members:
        members.update(extra_members)

    with zipfile.ZipFile(str(path), "w") as zf:
        for m, content in members.items():
            zf.writestr(m, content)
    return path


def make_raw_zip(path: Path, members: dict[str, str]) -> Path:
    """Write an arbitrary ZIP (for unsafe-member / bad-shape tests)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for m, content in members.items():
            zf.writestr(m, content)
    path.write_bytes(buf.getvalue())
    return path
