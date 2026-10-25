"""The control plane's own observations of an execution target (target-router T8/T9, Codex D4).

What this IS: probe jobs the control plane sends to the target over the target's own mutually
authenticated connection, and outcomes the CONTROL PLANE observes itself:

  * egress -- the job tries to reach a canary listener on the control plane's private address,
    carrying a fresh nonce. A positive control first proves the canary would have seen it. The
    canary seeing nothing is recorded as "no connection observed to the tested destination".
    The job also tries an IPv6 address and a DNS name; those results are what the JOB reported and
    are labelled so;
  * memory -- a job asked for the job ceiling allocates beyond it and must be killed;
  * wall clock -- a job asked for a short wall clock sleeps past it; the control plane times it;
  * identity -- the connection that carried the probes proved the target's expected URI;
  * platform -- what the worker reports about itself, stored ONLY as `reported`.

What this is NOT: proof of a hardware boundary, a kernel, a runtime, an isolation backend or of
"network none" in general. A customer-controlled host can recognise probe jobs and behave for them.
The record says so (`bounds`), and its assurance class says whose host it is
(`operator_managed_measured` for the operator's own worker, `customer_controlled_observed` for a
customer's). Self-report never authorizes anything (`target_router._eligible` reads only `properties`).
"""
from __future__ import annotations

import base64
import json
import secrets
import socket
import threading
import time
import uuid

from agentnode_sdk.gateway import challenge as ch
from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.worker import Job, Limits

SUITE = "agentnode-target-probe/1"
SENTINEL = "TARGETPROBE "
JOB_MEMORY_MB = 1024
JOB_STORAGE_MB = 512
WALL_PROBE_S = 8
BOUNDS = ("observations of the tested destinations, limits and configuration at the measured time only; "
          "not proof of hardware, kernel, runtime or isolation backend, nor of network isolation for "
          "destinations that were not tested; the egress positive control proves the canary listens on "
          "the control plane, and that the worker host's own network could reach it is a separate "
          "operator measurement")


class Canary:
    """A TCP listener on the control plane that records which nonces reached it."""

    def __init__(self, host: str, port: int = 0):
        self.seen: list[str] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(16)
        self._sock.settimeout(0.5)
        self.host, self.port = self._sock.getsockname()[:2]
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                continue
            try:
                conn.settimeout(2.0)
                data = conn.recv(256).decode("ascii", "replace").strip()
                self.seen.append(data or "<empty>")
            except OSError:
                self.seen.append("<connected>")
            finally:
                conn.close()

    def positive_control(self) -> bool:
        nonce = "pc-" + secrets.token_hex(8)
        with socket.create_connection((self.host, self.port), timeout=3) as s:
            s.sendall(nonce.encode("ascii"))
        deadline = time.time() + 3
        while time.time() < deadline:
            if nonce in self.seen:
                return True
            time.sleep(0.05)
        return False

    def close(self):
        self._stop = True
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2)


def _program(body: str) -> bytes:
    return ("import json as _j, socket as _s, time as _t, sys as _sys\n"
            "def _emit(o):\n    print(%r + _j.dumps(o), flush=True)\n" % SENTINEL + body).encode("utf-8")


def egress_program(host: str, port: int, nonce: str) -> bytes:
    return _program(
        "out = {}\n"
        "try:\n"
        "    c = _s.create_connection((%r, %d), timeout=5); c.sendall(%r.encode()); c.close(); out['ipv4'] = 'connected'\n"
        "except Exception as e:\n    out['ipv4'] = type(e).__name__\n"
        "try:\n"
        "    c = _s.socket(_s.AF_INET6, _s.SOCK_STREAM); c.settimeout(5); c.connect(('2606:4700:4700::1111', 443)); c.close(); out['ipv6'] = 'connected'\n"
        "except Exception as e:\n    out['ipv6'] = type(e).__name__\n"
        "try:\n"
        "    _s.getaddrinfo('probe-%s.example.com', 443); out['dns'] = 'resolved'\n"
        "except Exception as e:\n    out['dns'] = type(e).__name__\n"
        "_emit(out)\n" % (host, port, nonce, nonce))


def memory_program(megabytes: int) -> bytes:
    return _program(
        "blocks = []\n"
        "for i in range(%d):\n    blocks.append(bytearray(1024 * 1024))\n"
        "_emit({'allocated_mb': len(blocks)})\n" % megabytes)


