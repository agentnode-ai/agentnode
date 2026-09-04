"""0.24.1: container-present verification against the INSTALLED release artefact.

The 0.24.0 script (`release-artifact-smoke.py`) is left untouched — it is the evidence of that
release and is pinned to its version. This is its 0.24.1 successor and adds what 0.24.0 had no
reason to check: the three EM-3B-R1 runtime properties, proved against the distributed wheel
rather than against a repository checkout.

Nothing here imports from `sdk/`. It runs only against what `pip install <wheel>` put on the
path, and it lives outside `sdk/` so that adding it cannot change the artefact it verifies.

The three R1 properties are measured, never inferred:

* a payload that ignores every signal it may ignore is still ended, and its container is gone
  afterwards **by both id and name**, established by asking the runtime and requiring it to
  answer — a command that failed is not an empty list of containers;
* an allocation larger than the declared ceiling does not complete, which is what makes the
  ceiling a ceiling rather than a flag that was passed;
* a refusal carries at least one action that exists on this platform, and names a re-check.

Every check prints PASS/FAIL; the process exits non-zero if any check failed.
"""
from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from pathlib import Path

EXPECTED_VERSION = "0.24.1"
FAILS = 0

_NO_SUCH = re.compile(r"no such (object|container)|No such container", re.I)

IGNORING_PAYLOAD = (
    "import signal, sys, time\n"
    "for name in ('SIGTERM', 'SIGINT', 'SIGHUP', 'SIGQUIT', 'SIGUSR1', 'SIGUSR2'):\n"
    "    s = getattr(signal, name, None)\n"
    "    if s is not None:\n"
    "        try:\n"
    "            signal.signal(s, signal.SIG_IGN)\n"
    "        except Exception:\n"
    "            pass\n"
    "print('IGNORING', flush=True)\n"
    "sys.stdout.flush()\n"
    "time.sleep(600)\n"
)

# Allocate well above the 512 MiB ceiling, touching each page so it cannot be lazily mapped.
ALLOCATING_PAYLOAD = (
    "import sys\n"
    "chunks = []\n"
    "for _ in range(768):\n"
    "    b = bytearray(1024 * 1024)\n"
    "    b[::4096] = b'\\x01' * len(b[::4096])\n"
    "    chunks.append(b)\n"
    "print('ALLOCATED', len(chunks), flush=True)\n"
    "sys.stdout.flush()\n"
)


def chk(cond: bool, msg: str) -> None:
    global FAILS
    print(("  PASS  " if cond else "  FAIL  ") + msg, flush=True)
    if not cond:
        FAILS += 1


def _absent(runtime: str, ident: str) -> bool:
    """Absent means the runtime SAID so, not that it failed to answer."""
    r = subprocess.run([runtime, "inspect", "--format", "{{.Id}}", ident],
                       capture_output=True, text=True)
    if r.returncode == 0:
        return False
    return bool(_NO_SUCH.search((r.stderr or "") + (r.stdout or "")))


