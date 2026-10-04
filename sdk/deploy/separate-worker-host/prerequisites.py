#!/usr/bin/env python3
"""What each role needs on the host before any of this can run, and whether it is there.

## Why this file exists at all

The two machines the cross-host acceptance run was measured on could not run the documented deploy path as
they came. `tar` was absent on both, and the worker had no container runtime; both were installed by hand
before the documented steps would work. A step that exists only as something somebody typed once is not a
deploy path, and the merge plan named that as owed.

## Where the list comes from

Not from memory. Every program below is here because a file in this repository invokes it, and the scan that
says which file and which line is recorded beside this arc's evidence. Two entries were in an earlier draft
of this file and are NOT here, because that scan says nothing calls them: `openssl` -- the PKI uses the
`cryptography` library, not the command -- and `getent`, which no script invokes. Both are present on the
real hosts, which is exactly why believing they were needed cost nothing until something changed.

A few programs are marked `through the runtime`: nothing of ours invokes them, podman does. They are listed
because a podman that cannot find its OCI runtime or its network backend cannot start a container or build
the internal network the egress boundary is made of, and a host in that state should say so here rather than
when the first job arrives.

## Why ONE file, and why the stdlib only

It is read by two callers that must not drift apart:

  * the install scripts, which run BEFORE the virtual environment exists -- `tar` is how the artefact is
    unpacked, so a check that needs the artefact cannot check for `tar`;
  * the product's own preflight, which runs on every worker start as `ExecStartPre` and must refuse a host
    that is missing something rather than failing in the middle.

A shell table and a Python table would be two tables. This is the table. It imports nothing but the standard
library so the first caller can use it on a host where nothing of ours is installed yet.

## PATH is not the only place a program lives

`netavark` and `aardvark-dns` are not on PATH on Rocky 10: podman keeps them in /usr/libexec/podman. A check
that only looked at PATH reported the working worker host of this arc as NOT READY -- a correct host refused
by its own preflight, which is worse than no check at all. So a need may name the other places it is
legitimately found, and those are absolute paths in this file rather than anything a caller can supply.

## What it will NOT do

It will not unify the roles. A container runtime is what the WORKER runs foreign code with; the control
plane does not run foreign code and is not given one, and its service account is not put in any group that
exists to let an account reach a runtime. The gateway asking for podman "because the worker needs it" is how
a control plane acquires a way to run containers it has no business running.

It will not reach outside the host's own configured package sources. It prints, and with `--install` runs,
`dnf install` against whatever repositories the host already trusts; it never adds a repository, never
fetches from a URL of its own and never takes a package name from its arguments.

usage:
  python3 prerequisites.py --role worker            # a human-readable report; exit 1 if anything is missing
  python3 prerequisites.py --role control-plane --json
  python3 prerequisites.py --role worker --install  # install ONLY what is missing, from the host's repos
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

#: The version of this table. Bumped when what a role needs changes, so a host can say which table it was
#: checked against and two hosts checked months apart are comparable.
TABLE_VERSION = 1

WORKER = "worker"
CONTROL_PLANE = "control-plane"
ROLES = (WORKER, CONTROL_PLANE)

#: How a program comes to be needed. `INVOKED` means a file in this repository runs it, and the scan of the
#: deploy path names the file and the line. `THROUGH_THE_RUNTIME` means nothing of ours runs it and podman
#: does -- a weaker claim, said out loud rather than quietly mixed in with the others.
INVOKED = "invoked by our own files"
THROUGH_THE_RUNTIME = "not invoked by us: podman needs it"


class Need:
    """One prerequisite: the program, the package that provides it, why, and where else to look.

    `program` is what the deploy path actually invokes -- which is the thing that matters, because a
    package can be installed and still not put the command on PATH. `package` is what would install it on
    this family of distributions. `why` is in the report, so an operator reading "podman is missing" also
    reads what stops working without it. `at` names absolute paths where the program legitimately lives
    off PATH; they are in this file and can never come from a caller.
    """

    def __init__(self, program: str, package: str, why: str, *, optional: bool = False,
                 how: str = INVOKED, at: tuple = ()) -> None:
        self.program = program
        self.package = package
        self.why = why
        self.optional = optional
        self.how = how
        self.at = tuple(at)


#: WHAT EACH ROLE NEEDS, and nothing else. Every INVOKED entry is here because a file in this repository
#: calls it; the scan that says which file and which line is this arc's V0014.
NEEDS = {
    CONTROL_PLANE: (
        Need("systemctl", "systemd",
             "the control plane runs as a systemd service, and install.sh enables and starts it"),
        Need("python3", "python3",
             "the venv the gateway runs from is built with the host's python3, and this table is read "
             "with it"),
        Need("tar", "tar",
             "the artefact is a tar.gz, and upgrade.sh and rollback.sh both unpack and repack with tar. "
             "It was absent on both machines of the cross-host run and nothing checked"),
        Need("sha256sum", "coreutils",
             "the installer verifies the artefact's digest before unpacking it"),
        Need("install", "coreutils",
             "every directory, unit file and config this script writes is written with install(1), with "
             "an owner and a mode"),
        Need("ip", "iproute",
             "the installer reports which addresses this host has, so an operator can see what it bound"),
        Need("ss", "iproute",
             "diagnose.sh reads which sockets are listening, which is how an operator sees whether the "
             "port is where it should be and nowhere else"),
        Need("useradd", "shadow-utils",
             "the service account is created if it is not there"),
    ),
    WORKER: (
        Need("systemctl", "systemd",
             "the worker and its runtime helper are systemd services"),
        Need("loginctl", "systemd",
             "a rootless service has to linger, or its containers die with the session"),
        Need("python3", "python3",
             "the venv the worker runs from is built with the host's python3, and this table is read "
             "with it"),
        Need("tar", "tar",
             "the artefact is a tar.gz, and upgrade.sh and rollback.sh both unpack and repack with tar"),
        Need("sha256sum", "coreutils",
             "the installer verifies the artefact's digest before unpacking it"),
        Need("install", "coreutils",
             "every directory, unit file and config this script writes is written with install(1)"),
        Need("ip", "iproute",
             "the installer checks that the address it is about to bind is a private one that exists "
             "on this machine"),
        Need("ss", "iproute",
             "diagnose.sh reads which sockets are listening"),
        Need("nft", "nftables",
             "read by diagnose.sh to say what the host firewall holds. The egress boundary does NOT "
             "write host firewall rules -- that would need a privileged helper reachable from the "
             "component that runs foreign code -- so this is for reading, not for enforcing"),
        Need("useradd", "shadow-utils",
             "the service account is created if it is not there"),
        Need("usermod", "shadow-utils",
             "subuid and subgid ranges are set for the rootless account"),
        Need("podman", "podman",
             "THE WORKER'S OWN, and the one line of the two roles that differs on purpose: foreign code "
             "runs in a rootless container. It was absent on the worker of the cross-host run"),
        Need("crun", "crun", how=THROUGH_THE_RUNTIME,
             why="the OCI runtime podman starts containers with on this family. Without it podman is "
                 "installed and cannot start anything"),
        Need("conmon", "conmon", how=THROUGH_THE_RUNTIME,
             why="podman's container monitor; a container cannot be supervised without it"),
        Need("netavark", "netavark", how=THROUGH_THE_RUNTIME,
             at=("/usr/libexec/podman/netavark",),
             why="the network backend. The egress boundary is an --internal network with no gateway "
                 "address at all, and netavark is what creates it on this family. It is NOT on PATH "
                 "here, which is why this need names where podman keeps it"),
        Need("pasta", "passt", how=THROUGH_THE_RUNTIME,
             why="rootless networking for the proxy's own way out of the host"),
        Need("aardvark-dns", "aardvark-dns", optional=True, how=THROUGH_THE_RUNTIME,
             at=("/usr/libexec/podman/aardvark-dns",),
             why="podman's container DNS. The payload network is created with DNS DISABLED -- a resolver "
                 "reachable on the internal bridge is a host-side process a payload can talk to, which "
                 "this arc measured -- so the egress path does not need it and a host without it is not "
                 "broken"),
    ),
}


def _rpm(package: str) -> str:
    """What the package database says is installed, or "" -- never what the install command asked for."""
    if not shutil.which("rpm"):
        return ""
    try:
        done = subprocess.run(["rpm", "-q", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}|%{VENDOR}",
                               package], capture_output=True, text=True, timeout=30)
    except Exception:                                                 # noqa: BLE001
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _where(need: Need) -> str:
    """Where this program is on this host, or "". PATH first, then the places this file names.

    The extra places are absolute paths out of the table. Nothing a caller passes can add one, because a
    check that can be told where to look is a check that can be told to find anything.
    """
    found = shutil.which(need.program)
    if found:
        return found
    for candidate in need.at:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return ""


def inspect(role: str) -> dict:
    """What this host has and has not, for one role. Structured, and with the command that would fix it."""
    if role not in NEEDS:
        raise SystemExit("unknown role %r: it is one of %s" % (role, ", ".join(ROLES)))
    present, missing = [], []
    for need in NEEDS[role]:
        where = _where(need)
        installed = _rpm(need.package)
        version, vendor = (installed.split("|", 1) + [""])[:2] if installed else ("", "")
        entry = {"program": need.program, "package": need.package, "why": need.why,
                 "optional": need.optional, "how": need.how, "found_at": where,
                 "package_version": version, "package_vendor": vendor}
        (present if where else missing).append(entry)
    required_missing = [m for m in missing if not m["optional"]]
    return {
        "role": role,
        "table_version": TABLE_VERSION,
        "ready": not required_missing,
        "present": present,
        "missing": missing,
        "required_missing": required_missing,
        # EXECUTABLE, not advisory. One command, only the packages that are actually missing, against the
        # repositories this host already has. Empty when there is nothing to do, which is what makes a
        # second run a no-op rather than a reinstall.
        "to_install": (["dnf", "install", "-y"] + sorted({m["package"] for m in required_missing})
                       if required_missing else []),
    }


def install(role: str) -> dict:
    """Install ONLY what is missing, from the host's own repositories. Idempotent by construction.

    It does not run the package manager when nothing is missing -- which is what keeps a second run from
    changing a correct host -- and it re-inspects afterwards rather than trusting the exit code, because a
    package manager that succeeded is not the same statement as a program being there.
    """
    before = inspect(role)
    if not before["required_missing"]:
        return {"ran": False, "why": "nothing is missing", "before": before, "after": before}
    done = subprocess.run(before["to_install"], capture_output=True, text=True, timeout=30 * 60)
    after = inspect(role)
    return {"ran": True, "argv": before["to_install"], "exit_code": done.returncode,
            "stdout_tail": (done.stdout or "")[-2000:], "stderr_tail": (done.stderr or "")[-2000:],
            "before": before, "after": after}


def _say(report: dict) -> None:
    print()
    print("  Prerequisites for the %s role, table version %d" % (report["role"], report["table_version"]))
    print()
    for entry in report["present"]:
        print("  ok       %-14s %-28s %s" % (entry["program"], entry["package_version"] or "(no package)",
                                             entry["found_at"]))
    for entry in report["missing"]:
        print("  %-8s %-14s %s" % ("MISSING" if not entry["optional"] else "absent",
                                   entry["program"], entry["why"]))
    print()
    if report["required_missing"]:
        print("  This host cannot run the %s role yet. One command fixes it, against the repositories"
              % report["role"])
        print("  this host already has:")
        print()
        print("      " + " ".join(report["to_install"]))
        print()
    else:
        print("  Everything this role needs is here.")
        print()


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(add_help=True, description="what a role needs on this host")
    parser.add_argument("--role", required=True, choices=list(ROLES))
    parser.add_argument("--json", action="store_true", help="the report as JSON, for a caller")
    parser.add_argument("--install", action="store_true",
                        help="install only what is missing, from this host's own repositories")
    args = parser.parse_args(argv[1:])
    if args.install:
        if os.geteuid() != 0:
            print("installing needs root; run the report without --install to see what is missing")
            return 2
        got = install(args.role)
        if args.json:
            print(json.dumps(got, indent=1, sort_keys=True))
        else:
            if not got["ran"]:
                print("  nothing was missing, so nothing was installed")
            else:
                print("  ran: %s -> exit %s" % (" ".join(got["argv"]), got["exit_code"]))
            _say(got["after"])
        return 0 if got["after"]["ready"] else 1
    report = inspect(args.role)
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
    else:
        _say(report)
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