def wall_program(seconds: int) -> bytes:
    return _program("_t.sleep(%d)\n_emit({'slept': %d})\n" % (seconds, seconds))


# ---------------------------------------------------------------------- dial-back targets (local-worker R3 / D-0002)
REVERSE_MEMORY_USABLE_MB = 896
STORAGE_WRITE_MB, STORAGE_PAST_MB = 448, 576
CPU_SECONDS = 4


def cpu_program(seconds: int) -> bytes:
    """Two busy PROCESSES (not threads: one interpreter's threads share one core) for `seconds`; the cgroup's
    quota and CPU usage as the job itself sees them."""
    return _program(
        "import os\n"
        "def cg(p):\n    try:\n        return open(p).read().strip()\n    except Exception as e:\n        return type(e).__name__\n"
        "def usage():\n    for line in open('/sys/fs/cgroup/cpu.stat'):\n"
        "        if line.startswith('usage_usec'):\n            return int(line.split()[1])\n    return -1\n"
        "u0 = usage(); t0 = _t.monotonic(); kids = []\n"
        "for _ in range(2):\n    pid = os.fork()\n    if pid == 0:\n        end = _t.monotonic() + %d\n"
        "        while _t.monotonic() < end:\n            pass\n        os._exit(0)\n    kids.append(pid)\n"
        "for pid in kids:\n    os.waitpid(pid, 0)\n"
        "wall = _t.monotonic() - t0; u1 = usage()\n"
        "_emit({'cpu_max': cg('/sys/fs/cgroup/cpu.max'), 'usage_s': (u1 - u0) / 1e6, 'wall_s': wall,\n"
        "       'cpus_visible': len(os.sched_getaffinity(0))})\n" % seconds)


def storage_program(write_mb: int, past_mb: int) -> bytes:
    return _program(
        "import os, errno\n"
        "def write(path, mb):\n    with open(path, 'ab') as f:\n        block = b'\\0' * (1 << 20)\n"
        "        for _ in range(mb):\n            f.write(block)\n        f.flush(); os.fsync(f.fileno())\n"
        "out = {}\n"
        "try:\n    write('/tmp/agentnode-probe-a', %d); out['first'] = 'written'\n"
        "except OSError as e:\n    out['first'] = errno.errorcode.get(e.errno, str(e.errno))\n"
        "try:\n    write('/tmp/agentnode-probe-b', %d); out['past'] = 'written'\n"
        "except OSError as e:\n    out['past'] = errno.errorcode.get(e.errno, str(e.errno))\n"
        "_emit(out)\n" % (write_mb, past_mb - write_mb))


def boundary_program() -> bytes:
    """The platform and the isolation boundary as seen from INSIDE the job (observed, not reported)."""
    return _program(
        "import os\n"
        "def read(p):\n    try:\n        return open(p).read()\n    except Exception as e:\n        return '!' + type(e).__name__\n"
        "u = os.uname(); env = read('/run/.containerenv')\n"
        "engine = ''\n"
        "for line in env.splitlines():\n    if line.startswith('engine='):\n        engine = line.split('=', 1)[1].strip().strip('\"')\n"
        # podman fills .containerenv only for a privileged container; for this one the file's existence is what
        # can be observed (the engine family), and the version stays a claim (DECISION-0003)
        "marker = 'podman' if not env.startswith('!') else ('docker' if os.path.exists('/.dockerenv') else '')\n"
        "caps = [l.split()[1] for l in read('/proc/self/status').splitlines() if l.startswith('CapEff:')]\n"
        "try:\n    open('/agentnode-probe-root', 'w').close(); rootfs = 'writable'\n"
        "except OSError as e:\n    rootfs = 'refused'\n"
        "pids = [d for d in os.listdir('/proc') if d.isdigit()]\n"
        "pid1 = read('/proc/1/cmdline').split('\\0')[0]\n"
        "_emit({'kernel': u.release, 'machine': u.machine, 'engine': engine, 'marker': marker,\n"
        "       'uid_map': read('/proc/self/uid_map').split(), 'cap_eff': caps[0] if caps else '',\n"
        "       'rootfs': rootfs, 'processes_visible': len(pids), 'pid1': pid1})\n")

#: What PID 1 is on a host, never inside a job's own PID namespace.
HOST_INITS = ("systemd", "/sbin/init", "/lib/systemd/systemd", "/usr/lib/systemd/systemd", "init")