def _watch_for_container(runtime: str, prefix: str, seconds: float = 20.0):
    """Poll until a container whose name carries `prefix` is RUNNING; return (name, id)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        listed = subprocess.run(
            [runtime, "ps", "--filter", f"name={prefix}", "--format", "{{.Names}} {{.ID}}"],
            capture_output=True, text=True)
        if listed.returncode != 0:
            time.sleep(0.25)
            continue
        for line in listed.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0].startswith(prefix):
                return parts[0], parts[1]
        time.sleep(0.25)
    return None, None


def main() -> int:
    import agentnode_sdk

    origin = Path(agentnode_sdk.__file__).resolve()
    chk("site-packages" in str(origin), f"imported from the installed distribution: {origin}")
    chk(agentnode_sdk.__version__ == EXPECTED_VERSION,
        f"installed version is {agentnode_sdk.__version__} (expected {EXPECTED_VERSION})")

    from agentnode_sdk.sandbox.container_backend import ContainerBackend
    from agentnode_sdk.sandbox.types import ProcessSpec

    backend = ContainerBackend()
    av = backend.check_available()
    chk(av.available, f"container runtime + pinned image available: backend={av.backend} "
                      f"image={av.image_available} digest={(av.image_digest or '')[:24]}")
    if not av.available:
        print("  runtime unavailable — the R1 properties cannot be measured", flush=True)
        return 1
    runtime = av.backend

    # ---------------------------------------------------------------- R1/R1: the timeout ends it
    print("\n[R1] a payload that ignores its deadline", flush=True)
    name = "agentnode-rc-ignoring-payload"
    watcher: list = [None, None]

    def watch():
        watcher[0], watcher[1] = _watch_for_container(runtime, name)

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    rc, out, err = backend.run_process(
        ProcessSpec(command=["python", "-c", IGNORING_PAYLOAD], network="none", name=name),
        timeout=8)
    thread.join(timeout=5)
    observed_name, observed_id = watcher

    chk("IGNORING" in (out or ""), "the payload really started and said so from inside")
    chk(bool(observed_name and observed_id),
        f"the runtime listed it running under this run's own identity: {observed_name}")
    chk(observed_name is not None and observed_name.startswith(name) and observed_name != name,
        f"the run carries a generated identity, not the bare name: {observed_name!r}")
    chk(rc == -1, f"the documented timeout code came back (got {rc})")
    chk("timed out" in (err or ""), "the result says it timed out")
    if observed_id:
        chk(_absent(runtime, observed_id), f"the container is gone BY ID ({observed_id})")
    if observed_name:
        chk(_absent(runtime, observed_name), f"the container is gone BY NAME ({observed_name})")
    listed = subprocess.run([runtime, "ps", "-a", "--filter", f"name={name}",
                             "--format", "{{.Names}}"], capture_output=True, text=True)
    chk(listed.returncode == 0, "the runtime could be asked what remains (an empty answer from a "
                                "failed command is not an empty list)")
    chk(listed.returncode == 0 and name not in listed.stdout,
        "nothing of the payload remains in any state, running or exited")

    # ------------------------------------------------------------- R1/R2: the ceiling binds
    print("\n[R2] an allocation larger than the declared ceiling", flush=True)
    rc2, out2, err2 = backend.run_process(
        ProcessSpec(command=["python", "-c", ALLOCATING_PAYLOAD], network="none"), timeout=180)
    chk(rc2 != 0, f"a 768 MiB allocation under a 512 MiB ceiling did NOT complete (rc={rc2})")
    chk("ALLOCATED" not in (out2 or ""),
        "the payload never reached its own success line")
    argv = backend.wrap_command(
        backend.build_process_spec(["true"], network="none", mounts=[], env={}, limits={},
                                   clean_home=True))
    mem = argv[argv.index("--memory") + 1] if "--memory" in argv else None
    swap = argv[argv.index("--memory-swap") + 1] if "--memory-swap" in argv else None
    chk(swap is not None and mem == swap,
        f"the distributed wheel emits --memory {mem} and --memory-swap {swap} (equal = bound)")

    # ------------------------------------------------------- R1/R3: a refusal carries a way out
    print("\n[R3] structured refusal", flush=True)
    from agentnode_sdk.sandbox import refusal as R
    from agentnode_sdk.sandbox.types import SandboxAvailability

    def avail(**kw):
        """A probe result shaped like the real one, for a machine that cannot run the sandbox."""
        base = dict(available=False, backend="docker", reason="release-candidate probe",
                    daemon_ok=True, image_available=True, engine_os="linux",
                    memory_limit_enforceable=True, probe_error="")
        base.update(kw)
        return SandboxAvailability(**{k: v for k, v in base.items()
                                      if k in SandboxAvailability.__dataclass_fields__})

    situations = [
        ("not_installed", dict(availability=avail(backend="none"), platform="linux")),
        ("not_running", dict(availability=avail(daemon_ok=False), platform="linux")),
        ("not_permitted", dict(availability=avail(daemon_ok=False,
                                                  probe_error="permission denied while trying to "
                                                              "connect to the Docker daemon socket"),
                               platform="linux")),
        ("incompatible", dict(availability=avail(engine_os="windows"), platform="win32")),
        ("memory_ceiling_unenforceable",
         dict(availability=avail(memory_limit_enforceable=False), platform="linux")),
        ("image_placeholder", dict(availability=avail(), platform="linux", placeholder=True)),
        ("image_missing", dict(availability=avail(), platform="linux")),
        ("platform_unsupported", dict(availability=avail(), platform="ios")),
    ]
    seen = set()
    for label, kwargs in situations:
        ref = R.classify(which=lambda tool: "/usr/bin/" + tool, **kwargs)
        if ref is None:
            chk(False, f"{label}: classify returned None where a refusal was expected")
            continue
        seen.add(ref.case.value)
        acts = tuple(ref.actions)
        chk(len(acts) >= 1, f"{ref.case.value}: carries at least one action ({len(acts)})")
        chk(bool(ref.recheck.strip()), f"{ref.case.value}: names a way to re-check")
        chk(bool(ref.headline.strip()) and bool(ref.prevented.strip()),
            f"{ref.case.value}: says what happened and what it prevented")
        chk(any(not a.informational for a in acts),
            f"{ref.case.value}: at least one action is executable here, not only a pointer")

    chk(len(seen) >= 7, f"the installed wheel distinguishes {len(seen)} situations: {sorted(seen)}")

    withheld = R.classify(availability=avail(daemon_ok=False), platform="linux",
                          which=lambda tool: None)
    chk(withheld is not None and len(tuple(withheld.actions)) >= 1,
        "with no tool present at all, a refusal still offers something (never nothing)")
    if withheld is not None:
        leaked = [a for a in withheld.actions
                  if getattr(a, "requires", None) and not a.informational]
        chk(not leaked, f"no action requiring an absent tool is offered ({len(leaked)} leaked)")

    chk(R.classify(availability=avail(available=True), platform="linux") is None,
        "an available runtime produces no refusal")


    print(f"\n  TOTAL FAILURES: {FAILS}", flush=True)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
