"""Which source this artefact was built from, written INTO it at build time.

A deployment used to be handed a commit and believe it. `deploy-pinned.sh` took the commit as an
argument and compared it against an expectation only when an optional environment variable
supplied one, so the answer to "which source is this wheel?" was whatever the caller said.
`ALPHA-RUNTIME-PIN-0002` named that, and it was right: nothing tied the artefact to a commit.

So the build writes it in. `PROVENANCE_NAME` is part of the package, which means it is part of the
wheel, which means it is covered by the wheel's digest -- changing the commit changes the artefact.
A deployment reads it back OUT of the wheel before installing anything and refuses if it is absent
or disagrees. The operator can still lie to the build; what they can no longer do is hand a
deployment an artefact and a commit that have nothing to do with each other.
"""
from __future__ import annotations

import json
import pathlib
import zipfile

#: Inside the installed package and inside the wheel, at the same relative path.
PROVENANCE_NAME = "_provenance.json"


def written_beside_this_module() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent / PROVENANCE_NAME


def record(commit: str, where: pathlib.Path | None = None) -> pathlib.Path:
    """Write the commit into the source tree, so the build carries it into the wheel."""
    path = where or written_beside_this_module()
    path.write_text(json.dumps({"commit": str(commit or "")}, indent=1) + "\n",
                    encoding="utf-8")
    return path


def of_this_installation() -> str:
    """The commit this installed package was built from, or "" when it does not say."""
    try:
        said = json.loads(written_beside_this_module().read_text(encoding="utf-8"))
    except Exception:                                             # noqa: BLE001
        return ""
    return str(said.get("commit") or "") if isinstance(said, dict) else ""


def of_a_wheel(wheel) -> str:
    """The commit a wheel was built from, read WITHOUT installing it.

    Before anything is installed is the only moment this is worth asking: a deployment that
    discovers the artefact is the wrong one after replacing the running code has discovered it
    too late.
    """
    try:
        with zipfile.ZipFile(str(wheel)) as inside:
            names = [n for n in inside.namelist()
                     if n.endswith("agentnode_sdk/" + PROVENANCE_NAME)]
            if not names:
                return ""
            said = json.loads(inside.read(names[0]).decode("utf-8"))
    except Exception:                                             # noqa: BLE001
        return ""
    return str(said.get("commit") or "") if isinstance(said, dict) else ""