def _reverse_observations(worker, prefix: str) -> tuple[dict, dict]:
    """(results, properties) of the extra probes a dial-back target must pass; every value observed in a job."""
    results: dict = {}
    usable = _said(worker.run(_job(memory_program(REVERSE_MEMORY_USABLE_MB),
                                   Limits(memory_mb=JOB_MEMORY_MB, wall_clock_s=60, storage_mb=64),
                                   prefix + "-memory-usable")))
    results["memory_usable"] = {"allocated_mb": (usable or {}).get("allocated_mb"), "ran": usable is not None}
    cpu = _said(worker.run(_job(cpu_program(CPU_SECONDS), Limits(cpu=1.0, memory_mb=256, wall_clock_s=60,
                                                                 storage_mb=64), prefix + "-cpu"))) or {}
    results["cpu"] = cpu
    # the negative control: the same program allowed two cores must use more than one -- else the measurement
    # cannot see beyond one core, and a one-core quota "observed" through it would prove nothing (R3)
    control = _said(worker.run(_job(cpu_program(CPU_SECONDS), Limits(cpu=2.0, memory_mb=256, wall_clock_s=60,
                                                                     storage_mb=64), prefix + "-cpu-control"))) or {}
    results["cpu_control"] = control
    store =_said(worker.run(_job(storage_program(STORAGE_WRITE_MB, STORAGE_PAST_MB),
                                  Limits(memory_mb=JOB_MEMORY_MB, wall_clock_s=90, storage_mb=JOB_STORAGE_MB),
                                  prefix + "-storage"))) or {}
    results["storage"] = store
    seen = _said(worker.run(_job(boundary_program(), Limits(memory_mb=128, wall_clock_s=30, storage_mb=64),
                                 prefix + "-boundary"))) or {}
    results["boundary"] = seen
    wall = float(cpu.get("wall_s") or 0) or 1.0
    usage = float(cpu.get("usage_s") or 0)
    one_core = str(cpu.get("cpu_max") or "").split()[:2] == ["100000", "100000"]
    control_wall = float(control.get("wall_s") or 0) or 1.0
    control_seen = float(control.get("usage_s") or 0) >= 1.5 * control_wall
    cpu_ok = (one_core and int(cpu.get("cpus_visible") or 0) >= 2 and 0.5 * wall <= usage <= 1.25 * wall
              and control_seen)
    uid_map = list(seen.get("uid_map") or [])
    isolation_ok = (uid_map[:3] != ["0", "0", "4294967295"] and len(uid_map) >= 3
                    and str(seen.get("cap_eff") or "") == "0000000000000000" and seen.get("rootfs") == "refused"
                    and 0 < int(seen.get("processes_visible") or 0) <= 16
                    and str(seen.get("pid1") or "") not in HOST_INITS + ("",))
    storage_ok = store.get("first") == "written" and store.get("past") in ("ENOSPC", "EDQUOT")
    properties = {"cpu_quota_enforced": bool(cpu_ok), "isolation_observed": bool(isolation_ok),
                  "storage_mb": JOB_STORAGE_MB if storage_ok else 0,
                  # the engine with its version where the job can see it, else the engine family its marker file
                  # shows; never the worker's own report (DECISION-0003)
                  "platform": {"kernel": str(seen.get("kernel") or ""), "machine": str(seen.get("machine") or ""),
                               "runtime": str(seen.get("engine") or seen.get("marker") or "")}}
    return results, properties


def _job(program: bytes, limits: Limits, run_id: str) -> Job:
    payload = base64.b64encode(program).decode("ascii")
    return Job(run_id=run_id, container_name="agentnode-probe-" + uuid.uuid4().hex[:12],
               command=tuple(ch.bootstrap(command_was_given=False)), artifact=program,
               stdin=ch.on_stdin("", "", payload), network="none", allowed_domains=(), limits=limits)


def _said(outcome) -> dict | None:
    for line in (getattr(outcome, "stdout", "") or "").splitlines():
        if line.startswith(SENTINEL):
            try:
                return json.loads(line[len(SENTINEL):])
            except ValueError:
                return None
    return None


