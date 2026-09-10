"""Where an external run is told it is happening -- as a file, never through a shell.

`EM3C-E5-CLASSIFY-0001`: the fifth external run was told where the gateway was by exporting three
Linux paths in Git Bash and starting a Windows process. The MSYS layer rewrote them on the way,
so the run asked a Linux machine about ``C:/Program Files/Git/home/em3ce1/em3c-state-e5`` and the
far side answered ``sudo: C:/Program: command not found``. Every later step followed from that.
The driver checked its own command line and saw nothing wrong, because nothing was wrong there.

So there is no shell on this path any more. The values live in a JSON file; a launcher hands the
runner the LOCAL path of that file and a digest of it, and nothing else. A local path may be
converted on the way -- ``/c/x`` becoming ``C:/x`` is the same file -- and a hex digest is not
path-like, so neither can be damaged by the conversion this exists to prevent.

Nothing here relies on asking the shell not to convert something. `MSYS2_ARG_CONV_EXCL` is a
request to a program that may not be listening; what makes a value safe is that it is not on the
command line and not in the environment at all.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, fields
from pathlib import PurePosixPath
from typing import Any

#: Bumped when the meaning of a field changes. A configuration from another version is refused
#: rather than read approximately, for the same reason the wire protocol is.
CONFIG_VERSION = 1


class ConfigError(Exception):
    """The run has not been told where it is happening, or has been told something impossible."""


@dataclass(frozen=True)
class Settings:
    """Everything one external run needs to know. This dataclass IS the schema."""

    version: int
    #: Local, on the machine the runner is started on. These may be in whatever form this
    #: platform uses; they never cross to the other machine.
    client_home: str
    agentnode: str
    work: str
    evidence: str
    ssh_key: str
    #: The far machine, as ssh names it.
    server: str
    #: Remote, on the far machine. POSIX, and checked to be.
    gateway_bin: str
    gateway_state: str
    gateway_log: str
    gateway_user: str
    gateway_port: str

    def as_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


FIELD_NAMES = tuple(f.name for f in fields(Settings))

#: The three that are about the OTHER machine. What made the fifth run fail was one of these
#: arriving in this machine's own form.
REMOTE_PATHS = ("gateway_bin", "gateway_state", "gateway_log")

_TYPES: dict[str, tuple] = {name: (str,) for name in FIELD_NAMES}
_TYPES["version"] = (int,)

#: A configuration is not secret -- it holds paths, a host and an account, and no credential. The
#: key is named by its path, never by its content, so the digest below can be recorded as it is.
SECRET_FIELDS: tuple = ()

#: The environment names the fifth run used. Their presence now means somebody is still trying to
#: tell this run where it is happening through a shell, and that is refused rather than ignored:
#: an ignored setting is one whose author believes it took effect.
SUPERSEDED_ENVIRONMENT = (
    "EM3C_BASE", "EM3C_CLIENT_HOME", "EM3C_AGENTNODE", "EM3C_WORK", "EM3C_EVIDENCE",
    "EM3C_SSH_KEY", "EM3C_SERVER", "EM3C_GATEWAY_BIN", "EM3C_GATEWAY_STATE",
    "EM3C_GATEWAY_LOG", "EM3C_GATEWAY_USER", "EM3C_GATEWAY_PORT",
)

_DRIVE = re.compile(r"^[A-Za-z]:")
#: The same shape looked for inside a longer text -- a whole remote script, say -- where a
#: converted value would sit in the middle rather than at the start.
_DRIVE_ANYWHERE = re.compile(r"[A-Za-z]:[/\\]")
#: What a converted value looks like when this particular shell has been through it. Named
#: explicitly because it is the exact shape the fifth run recorded, and a reader deserves to see
#: that the check knows what it is looking for rather than only that it looks.
_SHELL_PREFIXES = ("/program files/git/", "program files/git/", "/usr/bin/", "/mingw64/")


def _no_duplicates(pairs):
    seen: dict = {}
    for key, value in pairs:
        if key in seen:
            raise ConfigError(
                f"the configuration names {key!r} more than once, so which of them was meant is "
                "not something this can decide")
        seen[key] = value
    return seen


def check_remote_path(name: str, value: Any) -> str:
    """A path on the far machine, or a refusal saying what it looks like instead.

    Returns the normalised value. Everything refused here is a shape a POSIX path cannot have,
    and most of them are shapes a POSIX path acquires when something converts it.
    """
    if not isinstance(value, str):
        raise ConfigError(f"{name} is {type(value).__name__} and a path is text")
    if value != value.strip():
        raise ConfigError(f"{name} has space around it, so it is not the path anyone meant")
    if not value:
        raise ConfigError(f"{name} is empty")
    if "\\" in value:
        raise ConfigError(
            f"{name} contains a backslash ({value!r}). The far machine is Linux, and a backslash "
            "is what a path looks like after this one has been through it")
    if _DRIVE.match(value):
        raise ConfigError(
            f"{name} starts with a drive letter ({value!r}). That is a path on THIS machine, and "
            "it is what a Linux path looks like after a shell has converted it")
    lowered = value.lower()
    for prefix in _SHELL_PREFIXES:
        if prefix in lowered:
            raise ConfigError(
                f"{name} contains {prefix!r} ({value!r}), which is a shell's own installation "
                "and not anything on the far machine")
    if value.startswith("//"):
        raise ConfigError(f"{name} is a UNC path ({value!r}), which names no place on Linux")
    if not value.startswith("/"):
        raise ConfigError(
            f"{name} is relative ({value!r}). A relative path depends on where something happens "
            "to be standing, and nothing here knows where the far machine is standing")
    if any(c in value for c in ("\x00", "\r", "\n")):
        raise ConfigError(f"{name} contains a control character")
    parts = PurePosixPath(value).parts
    if ".." in parts:
        raise ConfigError(f"{name} climbs out of itself ({value!r})")
    normalised = str(PurePosixPath(value))
    if normalised != value:
        raise ConfigError(
            f"{name} is not in its normal form: {value!r} would be written {normalised!r}, and a "
            "path that can be written two ways is one whose meaning is guessed at. What runs is "
            "what the configuration says, so the configuration says it once")
    return normalised


def parse(document: Any) -> Settings:
    """One configuration document, or a refusal. Nothing is defaulted and nothing is ignored."""
    if not isinstance(document, dict):
        raise ConfigError("the configuration is not an object")
    unknown = sorted(set(document) - set(FIELD_NAMES))
    if unknown:
        raise ConfigError(
            "the configuration carries " + ", ".join(repr(x) for x in unknown)
            + ", which this version does not describe. A field nobody reads is a setting whose "
            "author believes it took effect")
    missing = [name for name in FIELD_NAMES if name not in document]
    if missing:
        raise ConfigError("the configuration has no " + ", ".join(missing))
    for name, allowed in _TYPES.items():
        value = document[name]
        if isinstance(value, bool) or not isinstance(value, allowed):
            raise ConfigError(
                f"{name} is {type(value).__name__} and the schema says "
                + " or ".join(t.__name__ for t in allowed))
    if document["version"] != CONFIG_VERSION:
        raise ConfigError(
            f"this configuration says version {document['version']} and this build reads "
            f"version {CONFIG_VERSION}. A version it does not implement is refused rather than "
            "guessed at")
    values = dict(document)
    for name in REMOTE_PATHS:
        values[name] = check_remote_path(name, values[name])
    for name in ("server", "gateway_user", "gateway_port", "agentnode", "ssh_key"):
        if not str(values[name]).strip():
            raise ConfigError(f"{name} is empty")
    return Settings(**values)


def digest_of(settings: Settings) -> str:
    """A digest over the whole configuration, canonically.

    Nothing secret is in it -- see `SECRET_FIELDS`, which is empty and says why -- so it can be
    written into the evidence as it is, and a later reader can hold what ran against what was
    meant to run.
    """
    body = {k: v for k, v in settings.as_dict().items() if k not in SECRET_FIELDS}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def refuse_environment(environ=None) -> None:
    """The way the fifth run was told where it was happening is refused, not ignored."""
    env = os.environ if environ is None else environ
    present = sorted(name for name in SUPERSEDED_ENVIRONMENT if env.get(name))
    if present:
        raise ConfigError(
            "these are set: " + ", ".join(present) + ". That is how the fifth external run was "
            "told where the gateway was, and a shell rewrote three of them on the way. They are "
            "refused rather than ignored, because a setting that is quietly ignored is one whose "
            "author believes it took effect. Put the values in the configuration file")


def load(path: str, expected_digest: str = "", environ=None) -> Settings:
    """Read a configuration, establish it, and say so -- before anything else happens.

    `expected_digest`, when given, is what the launcher computed for the file it meant to hand
    over. A hex digest is not path-like, so it survives the boundary the values themselves could
    not, and it is what turns "a file at this path" into "the file that was meant".
    """
    refuse_environment(environ)
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        raise ConfigError(f"the configuration could not be read: {exc}") from None
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except UnicodeDecodeError:
        raise ConfigError("the configuration is not UTF-8") from None
    except ValueError as exc:
        raise ConfigError(f"the configuration is not readable as JSON: {exc}") from None
    settings = parse(document)
    if expected_digest:
        got = digest_of(settings)
        if got != expected_digest:
            raise ConfigError(
                "this is not the configuration that was meant: it hashes to " + got[:16]
                + "... and " + str(expected_digest)[:16] + "... was expected. Nothing has run")
    return settings


def describe(settings: Settings) -> list[str]:
    """What a reader is told about the configuration, in the record and on the way past."""
    lines = ["  configuration digest : " + digest_of(settings),
             "  version              : %d" % settings.version,
             "  server               : " + settings.server,
             "  gateway user / port  : %s / %s" % (settings.gateway_user, settings.gateway_port)]
    for name in REMOTE_PATHS:
        lines.append("  %-20s : %s" % (name, getattr(settings, name)))
    return lines
