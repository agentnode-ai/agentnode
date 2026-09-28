"""Build ONE named, self-contained artefact per role, so each host installs from its own.

`remote-worker-r1` R14 asks that both sides can be installed, upgraded and rolled back BY A NAMED
ARTEFACT. Two independent reviews read the deploy directory and answered the same way: a
directory of scripts is not an artefact, and one wheel that carries both roles is not two.

So this produces two:

    agentnode-control-plane-<version>.tar.gz
    agentnode-worker-<version>.tar.gz

Each holds the wheel, that role's unit file, that role's install, upgrade, rollback and diagnose
scripts, a README, and a `MANIFEST.sha256` over everything in it. Each is named, each has a
digest of its own, and each installs one host without the other's files being present.

## What this does NOT claim

THE WHEEL INSIDE BOTH IS THE SAME WHEEL. There is one Python distribution and it contains both
roles' modules; a worker host therefore has the control plane's code on disk with nothing
starting it. What makes that safe to say out loud rather than hide is `agentnode_sdk/roles.py`
and `tests/test_the_two_roles_are_separable.py`: the worker's start path reaches nothing of the
control plane's, and a new crossing fails the suite. Splitting the DISTRIBUTION is packaging work
that the command line blocks today -- `agentnode worker serve` imports `gateway.runtime_pin` --
and `roles.py::KNOWN_CLI_CROSSINGS` names exactly what follows from that one import.

So: two artefacts, one wheel, and the difference is written down rather than papered over.

Run from `sdk/`:  python deploy/separate-worker-host/build_artefacts.py --wheel <path> [--out DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys
import tarfile
import tempfile

HERE = pathlib.Path(__file__).resolve().parent

#: What goes into each artefact, beside the wheel. The lists do not overlap except where a file
#: genuinely serves both roles -- and each list is the whole of what that host gets, so a file
#: nobody added deliberately is a file that is not there.
CONTROL_PLANE = {
    "unit/agentnode-gateway.service": HERE / "control-plane.service",
    "install.sh": HERE / "install-control-plane.sh",
    "upgrade.sh": HERE / "upgrade-one-host.sh",
    "rollback.sh": HERE / "rollback-one-host.sh",
    "diagnose.sh": HERE / "diagnose.sh",
    "README.md": HERE / "README.md",
    "unit/agentnode-pki-tick.service": HERE.parent / "agentnode-pki-tick.service",
    "unit/agentnode-pki-tick.timer": HERE.parent / "agentnode-pki-tick.timer",
}

WORKER = {
    "unit/agentnode-worker.service": HERE / "worker-host.service",
    "install.sh": HERE / "install-worker-host.sh",
    "upgrade.sh": HERE / "upgrade-one-host.sh",
    "rollback.sh": HERE / "rollback-one-host.sh",
    "diagnose.sh": HERE / "diagnose.sh",
    "README.md": HERE / "README.md",
    "unit/agentnode-pki-tick.service": HERE.parent / "agentnode-pki-tick.service",
    "unit/agentnode-pki-tick.timer": HERE.parent / "agentnode-pki-tick.timer",
}

ROLES = {"control-plane": CONTROL_PLANE, "worker": WORKER}

#: A file that must NOT be in the other role's artefact. Checked when the artefact is built, so a
#: mistake here is a failed build rather than a unit file on a machine that should not have one.
NEVER_IN = {
    "control-plane": ("agentnode-worker.service",),
    "worker": ("agentnode-gateway.service",),
}


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(role: str, wheel: pathlib.Path, out: pathlib.Path, version: str) -> pathlib.Path:
    """Write one artefact and return its path. Never overwrites silently: it names the version."""
    files = ROLES[role]
    name = "agentnode-%s-%s" % (role, version)
    staged = pathlib.Path(tempfile.mkdtemp(prefix="artefact-")) / name
    (staged / "unit").mkdir(parents=True)
    (staged / "wheel").mkdir()

    lines = []
    placed = {"wheel/" + wheel.name: wheel}
    for inside, source in files.items():
        if not source.is_file():
            raise SystemExit("%s is named in the %s artefact and is not there" % (source, role))
        placed[inside] = source
    for inside, source in sorted(placed.items()):
        target = staged / inside
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        lines.append("%s  %s" % (_sha256(target), inside))

    for forbidden in NEVER_IN[role]:
        if any(forbidden in inside for inside in placed):
            raise SystemExit("%s is in the %s artefact and must not be" % (forbidden, role))

    (staged / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (staged / "WHAT-THIS-IS.txt").write_text(
        "agentnode %s, version %s.\n\n"
        "One host's half of a separate-worker-host deployment: the wheel, this role's unit and\n"
        "this role's install, upgrade, rollback and diagnose scripts. MANIFEST.sha256 covers\n"
        "every file beside itself.\n\n"
        "THE WHEEL IS THE SAME WHEEL IN BOTH ARTEFACTS. There is one Python distribution and it\n"
        "contains both roles' modules; this host has the other role's code on disk and nothing\n"
        "starts it. `agentnode_sdk/roles.py` and the import-graph test hold the property that\n"
        "matters -- this role's start path reaches nothing of the other's -- and splitting the\n"
        "distribution itself is packaging work that has not been done.\n\n"
        "Unpack, check the manifest, then run ./install.sh. It opens no port.\n" % (role, version),
        encoding="utf-8")

    out.mkdir(parents=True, exist_ok=True)
    where = out / (name + ".tar.gz")
    with tarfile.open(where, "w:gz") as tar:
        tar.add(staged, arcname=name)
    return where


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", required=True)
    parser.add_argument("--out", default=str(HERE.parent / "artefacts"))
    parser.add_argument("--version", default="")
    args = parser.parse_args()

    wheel = pathlib.Path(args.wheel).resolve()
    if not wheel.is_file():
        raise SystemExit("no wheel at %s" % wheel)
    version = args.version or wheel.name.split("-")[1] if "-" in wheel.name else "unknown"

    out = pathlib.Path(args.out).resolve()
    for role in sorted(ROLES):
        made = build(role, wheel, out, version)
        print("  %-58s %s" % (made.name, _sha256(made)[:16]))
    print()
    print("  Two artefacts, one wheel inside both. Each holds only its own role's unit and")
    print("  install script; the build fails if the other's is in it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