def probe(target: T.Target, worker, canary: Canary, *, now: float | None = None) -> dict:
    """Run the suite against `target` through `worker`; return the observation record (unsigned --
    the register signs it with the whole document when the operator commits it)."""
    if not canary.positive_control():
        raise RuntimeError("the canary did not see its own positive control; no egress observation is possible")
    nonce = "eg-" + secrets.token_hex(12)
    prefix = "probe-" + secrets.token_hex(4)
    results: dict = {}
    # egress
    out = worker.run(_job(egress_program(canary.host, canary.port, nonce),
                          Limits(memory_mb=256, wall_clock_s=30, storage_mb=64), prefix + "-egress"))
    time.sleep(1.0)
    reported = _said(out) or {}
    seen = nonce in canary.seen
    results["egress"] = {"canary_saw_nonce": seen, "job_reported": reported, "positive_control": True}
    # positive controls: the same programs WITHIN their limits must run to the end, or a "killed"
    # below would prove nothing about the limit (the job might fail for any reason at all)
    ok_mem = _said(worker.run(_job(memory_program(64), Limits(memory_mb=JOB_MEMORY_MB, wall_clock_s=60, storage_mb=64),
                                   prefix + "-memory-control")))
    ok_wall = _said(worker.run(_job(wall_program(1), Limits(memory_mb=128, wall_clock_s=WALL_PROBE_S, storage_mb=64),
                                    prefix + "-wall-control")))
    results["controls"] = {"memory_within_limit_ran": ok_mem is not None, "wall_within_limit_ran": ok_wall is not None}
    # memory: asked for the job ceiling, allocates 1.5x
    start = time.time()
    out = worker.run(_job(memory_program(int(JOB_MEMORY_MB * 1.5)),
                          Limits(memory_mb=JOB_MEMORY_MB, wall_clock_s=60, storage_mb=64), prefix + "-memory"))
    mem = _said(out)
    results["memory"] = {"killed": mem is None, "exit_code": getattr(out, "exit_code", None),
                         "seconds": round(time.time() - start, 2)}
    # wall clock: 3s limit, sleeps 8x longer
    start = time.time()
    out = worker.run(_job(wall_program(WALL_PROBE_S * 8), Limits(memory_mb=128, wall_clock_s=WALL_PROBE_S, storage_mb=64),
                          prefix + "-wall"))
    elapsed = time.time() - start
    wall = _said(out)
    results["wall_clock"] = {"killed": wall is None, "elapsed_s": round(elapsed, 2),
                             "within_bound": elapsed < WALL_PROBE_S * 4}
    reverse = bool(getattr(target, "is_reverse", False))
    if reverse:
        results["reverse"], reverse_props = _reverse_observations(worker, prefix)
    # EVERY probe run must have been carried by the target's own proved identity, not only the first:
    # an observation stitched together from runs on different workers observes nothing
    runs = [prefix + s for s in ("-egress", "-memory-control", "-wall-control", "-memory", "-wall")]
    if reverse:
        runs += [prefix + s for s in ("-memory-usable", "-cpu", "-cpu-control", "-storage", "-boundary")]
    proved = {r: (worker.who_ran(r)[1] if hasattr(worker, "who_ran") else "") for r in runs}
    identity = proved[prefix + "-egress"]
    results["identity"] = {"proved_uri": identity, "expected_uri": target.identity_uri,
                           "every_run_proved": sorted(set(proved.values())),
                           "matches": all(v == target.identity_uri for v in proved.values())}
    try:
        platform = {"runtime_version": str(worker.runtime_version()), "image_digest": str(worker.image_digest())}
    except Exception:                                          # noqa: BLE001 - reported facts are optional
        platform = {}
    controls = results["controls"]
    # A job reporting that it GOT OUT is evidence against "network none" on any path; a job
    # reporting that it did not is no evidence at all, so only the canary can support the claim.
    escaped = (reported.get("ipv4") == "connected" or reported.get("ipv6") == "connected"
               or reported.get("dns") == "resolved")
    properties = {"network_none_observed": (not seen) and not escaped,
                  "memory_ceiling_mb": (JOB_MEMORY_MB if results["memory"]["killed"]
                                        and controls["memory_within_limit_ran"] else 0),
                  "wall_clock_enforced": bool(results["wall_clock"]["killed"] and results["wall_clock"]["within_bound"]
                                              and controls["wall_within_limit_ran"]),
                  "storage_mb": JOB_STORAGE_MB, "reported_platform": platform,
                  "bounds": BOUNDS}
    if reverse:
        # local-worker R2/R3: on a dial-back target nothing is hard-coded -- storage, CPU, isolation and the
        # platform are what the jobs observed, and usable memory must have been shown too
        properties.update(reverse_props)
        if not (results["reverse"]["memory_usable"]["ran"] and properties["memory_ceiling_mb"]):
            properties["memory_ceiling_mb"] = 0
    assurance = T.ASSURANCE_OF_TYPE[target.type]
    measured = float(now if now is not None else time.time())
    body = {"assurance": assurance, "suite": SUITE, "measured_at": measured,
            "expires_at": measured + T.ATTESTATION_MAX_SECONDS[assurance] - 60,
            "identity_uri": target.identity_uri, "endpoint_digest": target.endpoint_digest,
            "properties": properties, "results": results, "challenge_nonces": [nonce]}
    body["record_digest"] = T.digest({k: body[k] for k in sorted(body)})
    return body


