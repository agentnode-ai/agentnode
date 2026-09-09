"""The external evidence run: the client half of the two-machine matrix.

Two runs died before this one and neither touched the product. `EM3C-E1-CLASSIFY-0001` found an
unguarded launch turning an expected absence into a crash, and a set of synthetic successes.
`EM3C-E2-CLASSIFY-0001` found the evidence contract itself broken, and then found more in the
rewrite: every SSH failure collapsed into an empty string, which a container-absence check read as
"gone"; and a locally failed `ls` was being offered as proof that two machines were two.

This file is built on what those determined.

* Nothing raises. `launch()` turns a missing executable and a timeout into an outcome.
* A step's exit code is an observation. `observed()` exists so a predicate is recorded as what was
  seen, never as what was hoped.
* Remote work is one command per step, so the recorded status is that command's own.
* `server_query()` returns a structured result carrying whether it ran, its own status, both
  streams apart, and whether the answer parsed. Absence is concluded only from a query that
  succeeded and was read.
* Separation is established by each machine describing itself over its own channel, and by two
  random sentinels that cross: one made on the client and found on the gateway, one made on the
  gateway and found on the client. Neither side vouches for itself.
* The operator-policy binding is captured from the active authenticated snapshot before and after
  the run, and compared.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import secrets as secretslib
import socket
import subprocess
import sys
import time
from pathlib import Path

def _setting(name: str, fallback: str) -> str:
    """One setting, from the environment or from its documented default.

    Nothing here is read at import beyond the environment, and nothing is written. The previous
    version set `AGENTNODE_HOME` and edited `sys.path` while being imported, which made the file
    impossible to load in a test without changing the process it was loaded into.
    """
    return os.environ.get(name, fallback)


BASE = Path(_setting("EM3C_BASE", str(Path.home() / "em3c")))
HOME = Path(_setting("EM3C_CLIENT_HOME", str(BASE / "clienthome")))
AN = _setting("EM3C_AGENTNODE", "agentnode")
WORK = Path(_setting("EM3C_WORK", str(BASE / "work")))
EVIDENCE = Path(_setting("EM3C_EVIDENCE", str(BASE / "client-evidence.jsonl")))
KEY = _setting("EM3C_SSH_KEY", "")
SERVER = _setting("EM3C_SERVER", "")
GW = _setting("EM3C_GATEWAY_BIN", "agentnode")
STATE = _setting("EM3C_GATEWAY_STATE", "")
LOG = _setting("EM3C_GATEWAY_LOG", "")
#: The unprivileged account the gateway runs as, and the loopback port it listens on. Settings
#: rather than literals: a driver carrying one operator's account name is a driver about that
#: operator's machine, and this one has to be about whichever two machines it is pointed at.
GATEWAY_USER = _setting("EM3C_GATEWAY_USER", "")
PORT = _setting("EM3C_GATEWAY_PORT", "8099")

from agentnode_sdk.gateway import client as gc                     # noqa: E402
from agentnode_sdk.gateway.connections import ConnectionStore      # noqa: E402
from agentnode_sdk.tools import evidence                            # noqa: E402

RUN_ID = re.compile(r"run:\s*([0-9a-f]{8,})")
rec = None


def digest(text: str) -> str:
    """The digest of a value, or nothing at all when there was no value.

    `EM3C-EVIDENCE-0005`: hashing an empty string yields a perfectly ordinary-looking digest, so
    a machine that could not read its own identity reported one anyway -- and two machines that
    each failed in a different way could even look like two. An absent value stays absent.
    """
    text = str(text).strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def launch(argv, timeout=600.0):
    """Run a command and always return (exit_code, stdout, stderr, error_class). Never raises."""
    try:
        done = subprocess.run(list(argv), capture_output=True, text=True,
                              timeout=timeout, check=False)
        return done.returncode, done.stdout or "", done.stderr or "", ""
    except FileNotFoundError as exc:
        return None, "", str(exc), "FileNotFoundError"
    except subprocess.TimeoutExpired as exc:
        out, err = exc.stdout or "", exc.stderr or ""
        return None, (out.decode("utf-8", "replace") if isinstance(out, bytes) else out), \
            (err.decode("utf-8", "replace") if isinstance(err, bytes) else err), "TimeoutExpired"
    except OSError as exc:
        return None, "", str(exc), type(exc).__name__


def ssh_argv(command):
    return ["ssh", "-n", "-i", KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
            SERVER, command]


MARKER = "E3-END-OF-LISTING"


def server_query(command, timeout=300.0) -> dict:
    """One remote command, as a structured result that never hides a failure.

    The previous harness returned "" for every failure, so a container listing that could not run
    was indistinguishable from one that found nothing. Everything a reader needs to tell those
    apart is kept.
    """
    # Every listing ends with a marker, so an empty answer and a truncated one are different
    # things. Without it, "nothing came back" and "nothing is there" are the same string.
    full = command + '; echo "' + MARKER + '"'
    code, out, err, error_class = launch(ssh_argv(full), timeout=timeout)
    return {"ran": code is not None or bool(error_class),
            "exit_code": code, "stdout": out, "stderr": err,
            "error_class": error_class, "parsed": code == 0 and not error_class,
            "complete": MARKER in out, "command": full}


def server_step(name, command, *, expected_exit=0, timeout=600.0, **fields):
    code, out, err, error_class = launch(ssh_argv(command), timeout=timeout)
    return rec.record(step(
        name=name, role="gateway", argv=evidence.redact_argv(ssh_argv(command)),
        started_at=time.time(), ended_at=time.time(), exit_code=code,
        stdout=out, stderr=err, expected_exit=expected_exit, error_class=error_class, **fields))


#: What a step expects when it expects nothing in particular. Written out rather than left to
#: defaults: `EM3C-EVIDENCE-0004` found `observed()` stating one of the five, which the recorder
#: refuses -- so every step built through it would have raised on the first call, and the whole
#: matrix with it. Stating them here is a decision recorded once, not a default applied silently.
NOTHING_EXPECTED = {"expected_exit": None, "expected_refusal": "", "expect_output": False,
                    "expect_cleanup": False, "expect_container_gone": False}


def step(**values):
    """One step, with every expectation answered before it is recorded."""
    stated = dict(NOTHING_EXPECTED)
    stated.update({k: v for k, v in values.items() if k in evidence.EXPECTATIONS})
    rest = {k: v for k, v in values.items() if k not in evidence.EXPECTATIONS}
    return evidence.Step(**stated, **rest)


def observed(name, argv, holds, detail, *, role="client", **fields):
    """A step whose exit code IS the observation: 0 when what it names was seen, 1 when not."""
    return rec.record(step(
        name=name, role=role, argv=list(argv),
        started_at=time.time(), ended_at=time.time(),
        exit_code=0 if holds else 1, expected_exit=0,
        stdout=detail if detail.endswith("\n") else detail + "\n", stderr="", **fields))


def connection():
    saved = ConnectionStore().get("e3")
    return gc.GatewayConnection(base_url=saved.url, token=saved.token,
                                gateway_id=saved.gateway_id, fingerprint=saved.fingerprint), saved


def gateway_record(run_id):
    if not run_id:
        return {"error": "the client never printed a run id"}
    try:
        conn, _saved = connection()
        status, body = gc._get(f"{conn.base_url}/v1/jobs/{run_id}", token=conn.token)
    except Exception as exc:                                       # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    if status != 200:
        return {"status": status, "error": body.get("error", f"HTTP {status}")}
    return body


def live_secrets():
    found = []
    for path in HOME.rglob("*"):
        if not path.is_file():
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except Exception:                                          # noqa: BLE001
            continue

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if isinstance(value, str) and len(value) >= 16 and any(
                            t in key.lower() for t in ("token", "secret", "code", "key")):
                        found.append(value)
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(document)
    return found


# --------------------------------------------------------------------------- identity


def _both(one: str, two: str) -> str:
    """Two values joined, or nothing when either is missing."""
    return (one + two) if (one and two) else ""


def as_command(query):
    """One identity command, with its own status and both streams, as the record requires."""
    return {"command": query.get("command", ""), "exit_code": query.get("exit_code"),
            "stdout": query.get("stdout", ""), "stderr": query.get("stderr", "")}


def machines():
    """Each machine describes itself over its own channel, and every command that produced a
    value is kept with its own status -- an identity assembled from commands nobody can check is
    an assertion."""
    # `EM3C-EVIDENCE-0004`: the client's digest was taken over the hostname and the command
    # record beside it held the platform name, so nothing connected the stored digest to any
    # command. Each value is now produced by a real command whose own output is kept, and the
    # digest is taken over THAT output rather than over a second lookup that agrees with it.
    host_code, host_out, host_err, host_cls = launch(["hostname"], timeout=60)
    fs_code, fs_out, fs_err, fs_cls = launch(["cmd", "/c", "vol", "C:"], timeout=60)
    os_code, os_out, os_err, os_cls = launch(["cmd", "/c", "ver"], timeout=60)
    client_host = host_out.strip()
    client_fs = fs_out.strip()
    client_os = os_out.strip()
    complete = host_code == 0 and fs_code == 0 and os_code == 0 and bool(client_host)
    observed("client identity, reported by the client",
             ["hostname", "cmd /c vol C:", "cmd /c ver"], complete,
             f"hostname exit={host_code} volume exit={fs_code} version exit={os_code}\n"
             f"{client_os}\n",
             machine={"role": "client",
                      # over the command's own stdout, which is recorded below
                      "host_sha256": digest(client_host),
                      "filesystem_sha256": digest(client_fs),
                      "os": client_os,
                      "commands": [
                          {"command": "hostname", "exit_code": host_code,
                           "stdout": host_out, "stderr": host_err or host_cls},
                          {"command": "cmd /c vol C:", "exit_code": fs_code,
                           "stdout": fs_out, "stderr": fs_err or fs_cls},
                          {"command": "cmd /c ver", "exit_code": os_code,
                           "stdout": os_out, "stderr": os_err or os_cls}]})

    host = server_query("hostname")
    machine_id = server_query("cat /etc/machine-id")
    kernel = server_query("uname -sr")
    fs = server_query("findmnt -no UUID /")
    ok = all(q["parsed"] for q in (host, machine_id, kernel, fs))
    observed("gateway identity, reported by the gateway over its own channel",
             ssh_argv("hostname; machine-id; uname; findmnt"), ok,
             f"kernel={kernel['stdout'].strip()}\n", role="gateway",
             machine={"role": "gateway", "host_sha256": digest(host["stdout"].replace(MARKER, "").strip()),
                      # Both halves, or neither: concatenating a value with
                      # a missing one gives a digest unlike every other, which is
                      # exactly what makes it look like an identity.
                      "filesystem_sha256": digest(_both(
                          machine_id["stdout"].replace(MARKER, "").strip(),
                          fs["stdout"].replace(MARKER, "").strip())),
                      "os": kernel["stdout"].replace(MARKER, "").strip(),
                      "commands": [as_command(q) for q in (host, machine_id, kernel, fs)]})


def grep_on_gateway(needle, path):
    """Look for a value in a file on the gateway.

    grep exits 1 when it finds nothing, which is an answer; anything else is not. The previous
    version appended `|| true`, which made "found", "not found" and "could not look" identical.
    """
    query = server_query("grep -c -- " + needle + " " + path)
    if query["error_class"] or query["exit_code"] not in (0, 1):
        return None, query                       # could not look
    return query["exit_code"] == 0, query        # looked, and this is what was there


def sentinels():
    """Two random values that cross the network in opposite directions.

    Neither origin is asserted. The client's value is in the payload it sent, and the payload text
    is recorded so a reader can see it there. The gateway's value is generated inside the sandbox,
    on the gateway machine, and appears only in what came back -- the recorded payload does NOT
    contain it, which is what makes it the gateway's rather than something this harness says about
    itself.

    Each value travels over the AgentNode job channel and is then looked for over SSH, a separate
    network channel to the same host. Nothing is read from shared storage: the client never mounts
    the gateway's filesystem, and the only route between the two machines is the tunnel.
    """
    made_here = secretslib.token_hex(16)
    payload = WORK / "sentinel.py"
    source = (
        "import secrets\n"
        "print('E3-FROM-CLIENT " + made_here + "')\n"
        "print('E3-FROM-GATEWAY ' + secrets.token_hex(16))\n"
    )
    payload.write_text(source, encoding="utf-8")

    code, out, err, cls = launch([AN, "remote", "run", str(payload), "--max-seconds", "120"])
    match = RUN_ID.search(out)
    run_id = match.group(1) if match else ""
    record = gateway_record(run_id) if run_id else None

    from_gateway = ""
    found = re.search(r"E3-FROM-GATEWAY ([0-9a-f]{32})", out)
    if found:
        from_gateway = found.group(1)

    client_seen, client_query = grep_on_gateway(made_here, LOG)
    if from_gateway:
        gateway_seen, gateway_query = grep_on_gateway(from_gateway, LOG)
    else:
        gateway_seen, gateway_query = None, {"command": "(the job returned no value to look for)",
                                             "exit_code": None, "stdout": "", "stderr": "",
                                             "error_class": "NoValue"}

    rec.record(step(
        name="a value made on the client reaches the gateway",
        role="client", argv=evidence.redact_argv([AN, "remote", "run", "sentinel.py"]),
        started_at=time.time(), ended_at=time.time(), exit_code=code, stdout=out, stderr=err,
        expected_exit=0, expect_output=True, error_class=cls,
        run_id=run_id, gateway_record=record, **from_record(record),
        notes=("the payload the client sent is below. The client's value is IN it, which is what "
               "makes this origin checkable rather than asserted.\n" + source),
        sentinel={"generated_on": "client",
                  "carried_over": "agentnode-job", "confirmed_over": "ssh",
                  "value_sha256": digest(made_here),
                  "in_request": made_here in source,
                  "in_response": made_here in out,
                  "in_other_channel": client_seen is True,
                  "matched": bool(made_here in out and client_seen is True)},
        container_query=client_query))

    rec.record(step(
        name="a value made inside the sandbox on the gateway reaches the client",
        role="gateway",
        argv=evidence.redact_argv(ssh_argv(str(gateway_query.get("command", "")))),
        started_at=time.time(), ended_at=time.time(),
        exit_code=gateway_query.get("exit_code"),
        stdout=gateway_query.get("stdout", ""), stderr=gateway_query.get("stderr", ""),
        expected_exit=None, error_class=str(gateway_query.get("error_class", "")),
        run_id=run_id, gateway_record=record, **from_record(record),
        notes=("the payload recorded on the previous step does NOT contain this value: the job "
               "generated it, on the gateway, so the client cannot have produced it.\n"),
        sentinel={"generated_on": "gateway",
                  "carried_over": "agentnode-job", "confirmed_over": "ssh",
                  "value_sha256": digest(from_gateway),
                  "in_request": bool(from_gateway) and from_gateway in source,
                  "in_response": bool(from_gateway),
                  "in_other_channel": gateway_seen is True,
                  "matched": bool(from_gateway and gateway_seen is True)}))


# --------------------------------------------------------------------------- policy binding


def binding_now(label):
    """The operator-policy binding, read from the active authenticated snapshot."""
    show = server_query(f"sudo -u {GATEWAY_USER} {GW} gateway egress --dir {STATE} --verbose")
    text = show["stdout"]

    def field(pattern):
        found = re.search(pattern, text)
        return found.group(1).strip() if found else ""

    binding = {
        # The policy's own word for its mode, from the diagnostic block. Not derived from the
        # prose above it: a sentence can be reworded without the policy changing, and a rule that
        # read the sentence would then be reading the wording rather than the policy.
        "mode": field(r"network mode\s*:\s*(\w+)"),
        "generation": field(r"activation generation\s*:\s*(\d+)"),
        "policy_digest": field(r"policy digest\s*:\s*([0-9a-f]{64})"),
        "configured_digest": field(r"configured digest\s*:\s*([0-9a-f]{64})"),
        "digests_agree": field(r"digests agree\s*:\s*(\w+)"),
        "required_properties": field(r"measured properties\s*:\s*(.+)"),
        "allowlist": re.findall(r"^\s{4}([a-z0-9.-]+)\s*$", text, re.M),
    }
    runtime = server_query("docker version --format '{{.Server.Version}}'")
    binding["runtime"] = (runtime["stdout"].replace(MARKER, "").strip()
                          if runtime["parsed"] and runtime["complete"] else "")
    backend = server_query("docker info --format '{{.Name}}/{{.OSType}}'")
    binding["backend"] = (backend["stdout"].replace(MARKER, "").strip()
                          if backend["parsed"] and backend["complete"] else "")
    report = server_query("sha256sum " + STATE + "/conformance.json")
    binding["conformance_digest"] = (report["stdout"].split()[0]
                                     if report["parsed"] and report["stdout"].split() else "")
    # `EM3C-EVIDENCE-0004`: whether the remote answer arrived WHOLE was computed and then left
    # out of this decision, so a truncated response whose fields happened to parse counted as a
    # reading of the active snapshot. Every query that fed this binding has to have run, been
    # readable, and reached its end marker.
    queries = (show, runtime, backend, report)
    answered = all(q["parsed"] and q["complete"] for q in queries)
    complete = answered and all(bool(binding.get(k)) for k in
                                ("mode", "generation", "policy_digest", "configured_digest",
                                 "runtime", "backend", "conformance_digest")) \
        and binding["digests_agree"] == "True"
    observed(f"the operator-policy binding, {label}", ssh_argv("gateway egress --verbose"),
             complete, text or "no output", role="gateway", binding=binding,
             container_query=show)
    return binding


# --------------------------------------------------------------------------- the matrix


def from_record(record):
    """What a step has to carry when it attaches a gateway record.

    A rule compares the step's own policy digests with the record's, and a step that attached a
    record without them was reported as never having compared the two. Only `cli()` lifted them,
    so every other step that carried a record produced that finding -- which the offline suite
    caught the first time it ran the whole matrix.
    """
    if not isinstance(record, dict) or "error" in record:
        return {}
    return {"job_id": str(record.get("job_id") or ""),
            "request_policy_sha256": str(record.get("request_policy_sha256") or ""),
            "effective_policy_sha256": str(record.get("effective_policy_sha256") or ""),
            "policy_deltas": record.get("policy_deltas"),
            "cleanup_verified": record.get("cleanup_verified")}


def cli(name, args, *, expected_exit=0, expected_refusal="", expect_cleanup=False,
        want_container=False):
    argv = [AN, *args]
    started = time.time()
    code, out, err, error_class = launch(argv)
    match = RUN_ID.search(out)
    run_id = match.group(1) if match else ""
    record = gateway_record(run_id) if run_id else None
    fields = from_record(record)
    container = ""
    if want_container and run_id:
        # Through the same listing everything else uses. Its own inline version took the first
        # line of stdout, which is the end marker when nothing is there -- so a run with no
        # container recorded the marker as the container's name.
        _listing, names, _ids = list_containers(run_id)
        container = names[0] if names else ""
    return rec.record(step(
        name=name, role="client", argv=evidence.redact_argv(argv),
        started_at=started, ended_at=time.time(), exit_code=code, stdout=out, stderr=err,
        expected_exit=expected_exit, expected_refusal=expected_refusal,
        expect_cleanup=expect_cleanup, expect_output=True, error_class=error_class,
        run_id=run_id, gateway_record=record, container=container, **fields))


def list_containers(run_id):
    """What this run's containers are called and what their ids are, right now.

    Both, always. `EM3C-EVIDENCE-0004`: the absence rule requires the sought NAME and the sought
    ID to be gone, and the harness only ever listed names -- so it could never supply an id, and
    every absence conclusion it produced was destined to be an evidence error rather than a
    result. An id has to be learned while the container still exists.
    """
    listing = server_query(f'docker ps -a --filter "name=agentnode-em3c-{run_id[:16]}" '
                           '--format "{{.Names}} {{.ID}}"')
    names, ids = [], []
    if listing["parsed"]:
        for line in listing["stdout"].splitlines():
            if line.strip() == MARKER or not line.strip():
                continue
            parts = line.split()
            if parts and parts[0] != MARKER:
                names.append(parts[0])
                if len(parts) > 1:
                    ids.append(parts[1])
    return listing, names, ids


def container_gone(run_id, container, sought_id):
    """Absence, concluded only from a query that ran and was read."""
    listing, names, ids = list_containers(run_id)
    query = {**listing, "names": names, "ids": ids, "sought_id": sought_id}
    final_record = gateway_record(run_id)
    rec.record(step(
        name="the cancelled job left no container behind", role="gateway",
        argv=evidence.redact_argv(ssh_argv(listing["command"])),
        started_at=time.time(), ended_at=time.time(),
        exit_code=listing["exit_code"], expected_exit=0,
        stdout=listing["stdout"], stderr=listing["stderr"],
        error_class=listing["error_class"],
        run_id=run_id, container=container, container_query=query,
        expect_container_gone=True,
        gateway_record=final_record, **from_record(final_record)))


def main(path=None) -> int:
    global rec
    os.environ.setdefault("AGENTNODE_HOME", str(HOME))
    destination = Path(path) if path else EVIDENCE
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    secrets = live_secrets()
    # The notice is printed here, at the moment a recording starts, because that is when
    # the exposure arrives. EM3C-V8-DECISION-0001 held that a docstring and a page are
    # not enough.
    rec = evidence.Recorder(destination, role="client", secrets=secrets)
    print(f"  recording to {destination}")
    print(f"  live secrets that must never appear: {len(secrets)}")

    machines()
    # BEFORE anything runs. The sentinels are jobs, and a job recorded before any binding has
    # nothing in the record saying what the machine allowed while it ran -- which is an evidence
    # error, correctly, and was the order this driver used.
    before = binding_now("before the run")
    sentinels()

    code, out, err, cls = launch(["docker", "version"], timeout=60)
    observed("J: this client has no container runtime", ["docker", "version"], code != 0,
             f"exit={code} error_class={cls or 'none'}\n{(err or out)[:200]}")

    cli("A: a job asks for a host while the operator ceiling is closed",
        ["remote", "run", str(WORK / "egress.py"), "--allow", "example.com",
         "--max-seconds", "240"], want_container=True)

    server_step("E1: the operator proposes an allowlist, measured before it takes effect",
                f"sudo -u {GATEWAY_USER} {GW} gateway egress --dir {STATE} --allow example.com",
                timeout=900, expect_output=True)
    before_pid = server_query(f'pgrep -u {GATEWAY_USER} -f "gateway start" | head -1')
    server_step("E2: stop the gateway", f'pkill -u {GATEWAY_USER} -f "gateway start"', expected_exit=None)
    server_step("E3: start it again on the new policy",
                f"sudo -u {GATEWAY_USER} setsid nohup {GW} gateway start --dir {STATE} --port {PORT} "
                f">> {LOG} 2>&1 < /dev/null &", expected_exit=None)
    time.sleep(10)
    listening = server_query("ss -ltn")
    observed("E4: the gateway listens again, on loopback only",
             ssh_argv("ss -ltn"),
             listening["parsed"] and f"127.0.0.1:{PORT}" in listening["stdout"]
             and f"0.0.0.0:{PORT}" not in listening["stdout"],
             listening["stdout"][:400] or "nothing", role="gateway")
    after_pid = server_query(f'pgrep -u {GATEWAY_USER} -f "gateway start" | head -1')
    observed("E5: it is a different gateway process", ["(pgrep before and after)"],
             after_pid["parsed"] and before_pid["parsed"]
             and after_pid["stdout"].strip() != before_pid["stdout"].strip()
             and bool(after_pid["stdout"].strip()),
             f"before={before_pid['stdout'].strip() or 'none'} "
             f"after={after_pid['stdout'].strip() or 'none'}\n", role="gateway")
    after_change = binding_now("after the policy change")
    observed("E6: the policy change moved the generation and the digest",
             ["(compare the two bindings)"],
             bool(after_change["generation"]) and bool(before["generation"])
             and after_change["generation"] != before["generation"]
             and after_change["policy_digest"] != before["policy_digest"],
             f"generation {before['generation']} -> {after_change['generation']}\n",
             role="gateway", binding=after_change)

    cli("B: the allowed host is reachable and nothing else is",
        ["remote", "run", str(WORK / "egress.py"), "--allow", "example.com",
         "--max-seconds", "240"], want_container=True)
    cli("C: a job asking only for a host the operator never allowed",
        ["remote", "run", str(WORK / "offceiling.py"), "--allow", "www.google.com",
         "--max-seconds", "240"],
        expected_exit=1, expected_refusal="named no host it may reach")
    cli("D: a job asking for one allowed and one forbidden host",
        ["remote", "run", str(WORK / "offceiling.py"), "--allow", "example.com",
         "--allow", "www.google.com", "--max-seconds", "240"], want_container=True)

    cli("H1: a job that finishes normally",
        ["remote", "run", str(WORK / "whoami.py"), "--max-seconds", "120"],
        expect_cleanup=True, want_container=True)

    slow_argv = [AN, "remote", "run", str(WORK / "slow.py"), "--max-seconds", "900",
                 "--timeout", "300"]
    run_id, slow, launch_error = "", None, ""
    try:
        slow = subprocess.Popen(slow_argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
    except OSError as exc:
        launch_error = f"{type(exc).__name__}: {exc}"
    if slow is not None:
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                line = slow.stdout.readline()
            except Exception as exc:                               # noqa: BLE001
                launch_error = f"{type(exc).__name__}: {exc}"
                break
            if not line:
                break
            found = RUN_ID.search(line)
            if found:
                run_id = found.group(1)
                break
    observed("H2: a long job started and named its run", slow_argv, bool(run_id),
             f"run_id={run_id or 'none'} launch_error={launch_error or 'none'}\n")
    if run_id:
        # While it is still running: what it is called AND what its id is. The absence check
        # afterwards needs both, and neither can be recovered once the container is gone.
        listing, names, ids = list_containers(run_id)
        container = names[0] if names else ""
        sought_id = ids[0] if ids else ""
        running_record = gateway_record(run_id)
        observed("H2b: the running job has a container, with a name and an id",
                 ssh_argv(listing["command"]), bool(container) and bool(sought_id),
                 listing["stdout"][:400] or "nothing came back", role="gateway",
                 run_id=run_id, container=container,
                 container_query={**listing, "names": names, "ids": ids,
                                  "sought_id": sought_id},
                 gateway_record=running_record, **from_record(running_record))
        code, out, err, cls = launch([AN, "remote", "cancel", "--run", run_id])
        cancel_record = gateway_record(run_id)
        rec.record(step(
            name="H3: cancel stops the job", role="client",
            argv=evidence.redact_argv([AN, "remote", "cancel", "--run", run_id]),
            started_at=time.time(), ended_at=time.time(), exit_code=code,
            stdout=out, stderr=err, expected_exit=0, expect_output=True, error_class=cls,
            run_id=run_id, container=container, gateway_record=cancel_record,
            **from_record(cancel_record)))
        if slow is not None:
            try:
                slow.wait(timeout=300)
            except Exception:                                      # noqa: BLE001
                try:
                    slow.kill()
                except Exception:                                  # noqa: BLE001
                    pass
        container_gone(run_id, container, sought_id)

    cli("H4: a payload that ignores signals is still ended by the timeout",
        ["remote", "run", str(WORK / "stubborn.py"), "--max-seconds", "15",
         "--timeout", "240"], want_container=True)

    before_token = connection()[1].token
    code, out, err, cls = launch([AN, "remote", "rotate"])
    after_token = connection()[1].token
    rec.record(step(
        name="F1: rotating the credential", role="client",
        argv=evidence.redact_argv([AN, "remote", "rotate"]),
        started_at=time.time(), ended_at=time.time(), exit_code=code, stdout=out, stderr=err,
        expected_exit=0, expect_output=True, error_class=cls))
    observed("F2: the credential really changed", ["(compare the stored credential)"],
             bool(after_token) and before_token != after_token,
             "the stored credential differs from the one before the rotation\n"
             if before_token != after_token else "the stored credential did NOT change\n")
    cli("F3: a job still runs under the rotated credential",
        ["remote", "run", str(WORK / "whoami.py"), "--max-seconds", "120"])

    after = binding_now("after the run")
    observed("the binding is unchanged across the jobs that followed the policy change",
             ["(compare the two bindings)"],
             after["policy_digest"] == after_change["policy_digest"]
             and after["generation"] == after_change["generation"],
             f"generation {after_change['generation']} -> {after['generation']}\n",
             role="gateway", binding=after)

    problems = evidence.verify([s.as_dict() for s in rec.steps], secrets=secrets)
    print()
    print(f"  steps recorded: {len(rec.steps)}")
    if problems:
        failures = [p for p in problems if p.kind == evidence.FAIL]
        errors = [p for p in problems if p.kind == evidence.EVIDENCE_ERROR]
        print(f"  {len(failures)} failed, {len(errors)} could not be evaluated:")
        for problem in problems:
            print(f"    {problem}")
        return 1
    print("  every step is complete, self-consistent and about the run it names")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
