"""EM-3B: gather what can be measured about a backend, then let the checks judge it.

The runner does the executing and nothing else. It runs the probe inside the payload, asks the
runtime what it sees from outside where that is possible, performs the bounded stress runs, looks
for anything the run left behind, and hands all of it to :mod:`checks` as data. Judgement lives
there, which is why the checks can be tested without a container and the gathering can fail without
producing a verdict.

Everything here is defensive by construction: any step that fails records why it failed, and a step
that could not run leaves its checks at ``not_checked``. Nothing in this file can turn a missing
measurement into a passing property -- that is prevented one layer down, in :mod:`report`.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass

from agentnode_sdk.conformance import probe as probe_mod
from agentnode_sdk.conformance.checks import Context, run_all
from agentnode_sdk.conformance.report import ConformanceReport
from agentnode_sdk.sandbox.types import ProcessSpec

#: A runtime name that cannot exist, used to observe what a backend does when it loses its runtime.
ABSENT_RUNTIME = "agentnode-conformance-absent-runtime"

#: The signal ``run_process`` documents for a run it stopped: this return code AND this marker
#: on stderr. Both together are what attributes an ending to the ceiling rather than to chance.
TIMEOUT_RC = -1
TIMEOUT_MARKER = "[sandbox timed out after"

#: A variable name released into the lifecycle run. The VALUE never leaves this process.
CREDENTIAL_PROBE_NAME = "AGENTNODE_CONFORMANCE_RELEASED"


@dataclass(frozen=True)
class SuiteOptions:
    include_network_probe: bool = True
    include_stress: bool = True
    include_outside: bool = True
    probe_timeout: float = 90.0
    wallclock_sleep: int = 30
    wallclock_timeout: float = 5.0
    #: Deliberately above the container's declared memory ceiling, and bounded well below any
    #: host's, so the ceiling is what stops it rather than the machine running out.
    memory_stress_mb: int = 768


def _declared(argv: list, spec: ProcessSpec) -> dict:
    def after(flag):
        for i, item in enumerate(argv):
            if item == flag and i + 1 < len(argv):
                return argv[i + 1]
        return None

    tmpfs = [argv[i + 1] for i, x in enumerate(argv) if x == "--tmpfs" and i + 1 < len(argv)]

    def size_of(prefix):
        for entry in tmpfs:
            if entry.startswith(prefix):
                m = re.search(r"size=([0-9]+[kKmMgG]?)", entry)
                return m.group(1) if m else None
        return None

    home_path = next((v.split("=", 1)[1] for i, x in enumerate(argv) if x == "-e"
                      for v in [argv[i + 1]] if v.startswith("HOME=")), "")
    return {
        "memory": after("--memory"),
        "pids": after("--pids-limit"),
        "cpus": after("--cpus"),
        "tmp_size": size_of("/tmp:"),
        "home_size": size_of(home_path + ":") if home_path else None,
        "home_path": home_path,
        "network": spec.network,
        "mount_targets": [m.dst for m in spec.mounts],
        "env_names": sorted(set(list(spec.env) + list(spec.env_passthrough))),
    }


def _probe_spec(backend, options: SuiteOptions, name: str) -> ProcessSpec:
    opts = json.dumps({"network": options.include_network_probe, "net_timeout": 3})
    return ProcessSpec(command=["python", "-c", probe_mod.PROBE_SOURCE, opts],
                       network="none", clean_home=True, name=name)


def _run(backend, spec, timeout):
    started = time.time()
    rc, out, err = backend.run_process(spec, timeout=timeout)
    return {"rc": rc, "stdout": out, "stderr": err, "elapsed": round(time.time() - started, 2)}


def _gather_probe(backend, options, name):
    try:
        spec = _probe_spec(backend, options, name)
        result = _run(backend, spec, options.probe_timeout)
    except Exception as exc:                                        # noqa: BLE001 - deliberate
        return {}, f"the probe run raised {type(exc).__name__}: {str(exc)[:200]}", None, {}
    try:
        readings = probe_mod.parse(result["stdout"])
    except Exception as exc:                                        # noqa: BLE001
        tail = (result["stderr"] or "")[-200:].strip()
        return ({}, f"the probe produced no readings (rc={result['rc']}): {exc}. "
                    f"stderr tail: {tail}", None, {})
    argv = backend.wrap_command(_probe_spec(backend, options, name))
    return readings, None, argv, _declared(argv, _probe_spec(backend, options, name))


def _inspect_live(backend, options, run_id):
    """Ask the runtime what it sees, from outside, while a container of ours is alive."""
    if not options.include_outside:
        return {}
    name = f"agentnode-conformance-{run_id}-live"
    spec = ProcessSpec(command=["python", "-c", "import time; time.sleep(8)"],
                       network="none", clean_home=True, name=name)
    argv = backend.wrap_command(spec)
    runtime = argv[0]
    proc = None
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            probe = subprocess.run([runtime, "inspect", name], capture_output=True, text=True,
                                   timeout=10)
            if probe.returncode == 0 and probe.stdout.strip().startswith("["):
                return json.loads(probe.stdout)[0]
            time.sleep(0.25)
        return {}
    except Exception:                                               # noqa: BLE001
        return {}
    finally:
        if proc is not None:
            try:
                subprocess.run([runtime, "kill", name], capture_output=True, timeout=10)
            except Exception:                                       # noqa: BLE001
                pass
            try:
                proc.wait(timeout=10)
            except Exception:                                       # noqa: BLE001
                proc.kill()


def _stress(backend, options, run_id):
    """Bounded runs whose OUTCOME is the measurement. Nothing here targets the host."""
    out = {}
    if not options.include_stress:
        return out
    try:
        spec = ProcessSpec(
            command=["python", "-c", f"import time; time.sleep({options.wallclock_sleep})"],
            network="none", clean_home=True, name=f"agentnode-conformance-{run_id}-clock")
        r = _run(backend, spec, options.wallclock_timeout)
        # EM3B review: a duration is not evidence -- an unrelated early exit produces the same
        # elapsed time. The verdict rests on the backend's own timeout signal: the return code it
        # documents for a timeout AND the marker it writes. The elapsed time stays as diagnosis.
        marker = TIMEOUT_MARKER in (r["stderr"] or "")
        out["wallclock"] = {
            "sleep": options.wallclock_sleep, "timeout": options.wallclock_timeout,
            "elapsed": r["elapsed"], "rc": r["rc"],
            "timeout_marker_seen": marker,
            "timeout_signal": bool(marker and r["rc"] == TIMEOUT_RC),
            "stderr_tail": (r["stderr"] or "")[-120:].strip(),
        }
    except Exception as exc:                                        # noqa: BLE001
        out["wallclock"] = {"_error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    try:
        mb = options.memory_stress_mb
        code = (
            "import sys\n"
            "held = []\n"
            f"for _ in range({mb} // 32):\n"
            "    held.append(bytearray(32 * 1024 * 1024))\n"
            "print('ALLOCATED', len(held) * 32)\n"
            "sys.stdout.flush()\n"
        )
        spec = ProcessSpec(command=["python", "-c", code], network="none", clean_home=True,
                           name=f"agentnode-conformance-{run_id}-mem")
        r = _run(backend, spec, 60)
        out["memory"] = {
            "requested_mb": mb, "rc": r["rc"],
            "killed": r["rc"] != 0 or "ALLOCATED" not in (r["stdout"] or ""),
            "stdout_tail": (r["stdout"] or "")[-80:].strip(),
        }
    except Exception as exc:                                        # noqa: BLE001
        out["memory"] = {"_error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    return out


def _leftovers(runtime: str, run_id: str) -> dict | None:
    """What the runtime still lists that belongs to THIS run. Never anyone else's."""
    if not runtime:
        return None
    prefix = f"agentnode-conformance-{run_id}"
    try:
        containers = subprocess.run(
            [runtime, "ps", "-a", "--filter", f"name={prefix}", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=15)
        networks = subprocess.run(
            [runtime, "network", "ls", "--filter", "name=agentnode-egress",
             "--format", "{{.Name}}"], capture_output=True, text=True, timeout=15)
    except Exception:                                               # noqa: BLE001
        return None
    if containers.returncode != 0:
        return None
    return {
        "containers": [x for x in containers.stdout.split() if x],
        "networks": [x for x in networks.stdout.split() if x] if networks.returncode == 0 else [],
        "filtered_on": prefix,
    }


def _host_observations(backend, runtime, run_id) -> dict:
    host = {"leftovers": _leftovers(runtime, run_id)}
    try:
        backend.wrap_command(ProcessSpec(command=["true"], network="none",
                                         env_passthrough=["AGENTNODE_CONFORMANCE_NAME"]))
        host["passthrough_refused"] = False
    except Exception as exc:                                        # noqa: BLE001
        host["passthrough_refused"] = True
        host["passthrough_refusal"] = type(exc).__name__
    try:
        avail = backend.check_available()
        host["runtime_version"] = _runtime_version(runtime) if runtime else None
        host["image"] = getattr(backend, "_image", None)
        host["available_reason"] = avail.reason
    except Exception as exc:                                        # noqa: BLE001
        host["available_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    host.update(_backend_loss(backend))
    # A test double has no runtime binary to ask, so it may supply the outside-vantage
    # observations itself -- and ONLY a double may. The hook is read solely when the double marker
    # is set, and a report carrying that marker can never be conformant, so this cannot become a
    # route for a product backend to answer questions about itself.
    if getattr(backend, "IS_TEST_DOUBLE", False):
        supplied = getattr(backend, "conformance_host_observations", None)
        if callable(supplied):
            host.update(supplied())
    return host


def _runtime_version(runtime: str):
    for args in (["version", "--format", "{{.Server.Version}}"], ["--version"]):
        try:
            r = subprocess.run([runtime, *args], capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and r.stdout.strip():
                return f"{runtime} {r.stdout.strip().splitlines()[0]}"
        except Exception:                                           # noqa: BLE001
            continue
    return None


def _backend_loss(backend) -> dict:
    """What a backend of the same class does when its runtime is gone."""
    cls = type(backend)
    try:
        sibling = cls(runtime=ABSENT_RUNTIME)
    except Exception:                                               # noqa: BLE001
        return {}
    out = {}
    try:
        avail = sibling.check_available()
        message = sibling.explain_unavailable()
        refused, error_type = False, None
        try:
            sibling.open_agent_session(ProcessSpec(command=["true"], network="none"))
        except Exception as exc:                                    # noqa: BLE001
            refused, error_type = True, type(exc).__name__
        out["backend_loss"] = {"available": avail.available, "refused": refused and
                               not avail.available, "error_type": error_type,
                               "reason": avail.reason}
        out["refusal_message"] = message
    except Exception as exc:                                        # noqa: BLE001
        out["backend_loss"] = {"_error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    return out


def run_conformance(backend, *, generated_at: str, options: SuiteOptions | None = None,
                    egress_matrix: dict | None = None,
                    credential_lifecycle: dict | None = None) -> ConformanceReport:
    """Measure what this backend actually does, and report what could not be measured as such.

    ``generated_at`` is supplied by the caller rather than read from the clock here, so a report
    can be reproduced from the same inputs.
    """
    options = options or SuiteOptions()
    run_id = uuid.uuid4().hex[:8]
    is_double = bool(getattr(backend, "IS_TEST_DOUBLE", False))

    available = None
    try:
        available = backend.check_available()
    except Exception:                                               # noqa: BLE001
        pass
    runtime = getattr(available, "backend", "") if available else ""
    runtime = runtime if runtime and runtime != "none" else ""

    if available is not None and not available.available:
        # Nothing can be measured. Say that, once, rather than 23 times in different words.
        ctx = Context(probe_failure=f"the backend is not available: {available.reason}",
                      host={"refusal_message": backend.explain_unavailable()})
        return ConformanceReport(
            backend_identity=type(backend).__name__, backend_version="unknown",
            runtime=runtime or available.backend, image=str(getattr(backend, "_image", "")),
            generated_at=generated_at, results=run_all(ctx), is_test_double=is_double)

    name = f"agentnode-conformance-{run_id}-probe"
    readings, probe_failure, argv, declared = _gather_probe(backend, options, name)
    inspect = _inspect_live(backend, options, run_id) if not probe_failure else {}
    stress = _stress(backend, options, run_id)
    host = _host_observations(backend, runtime, run_id)
    if egress_matrix is not None:
        host["egress_matrix"] = egress_matrix
    if not probe_failure:
        # setdefault, not assignment: a test double may have supplied these through the
        # double-only hook, and a real backend never reaches that hook at all.
        host.setdefault("env_baseline", _env_baseline(backend, options, run_id))
        host.setdefault("cancel", _cancel_probe(backend, options, run_id))
    if credential_lifecycle is not None:
        host["credential_lifecycle"] = credential_lifecycle

    ctx = Context(readings=readings, probe_failure=probe_failure, inspect=inspect,
                  argv=argv or [], declared=declared or {}, stress=stress, host=host)
    return ConformanceReport(
        backend_identity=type(backend).__name__,
        backend_version=str(host.get("runtime_version") or "unknown"),
        runtime=runtime, image=str(getattr(backend, "_image", "")),
        generated_at=generated_at, results=run_all(ctx), is_test_double=is_double)


def measure_egress(backend, *, allowed: str = "example.com", denied: str = "google.com",
                   timeout: float = 120.0) -> dict:
    """Run the bypass matrix inside a container on the internal network. Needs a real runtime.

    This is the only check that has to build something before it can measure: an internal network
    and the dual-homed proxy. It tears both down again, and returns the readings rather than a
    verdict -- ``checks.check_egress_allowlist`` decides what they mean.
    """
    from agentnode_sdk.sandbox import egress as egress_mod

    handle = egress_mod.start_egress_proxy([allowed])
    try:
        spec = ProcessSpec(
            command=["python", "-c", probe_mod.egress_matrix_source(allowed, denied)],
            network="egress", egress=handle.spec, clean_home=True,
            name=f"agentnode-conformance-egress-{uuid.uuid4().hex[:8]}")
        result = _run(backend, spec, timeout)
        return probe_mod.parse_egress(result["stdout"])
    finally:
        egress_mod.stop_egress_proxy(handle)


def _cancel_probe(backend, options, run_id) -> dict:
    """Start something long-running, confirm the runtime sees it, cancel it, confirm it is gone.

    EM3B review: the earlier version reported the timeout run as though it were a cancellation.
    A timeout firing and a cancellation being honoured are different things, and only one of them
    was ever exercised.
    """
    if not options.include_outside:
        return {"_error": "outside observation is switched off, so nothing could be confirmed"}
    name = f"agentnode-conformance-{run_id}-cancel"
    spec = ProcessSpec(command=["python", "-c", "import time; time.sleep(120)"],
                       network="none", clean_home=True, name=name)
    try:
        argv = backend.wrap_command(spec)
    except Exception as exc:                                        # noqa: BLE001
        return {"_error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    runtime = argv[0]
    proc = None
    out = {"name": name, "runtime": runtime}
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        out["was_running"] = False
        for _ in range(60):
            listed = subprocess.run([runtime, "ps", "--filter", f"name={name}",
                                     "--format", "{{.Names}}"],
                                    capture_output=True, text=True, timeout=15)
            if name in listed.stdout:
                out["was_running"] = True
                break
            time.sleep(0.25)
        if not out["was_running"]:
            out["_error"] = "the payload never appeared in the runtime's list, so there was " \
                            "nothing to cancel"
            return out
        subprocess.run([runtime, "kill", name], capture_output=True, timeout=20)
        for _ in range(40):
            listed = subprocess.run([runtime, "ps", "-a", "--filter", f"name={name}",
                                     "--format", "{{.Names}}"],
                                    capture_output=True, text=True, timeout=15)
            if name not in listed.stdout:
                out["gone_after"] = True
                out["still_listed"] = ""
                return out
            time.sleep(0.25)
        out["gone_after"] = False
        out["still_listed"] = listed.stdout.strip()
        return out
    except Exception as exc:                                        # noqa: BLE001
        out["_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        return out
    finally:
        if proc is not None:
            try:
                subprocess.run([runtime, "rm", "-f", name], capture_output=True, timeout=20)
            except Exception:                                       # noqa: BLE001
                pass
            try:
                proc.wait(timeout=10)
            except Exception:                                       # noqa: BLE001
                proc.kill()


def _env_baseline(backend, options, run_id):
    """What the image itself puts in the environment, measured rather than assumed.

    EM3B: the first real run found three variables a hand-written expectation did not know about.
    The baseline is a run that releases nothing; anything beyond it in the measured run has to be
    something this run released.
    """
    try:
        spec = _probe_spec(backend, options, f"agentnode-conformance-{run_id}-baseline")
        result = _run(backend, spec, options.probe_timeout)
        readings = probe_mod.parse(result["stdout"])
    except Exception:                                               # noqa: BLE001
        return None
    names = readings.get("env_names")
    return list(names) if isinstance(names, list) else None


def measure_credential_lifecycle(backend, *, allowed: str = "example.com",
                                 timeout: float = 120.0) -> dict:
    """Release one variable by name into one run, then look for it in the next one.

    The name is released the way the product releases one -- ``--env NAME`` on the
    destination-limited network -- and the VALUE never leaves this process: the probe reports
    names only. What is measured is whether the release outlived the run it was made for.
    """
    from agentnode_sdk.sandbox import egress as egress_mod

    out = {"name": CREDENTIAL_PROBE_NAME}
    previous = os.environ.get(CREDENTIAL_PROBE_NAME)
    os.environ[CREDENTIAL_PROBE_NAME] = "conformance-probe-value-" + uuid.uuid4().hex
    handle = None
    try:
        handle = egress_mod.start_egress_proxy([allowed])
        released = ProcessSpec(
            command=["python", "-c", probe_mod.PROBE_SOURCE, json.dumps({"network": False})],
            network="egress", egress=handle.spec, clean_home=True,
            env_passthrough=[CREDENTIAL_PROBE_NAME],
            name=f"agentnode-conformance-cred-{uuid.uuid4().hex[:8]}")
        first = probe_mod.parse(_run(backend, released, timeout)["stdout"])
        out["present_when_released"] = CREDENTIAL_PROBE_NAME in (first.get("env_names") or [])
    except Exception as exc:                                        # noqa: BLE001
        out["_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return out
    finally:
        if handle is not None:
            try:
                egress_mod.stop_egress_proxy(handle)
            except Exception:                                       # noqa: BLE001
                pass
        if previous is None:
            os.environ.pop(CREDENTIAL_PROBE_NAME, None)
        else:
            os.environ[CREDENTIAL_PROBE_NAME] = previous
    try:
        after = ProcessSpec(
            command=["python", "-c", probe_mod.PROBE_SOURCE, json.dumps({"network": False})],
            network="none", clean_home=True,
            name=f"agentnode-conformance-cred-after-{uuid.uuid4().hex[:8]}")
        second = probe_mod.parse(_run(backend, after, timeout)["stdout"])
        out["present_afterwards"] = CREDENTIAL_PROBE_NAME in (second.get("env_names") or [])
    except Exception as exc:                                        # noqa: BLE001
        out["_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return out
    remaining, problem = _egress_leftovers(handle.runtime if handle is not None else "")
    if problem:
        # EM3B-IMPLEMENTATION-0002 / F1: an exception or a failed command used to be recorded as an
        # empty list, and an empty list reads as "nothing was left behind". A question the runtime
        # would not answer is not an answer, and it must not become the evidence that the
        # credential-carrying network is gone.
        out["_error"] = problem
        return out
    out["leftovers"] = remaining
    return out


def _egress_leftovers(runtime: str):
    """(what remains, why that could not be determined). Exactly one of the two is set."""
    if not runtime:
        return None, "no runtime was resolved, so nothing could be asked about what remained"
    try:
        left = subprocess.run([runtime, "network", "ls", "--filter", "name=agentnode-egress",
                               "--format", "{{.Name}}"], capture_output=True, text=True, timeout=15)
    except Exception as exc:                                        # noqa: BLE001 - deliberate
        return None, f"asking the runtime what remained raised {type(exc).__name__}: {str(exc)[:160]}"
    if left.returncode != 0:
        return None, ("the runtime refused to list what remained: "
                      + (left.stderr or "").strip()[:160])
    return [x for x in left.stdout.split() if x], None