ESTABLISHED, LOST, INCONCLUSIVE = "established", "lost", "inconclusive"
#: Handshake refusals in which a peer AUTHENTICATED by this deployment's anchor named another identity:
#: positive evidence of a wrong peer. Every other refusal (no certificate, an untrusted chain, validity,
#: revocation-list or floor trouble on this side) cannot tell a wrong peer from broken control-side trust.
WRONG_IDENTITY_CHECKS = frozenset({"instance", "deployment", "role"})


def judge(record: dict) -> tuple[str, list[str]]:
    """A renewal's verdict on a complete record (attest-renewal D4): (established | lost | inconclusive,
    reasons). LOST needs positive evidence: a proved wrong URI, the canary seeing the nonce, the job
    reporting a way out, or a limit that was demonstrably not enforced while its within-limit control ran
    to the end. A control that did not complete, a missing egress report or an unproved run says nothing
    about the target: inconclusive, never lost."""
    r = record.get("results") or {}
    lost, unsure = [], []
    ident = r.get("identity") or {}
    proved = list(ident.get("every_run_proved") or [])
    expected = ident.get("expected_uri")
    if any(p and p != expected for p in proved):
        lost.append("identity")
    elif not proved or any(not p for p in proved):
        unsure.append("identity_unproved")
    egress = r.get("egress") or {}
    said = egress.get("job_reported") or {}
    if egress.get("canary_saw_nonce") or said.get("ipv4") == "connected" or said.get("ipv6") == "connected" \
            or said.get("dns") == "resolved":
        lost.append("network")
    elif not said:
        unsure.append("egress_report_missing")
    controls = r.get("controls") or {}
    mem, wall = r.get("memory") or {}, r.get("wall_clock") or {}
    if not controls.get("memory_within_limit_ran"):
        unsure.append("memory_control")
    elif not mem.get("killed"):
        lost.append("memory")
    if not controls.get("wall_within_limit_ran"):
        unsure.append("wall_control")
    elif not (wall.get("killed") and wall.get("within_bound")):
        lost.append("wall_clock")
    if lost:
        return LOST, lost
    if unsure:
        return INCONCLUSIVE, unsure
    failed = passed(record)
    if failed:                                                 # cannot happen when the above hold; never assumed
        return INCONCLUSIVE, ["requirements:" + ",".join(failed)]
    return ESTABLISHED, []


def passed(record: dict) -> list[str]:
    """Which requirement classes the record does NOT establish (empty list = activation possible)."""
    p, r = record.get("properties") or {}, record.get("results") or {}
    failed = []
    if not p.get("network_none_observed"):
        failed.append("network")
    if int(p.get("memory_ceiling_mb") or 0) < JOB_MEMORY_MB:
        failed.append("memory")
    if not p.get("wall_clock_enforced"):
        failed.append("wall_clock")
    if not (r.get("identity") or {}).get("matches"):
        failed.append("identity")
    if "reverse" in r:
        if int(p.get("storage_mb") or 0) < JOB_STORAGE_MB:
            failed.append("storage")
        if p.get("cpu_quota_enforced") is not True:
            failed.append("cpu")
        if p.get("isolation_observed") is not True:
            failed.append("isolation")
        if not ((p.get("platform") or {}).get("runtime") and (p.get("platform") or {}).get("kernel")):
            failed.append("platform")
    return failed


__all__ = ["BOUNDS", "Canary", "ESTABLISHED", "INCONCLUSIVE", "LOST", "SUITE", "WRONG_IDENTITY_CHECKS", "judge",
           "passed", "probe"]
