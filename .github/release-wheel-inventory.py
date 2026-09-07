"""Inspect a built wheel: name, version, dependencies, file inventory, unexpected content.

Run as: python release-wheel-inventory.py <dist-dir> <version>

It reads the distribution files only. It never imports the package, so what it reports is what is
inside the archive rather than what the package says about itself once installed.
"""
from __future__ import annotations

import re
import sys
import tarfile
import zipfile
from pathlib import Path


def main() -> int:
    dist = Path(sys.argv[1])
    version = sys.argv[2]
    whl = dist / f"agentnode_sdk-{version}-py3-none-any.whl"
    sdist = dist / f"agentnode_sdk-{version}.tar.gz"
    problems: list[str] = []

    z = zipfile.ZipFile(whl)
    names = z.namelist()
    meta = z.read(f"agentnode_sdk-{version}.dist-info/METADATA").decode("utf-8")

    print("--- METADATA ---")
    for line in meta.splitlines():
        if re.match(r"^(Name|Version|Requires-Python|Requires-Dist|License|Summary):", line):
            print("   ", line)

    if "Name: agentnode-sdk" not in meta:
        problems.append("wheel METADATA does not declare Name: agentnode-sdk")
    if f"Version: {version}" not in meta:
        problems.append(f"wheel METADATA does not declare Version: {version}")

    tops = sorted({n.split("/")[0] for n in names})
    print(f"--- top-level entries in the wheel ({len(names)} files) ---")
    for t in tops:
        print("   ", t)
    allowed = {"agentnode_sdk", f"agentnode_sdk-{version}.dist-info"}
    unexpected = sorted(set(tops) - allowed)
    if unexpected:
        problems.append(f"unexpected top-level content in the wheel: {unexpected}")

    suspicious = [n for n in names
                  if n.endswith((".pyc", ".pyo", ".env", ".pypirc"))
                  or "/__pycache__/" in n
                  or n.startswith("tests/") or "/tests/" in n
                  or n.endswith(".key") or n.endswith(".pem")]
    if suspicious:
        problems.append(f"unexpected files in the wheel: {suspicious[:20]}")
    else:
        print("--- no bytecode, no tests, no dotenv, no key material ---")

    # the sdist is a source archive: it MAY carry tests, but must not carry secrets or bytecode
    with tarfile.open(sdist) as t:
        snames = t.getnames()
    print(f"--- sdist: {len(snames)} entries ---")
    sbad = [n for n in snames
            if n.endswith((".pyc", ".pyo", ".env", ".pypirc", ".key", ".pem"))
            or "/__pycache__/" in n]
    if sbad:
        problems.append(f"unexpected files in the sdist: {sbad[:20]}")
    else:
        print("--- sdist carries no bytecode and no key material ---")

    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print("  FAIL ", p)
        return 1
    print("\n  the distribution inventory is what it should be")
    return 0


if __name__ == "__main__":
    sys.exit(main())
