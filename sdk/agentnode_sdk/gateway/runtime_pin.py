"""What this service is allowed to run as, and the refusals that keep it that way.

## Why this exists

The R2 deployment worked and was accepted, and the acceptance named the thing that stopped the
operating state from being called finished: the machine ran the gateway on the system Python
3.14, while CI tests 3.10-3.12 and `requires-python` carries no upper bound. Five
install-transaction tests fail on 3.14 and pass on 3.12 -- established on the same machine, in
both directions. A serving path that happened to work is a narrower claim than "this is tested".

So the interpreter stops being whatever the machine offers and becomes something the service
DECLARES and REFUSES to run without.

## Three things, checked separately

`Pin` records three facts and each is checked on its own, because each is a different mistake and
an operator needs to be told which one they made:

* **the interpreter** -- a service running on an untested Python is the whole reason this file
  exists;
* **the artefact** -- the digest of the wheel it was installed from. `0.24.1` was installed
  before and after a deployment that changed the code, so a version number cannot be an identity;
* **the commit** -- which source the artefact was built from. A wheel with the right digest built
  from an unknown tree is a wheel nobody can review.

A single check that conflated them could only say "something is wrong", which is not an answer
anybody can act on.

## Fail-closed, and what that means here

An unreadable pin refuses. Not "assume it is fine" -- a service that cannot tell what it is
allowed to be is not a service that should decide it is allowed. `absent` is treated separately
from `unreadable`: a machine with no pin at all has not been set up yet and says so, while a pin
that exists and cannot be read is a machine somebody should look at.

## What this does NOT establish

That the pinned interpreter is free of defects, or that the tests which pass on it are enough.
It establishes that the interpreter is the one that was tested, which is a different and smaller
claim -- and the only one this file is entitled to make.

An operator with root can edit the pin. That is not a hole; it is what root means. What this
stops is a service drifting onto an untested interpreter without anybody choosing it.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys

#: Beside the state, not inside it: the pin is about the INSTALLATION, and a state directory
#: restored from a backup taken on another machine must not carry that machine's pin with it.
#:
#: This was written before the pin was put in the state directory anyway, and the drill found it
#: within the hour: the drill destroys the state directory and rebuilds it from an archive, so
#: the gateway's pin went with it and came back describing the previous build, while the worker's
#: -- one directory away -- was untouched and right. Both live outside the state now.
PIN_NAME = "runtime-pin.json"

#: Where a service looks for its pin when nobody tells it otherwise. Beside the worker's key,
#: which is a directory both service accounts can read and no backup rewrites.
DEFAULT_PIN_DIR = "/etc/agentnode"


def pin_dir(explicit: str = "") -> str:
    """The directory holding this installation's pin.

    Explicit wins, then the environment, then the default. Deliberately NOT derived from the
    state directory: that is the thing a restore replaces, and a pin that a restore can replace
    is a pin that can tell a machine it is something it is not.
    """
    import os as _os

    return str(explicit or _os.environ.get("AGENTNODE_PIN_DIR") or DEFAULT_PIN_DIR)

#: The interpreter this service is tested on and pinned to. A tuple rather than a string so
#: "3.12.7 is 3.12" is a comparison rather than a substring match -- `"3.1"` is a prefix of
#: `"3.14"`, and that is exactly the confusion this file was written about.
SUPPORTED = (3, 12)


class NotWhatWasPinned(Exception):
    """The running service is not what the pin says it should be. Always refuse.

    Carries `which` so a caller can say WHICH of the three failed. A refusal that only says
    "mismatch" makes an operator guess between an interpreter, an artefact and a commit.
    """

    def __init__(self, which: str, said: str, what_to_do: str) -> None:
        super().__init__(said)
        self.which = which
        self.said = said
        self.what_to_do = what_to_do


class NoPinAtAll(Exception):
    """There is no pin. Distinct from an unreadable one, and distinct on purpose.

    A machine with no pin has not been pinned yet, which is a setup step somebody has not run.
    A machine whose pin cannot be read has a pin and a problem, and those want different answers.
    """


def running_python() -> str:
    return "%d.%d.%d" % sys.version_info[:3]


def is_supported(version: str) -> bool:
    """Whether a version string is the interpreter family this service is tested on.

    Two things this gets right that the first version of it did not:

    * Compared as NUMBERS. `"3.14".startswith("3.1")` is true, so a string comparison would call
      the untested interpreter supported -- the precise shape of the defect this module exists
      to prevent.
    * NO DEFAULT. It used to fall back to the running interpreter when given an empty string,
      so a caller reading a version out of a record that had none got "supported" -- and got it
      from a question it never asked. An empty version is UNKNOWN, and unknown is not supported.
      Its own test caught that, which is the only reason it is not still there.
    """
    parts = str(version or "").split(".")
    try:
        return (int(parts[0]), int(parts[1])) == SUPPORTED
    except (IndexError, ValueError):
        return False


def running_is_supported() -> bool:
    """The same question about the interpreter this is running on. Separate on purpose: "is
    this string a tested version" and "am I a tested version" are different questions, and
    letting one stand in for the other is what made the empty string mean yes."""
    return is_supported(running_python())


def build_id(commit: str, artefact_sha256: str) -> str:
    """The managed service's own identity: where it came from, and what it is.

    Deliberately NOT a version number, and deliberately not derived from one. `0.24.1` was
    installed before and after a deployment that changed the code; anything that can be true of
    two different builds cannot identify either. Short enough to read aloud, long enough that a
    collision is not the interesting failure.
    """
    return "managed-%s+%s" % ((commit or "unknown")[:12], (artefact_sha256 or "unknown")[:12])


def digest_of(path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def installed_artefact_digest(site_packages=None) -> str:
    """The digest recorded when this package was installed, or "" if it was not recorded.

    Read from the distribution's own metadata rather than recomputed: a wheel is not on the
    machine after installation, and recomputing something from the unpacked files would be
    inventing a number rather than reading the one that was installed.
    """
    try:
        import importlib.metadata as md

        dist = md.distribution("agentnode-sdk")
        recorded = dist.read_text("AGENTNODE_ARTEFACT") or ""
        return recorded.strip()
    except Exception:                                             # noqa: BLE001
        return ""


def pin_path(root) -> pathlib.Path:
    return pathlib.Path(root) / PIN_NAME


def write_pin(root, *, python_version: str, artefact_sha256: str, commit: str,
              **extra) -> pathlib.Path:
    """Record what this installation is allowed to be. Written by a deployment, never by a start."""
    said = {
        "python_version": python_version,
        "artefact_sha256": artefact_sha256,
        "commit": commit,
        "build_id": build_id(commit, artefact_sha256),
    }
    said.update({k: v for k, v in extra.items() if v not in (None, "")})
    where = pin_path(root)
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(json.dumps(said, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(where, 0o600)
    return where


def read_pin(root) -> dict:
    """The pin, or a refusal. Absent and unreadable are different answers."""
    where = pin_path(root)
    try:
        raw = where.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise NoPinAtAll(
            "this installation has no runtime pin at %s, so it cannot say what it is allowed "
            "to run as" % where) from None
    except OSError as exc:
        raise NotWhatWasPinned(
            "pin", "the runtime pin at %s could not be read: %s" % (where, exc),
            "Look at the file. A pin that cannot be read is not a pin that can be trusted, so "
            "this service will not start until somebody does.") from exc
    try:
        said = json.loads(raw)
    except ValueError as exc:
        raise NotWhatWasPinned(
            "pin", "the runtime pin at %s is not readable JSON: %s" % (where, exc),
            "Write it again from the deployment that installed this, or re-run that "
            "deployment.") from exc
    if not isinstance(said, dict):
        raise NotWhatWasPinned("pin", "the runtime pin at %s is not an object" % where,
                               "Re-run the deployment that wrote it.")
    return said


def check(root, *, artefact_sha256: str = "", commit: str = "") -> dict:
    """Raise `NotWhatWasPinned` unless this service is what the pin says. Returns the pin.

    The three checks are separate and each names itself. `artefact_sha256` and `commit` may be
    passed by a caller that knows them from somewhere other than the installed metadata -- a
    deployment checking BEFORE it installs, for instance, which is the case the running service
    cannot cover.
    """
    said = read_pin(root)

    wanted = str(said.get("python_version") or "")
    running = running_python()
    if not wanted:
        raise NotWhatWasPinned("interpreter", "the pin names no python version",
                               "Re-run the deployment; a pin without an interpreter pins nothing.")
    if not is_supported(wanted):
        raise NotWhatWasPinned(
            "interpreter",
            "the pin names python %s, which is not the tested family %d.%d"
            % (wanted, *SUPPORTED),
            "This service is tested on python %d.%d. Pin that, or change what is tested."
            % SUPPORTED)
    if running.rsplit(".", 1)[0] != wanted.rsplit(".", 1)[0]:
        raise NotWhatWasPinned(
            "interpreter",
            "this service is running on python %s and is pinned to %s" % (running, wanted),
            "Start it from the pinned environment. Its path is in the systemd unit, and a unit "
            "pointing at any other interpreter is the thing that went wrong.")

    on_disk = artefact_sha256 or installed_artefact_digest()
    expected = str(said.get("artefact_sha256") or "")
    # A PIN THAT NAMES NO ARTEFACT PINS NOTHING, and is refused for the same reason a pin that
    # names no interpreter is. The two used to be treated differently: an empty interpreter
    # refused and an empty digest quietly passed every comparison below, so a pin with the field
    # missing looked like a pin that agreed. The only writer is `write_pin`, which always records
    # all three -- so an absent one means the file was edited by hand or written by something
    # else, and neither is a thing to start on.
    if not expected:
        raise NotWhatWasPinned(
            "artefact", "the pin names no artefact digest",
            "Re-run the deployment; a pin without a digest cannot tell this installation from "
            "any other build carrying the same version number.")
    if expected and on_disk and on_disk != expected:
        raise NotWhatWasPinned(
            "artefact",
            "the installed artefact is %s and the pin expects %s" % (on_disk[:16], expected[:16]),
            "Install the artefact this installation was pinned to, or deploy again and let the "
            "deployment write a new pin. The version number is the same either way, which is "
            "why the digest is what is compared.")
    if expected and not on_disk:
        raise NotWhatWasPinned(
            "artefact",
            "the pin expects artefact %s and the installed distribution records none"
            % expected[:16],
            "Reinstall from a wheel deployed by this project's deployment script, which records "
            "the digest it installed.")

    pinned_commit = str(said.get("commit") or "")
    if not pinned_commit:
        raise NotWhatWasPinned(
            "commit", "the pin names no commit",
            "Re-run the deployment; a pin without a commit cannot say which source the installed "
            "artefact was built from.")
    here = commit or pinned_commit
    if here != pinned_commit:
        raise NotWhatWasPinned(
            "commit", "this deployment says commit %s and the pin says %s"
            % (here[:12], pinned_commit[:12]),
            "Build the artefact from the commit named in the pin, or deploy that commit.")
    return said
