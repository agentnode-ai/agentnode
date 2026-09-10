"""What starts an external run, and the only thing a shell is allowed to hand it.

`EM3C-E5-CLASSIFY-0001`: the fifth external run was started from Git Bash with the three remote
paths exported as shell variables. The MSYS layer rewrote them between the shell and the Windows
process, and the run spent twenty-five steps asking a Linux machine about ``C:/Program Files``.

The shell is still where a person types, and that is fine. What changed is what it may pass. It
may pass the LOCAL path of a configuration file, and nothing else; a local path may be converted
on the way and still name the same file. Everything that must survive verbatim is inside that
file, where nothing in between has an opinion about it.

This launcher reads the file, establishes it, computes a digest of it, and starts the runner with
the interpreter that is native to this machine. The runner is given the same local path and that
digest. If anything replaced the file in between, the digest no longer matches and the run stops
before its first step.
"""
from __future__ import annotations

import os
import subprocess
import sys

from agentnode_sdk.verification import config

#: How this process talks to the one it starts. Bytes, like everything else that crosses a
#: boundary here -- see `external_run.launch` and `EM3C-E4-CLASSIFY-0001`.
WIRE = "utf-8"

RUNNER = "agentnode_sdk.verification.run"


class LaunchError(Exception):
    """The run cannot be started as asked, and no child was created."""


def native_interpreter(candidate: str = "") -> str:
    """The Python this machine runs, named as this machine names it.

    On Windows a POSIX-looking interpreter path is a sign that something has been through a shell
    that has its own idea of where things are. `sys.executable` is what the running process
    already is, so it needs no conversion and cannot have been converted.
    """
    chosen = candidate or sys.executable
    if not chosen:
        raise LaunchError("this process cannot say which interpreter it is running")
    if os.name == "nt" and chosen.startswith("/"):
        raise LaunchError(
            "the interpreter is named " + chosen + ", which is not how this machine names a "
            "program. Something between here and the shell has its own idea of where things are, "
            "and that is the class of failure this launcher exists to avoid")
    if not os.path.exists(chosen):
        raise LaunchError("there is no interpreter at " + chosen)
    return chosen


def runner_argv(config_path: str, digest: str, extra=(), python: str = "") -> list[str]:
    """Exactly what the runner is started with. A local path, a digest, and flags.

    Nothing here is a remote value, which is the point: `external_run.check_start` refuses to
    begin if a remote path is on its own command line, and this is the command line it means.
    """
    if not digest:
        raise LaunchError("a run is not started without the digest of the configuration it uses")
    return [native_interpreter(python), "-m", RUNNER,
            "--config", config_path, "--expect", digest, *extra]


def prepare(config_path: str, environ=None):
    """Read the configuration and say what it is, before anything is started.

    Returns `(settings, digest)`. Raises `ConfigError` if the file is not a configuration this
    build can run, which is the same refusal the runner would make -- made here first, so the
    person who typed the command hears it rather than reading it out of a record.
    """
    settings = config.load(config_path, "", environ)
    return settings, config.digest_of(settings)


def start(config_path: str, extra=(), python: str = "", environ=None, timeout=None):
    """Start the runner as a real child process and wait for it.

    The child's streams come back as bytes and are decoded here, deliberately and once. No
    `text=`, no `encoding=`: those put a translating wrapper on a stream, and on Windows that
    wrapper rewrites line endings -- which is what `EM3C-E4-CLASSIFY-0001` was.
    """
    settings, digest = prepare(config_path, environ)
    argv = runner_argv(config_path, digest, extra, python)
    child = dict(os.environ if environ is None else environ)
    for name in config.SUPERSEDED_ENVIRONMENT:
        child.pop(name, None)      # not passed on; the runner refuses them and it would be right
    done = subprocess.run(argv, capture_output=True, timeout=timeout, check=False, env=child)
    return (done.returncode,
            done.stdout.decode(WIRE, "replace"),
            done.stderr.decode(WIRE, "replace"),
            settings, digest, argv)


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="agentnode-external-launch",
        description="Start an external evidence run from a configuration file.")
    parser.add_argument("--config", required=True,
                        help="local path to the configuration file for this run")
    parser.add_argument("--preflight", action="store_true",
                        help="have the run say where it believes it is happening, and stop")
    parser.add_argument("--evidence", default="", help="where the run writes its record")
    args = parser.parse_args(argv)

    extra = (["--preflight"] if args.preflight else [])
    if args.evidence:
        extra += ["--evidence", args.evidence]
    try:
        code, out, err, settings, digest, started = start(args.config, extra)
    except (config.ConfigError, LaunchError) as exc:
        print("  Nothing was started:")
        print("    " + str(exc))
        return 2
    print("  started " + " ".join(started[:3]) + " ... --expect " + digest)
    sys.stdout.write(out)
    sys.stderr.write(err)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
