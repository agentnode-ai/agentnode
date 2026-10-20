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
    # EVERY probe run must have been carried by the target's own proved identity, not only the first:
    # an observation stitched together from runs on different workers observes nothing
    runs = [prefix + s for s in ("-egress", "-memory-control", "-wall-control", "-memory", "-wall")]
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
    return failed


__all__ = ["BOUNDS", "Canary", "ESTABLISHED", "INCONCLUSIVE", "LOST", "SUITE", "WRONG_IDENTITY_CHECKS", "judge",
           "passed", "probe"]
