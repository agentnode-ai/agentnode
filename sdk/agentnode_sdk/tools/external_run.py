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
import re
import secrets as secretslib
import subprocess
import sys
import time
from pathlib import Path

#: Where this run was told it is happening. Set once, by `configure`, from a file -- never from
#: the environment. `EM3C-E5-CLASSIFY-0001`: the fifth external run took these from exported
#: shell variables, and the MSYS layer rewrote the three remote ones between the shell and this
#: process. The runner could not have noticed: it checked its command line, and its command line
#: was fine.
SETTINGS = None

BASE = HOME = WORK = EVIDENCE = None
AN = KEY = SERVER = GW = STATE = LOG = GATEWAY_USER = PORT = ""


def configure(settings) -> None:
    """Take the settings this run was given. Nothing reads them before this has happened."""
    global SETTINGS, BASE, HOME, WORK, EVIDENCE, AN, KEY, SERVER, GW, STATE, LOG
    global GATEWAY_USER, PORT

    SETTINGS = settings
    HOME = Path(settings.client_home)
    BASE = HOME.parent
    WORK = Path(settings.work)
    EVIDENCE = Path(settings.evidence)
    AN = settings.agentnode
    KEY = settings.ssh_key
    SERVER = settings.server
    GW = settings.gateway_bin
    STATE = settings.gateway_state
    LOG = settings.gateway_log
    GATEWAY_USER = settings.gateway_user
    PORT = str(settings.gateway_port)


def configured() -> bool:
    return SETTINGS is not None


from agentnode_sdk.gateway import client as gc                     # noqa: E402
from agentnode_sdk.tools import external_config as config          # noqa: E402
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


#: Everything that crosses to the far machine, and everything that comes back, in this encoding.
#: Chosen here and applied by hand, because the alternative is whatever the platform would have
#: chosen -- and on Windows that includes rewriting every line ending on the way out.
WIRE = "utf-8"


def decode(raw) -> str:
    """Bytes from the far side, read as the encoding this end chose.

    `errors="replace"`, because a remote command may print anything and a record that stopped at
    the first undecodable byte would be a record of this function rather than of what happened.
    """
    if raw is None:
        return ""
    if isinstance(raw, str):                     # nothing asked for text, but be sure of it
        return raw
    return raw.decode(WIRE, "replace")


def launch(argv, timeout=600.0, script=None):
    """Run a command and always return (exit_code, stdout, stderr, error_class). Never raises.

    `script`, when given, is what the process reads on stdin -- which is how remote work travels,
    because an argument can be rewritten before the process starts and stdin cannot.

    NOTHING HERE IS TEXT. `EM3C-E4-CLASSIFY-0001`: this handed `subprocess` a string with
    `text=True`, and on Windows that writes through a wrapper which turns every LF into CRLF. The
    far side's shell received `$'hostname\r'` and said so, and the whole observation channel went
    with it. The script is encoded here, once, deliberately; the two streams come back as bytes
    and are decoded here, once, deliberately. No `text`, no `encoding`, no `universal_newlines`:
    each of those puts a translating wrapper on a stream that must carry what it was given.
    """
    payload = None if script is None else script.encode(WIRE)
    try:
        done = subprocess.run(list(argv), capture_output=True,
                              timeout=timeout, check=False, input=payload)
        return done.returncode, decode(done.stdout), decode(done.stderr), ""
    except FileNotFoundError as exc:
        return None, "", str(exc), "FileNotFoundError"
    except subprocess.TimeoutExpired as exc:
        return None, decode(exc.stdout), decode(exc.stderr), "TimeoutExpired"
    except OSError as exc:
        return None, "", str(exc), type(exc).__name__


#: A marker every remote answer ends with, so an empty answer and a truncated one are different.
MARKER = "E3-END-OF-LISTING"


def ssh_argv():
    """The command line, with NOTHING on it that a shell environment would rewrite.

    `EM3C-E3-CLASSIFY-0001`: the command used to be the last argument, and on Windows the MSYS
    layer rewrites an argument that looks like an absolute POSIX path before ssh.exe ever sees
    it. an absolute POSIX path left this machine rewritten as a Windows one, the far side
    was asked about a directory that does not exist there, and the step recorded a
    failure that was about the transport rather than about the gateway.

    So the far side is given no command at all: the login shell reads its work from stdin. The
    only remaining argument that could be rewritten is the key, which `check_argv` refuses if it
    is path-like, because a setting is where that belongs and a Windows path is what it must be.
    """
    return ["ssh", "-T", "-i", KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
            "-o", "StrictHostKeyChecking=yes", SERVER]


def path_like(argv) -> list[str]:
    """Which of these arguments have the shape a shell rewrites. Says nothing about where.

    Separate from `check_argv` on purpose: the SHAPE is the same everywhere and can be checked
    anywhere, while whether anything acts on it depends on the machine. Keeping them apart means
    the rule itself is testable on a Linux runner, where nothing would ever act on it.
    """
    return [a for a in argv if a.startswith("/") or a.startswith("\\\\")]


def check_argv(argv=None) -> list[str]:
    """Which arguments a shell environment could rewrite HERE. Empty means none of them.

    Only a Windows client has a layer that rewrites them. On a POSIX client an absolute POSIX
    path is simply what a path IS -- the key really does live at `/home/somebody/.ssh/something`
    and nothing is going to change it on the way to ssh -- so reporting one there would be
    reporting correctness, and a driver that refused to send anything on a Linux client is a
    driver that does not run on Linux. The platform is asked, and said, rather than assumed.
    """
    if os.name != "nt":
        return []
    return path_like(argv if argv is not None else ssh_argv())


def one_command(command: str) -> str:
    """A script that runs ONE command, keeps that command's own status, and says it finished.

    Not `cmd; echo MARKER`: the status of that is the echo's. The command's status is taken
    first, the marker is printed, and the script exits with the status that was taken -- so a
    reader gets both the end of the answer and the status of the thing that produced it.
    """
    return (command + chr(10)
            + "__status=$?" + chr(10)
            + "printf '%s" + chr(92) + "n' " + repr(MARKER).replace("'", '"') + chr(10)
            + "exit $__status" + chr(10))


def server_query(command, timeout=300.0) -> dict:
    """One remote command, as a structured result that never hides a failure.

    The previous harness returned "" for every failure, so a container listing that could not run
    was indistinguishable from one that found nothing. Everything a reader needs to tell those
    apart is kept.
    """
    rewritable = check_argv()
    if rewritable:
        return {"ran": False, "exit_code": None, "stdout": "", "stderr": "",
                "error_class": "ArgumentWouldBeRewritten", "parsed": False, "complete": False,
                "command": command}
    code, out, err, error_class = launch(ssh_argv(), timeout=timeout,
                                         script=one_command(command))
    return {"ran": code is not None or bool(error_class),
            "exit_code": code, "stdout": out, "stderr": err,
            "error_class": error_class, "parsed": code == 0 and not error_class,
            "complete": MARKER in out, "command": command}


def server_step(name, command, *, expected_exit=0, timeout=600.0, **fields):
    code, out, err, error_class = launch(ssh_argv(), timeout=timeout, script=one_command(command))
    return rec.record(step(
        name=name, role="gateway", argv=evidence.redact_argv(ssh_argv() + [command]),
        started_at=time.time(), ended_at=time.time(), exit_code=code,
        stdout=out, stderr=err, expected_exit=expected_exit, error_class=error_class, **fields))


#: What a step expects when it expects nothing in particular. Written out rather than left to
#: defaults: `EM3C-EVIDENCE-0004` found `observed()` stating one of the five, which the recorder
#: refuses -- so every step built through it would have raised on the first call, and the whole
#: matrix with it. Stating them here is a decision recorded once, not a default applied silently.
NOTHING_EXPECTED = {"expected_exit": None, "expected_refusal": "", "expect_output": False,
                    "expect_cleanup": False, "expect_container_gone": False,
                    "expect_timeout": False}


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
    """Ask the gateway about a run, and check the answer the way the product checks it.

    Returns `(answer, observed)`. `answer` is EXACTLY what came back, unmodified -- this driver
    never builds one. `observed` is what this client noticed about it, which is a separate thing
    and is recorded separately: the HTTP status, whether the production verifier accepted it, and
    why not when it did not.

    `EM3C-E3-CLASSIFY-0001`: this used to read the wire and hand back a body nobody had verified,
    and to invent `{"status": ..., "error": ...}` of its own when something went wrong -- an
    answer of the driver's own making, recorded in the place a gateway's answer goes.
    """
    asked = f"/v1/jobs/{run_id}" if run_id else "(no run id was printed)"
    if not run_id:
        return None, {"http_status": None, "verified": False, "asked_for": asked,
                      "refusal": "the client never printed a run id, so nothing was asked",
                      "verified_sha256": ""}
    # `gc.status_of` IS the way the product asks: it reads the answer, refuses one from a
    # gateway this connection did not pair with, refuses a status that is not 200, and returns
    # only what `verify_answer` accepted. Asking any other way would be this driver deciding
    # for itself what an acceptable answer is, which is the thing that went wrong before.
    try:
        conn, _saved = connection()
    except Exception as exc:                                       # noqa: BLE001
        return None, {"http_status": None, "verified": False, "asked_for": asked,
                      "refusal": f"{type(exc).__name__}: {exc}",
                      "verified_sha256": ""}
    status = None
    try:
        status, raw = gc._get(f"{conn.base_url}{asked}", token=conn.token)
    except Exception as exc:                                       # noqa: BLE001
        return None, {"http_status": None, "verified": False, "asked_for": asked,
                      "refusal": f"{type(exc).__name__}: {exc}",
                      "verified_sha256": ""}
    try:
        accepted = gc.status_of(conn, run_id)
    except Exception as exc:                                       # noqa: BLE001
        # What came back is still recorded, exactly as it came back. Whether it was acceptable
        # is the separate observation beside it.
        return (raw if isinstance(raw, dict) else None), {
            "http_status": status, "verified": False, "asked_for": asked,
            "refusal": f"{type(exc).__name__}: {exc}", "verified_sha256": ""}
    from agentnode_sdk.gateway.protocol import canonical_bytes, digest

    return accepted, {"http_status": status, "verified": True, "asked_for": asked, "refusal": "",
                      # The whole answer, as accepted, canonically. Anything that changes in the
                      # record afterwards -- including the signature -- stops matching this.
                      "verified_sha256": digest(canonical_bytes(accepted))}


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
             ssh_argv() + ["hostname; machine-id; uname; findmnt"], ok,
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
    asked = about(run_id)

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
        run_id=run_id, **asked,
        sentinel={"request_text": source,
                  "generated_on": "client",
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
        argv=evidence.redact_argv(ssh_argv() + [str(gateway_query.get("command", ""))]),
        started_at=time.time(), ended_at=time.time(),
        exit_code=gateway_query.get("exit_code"),
        stdout=gateway_query.get("stdout", ""), stderr=gateway_query.get("stderr", ""),
        expected_exit=None, error_class=str(gateway_query.get("error_class", "")),
        run_id=run_id, **asked,
        # The same payload, deliberately. This value is NOT in it -- the job made it on the
        # gateway -- and the rule reads that rather than taking the label's word for it.
        sentinel={"request_text": source,
                  "generated_on": "gateway",
                  "carried_over": "agentnode-job", "confirmed_over": "ssh",
                  "value_sha256": digest(from_gateway),
                  "in_request": bool(from_gateway) and from_gateway in source,
                  "in_response": bool(from_gateway),
                  "in_other_channel": gateway_seen is True,
                  "matched": bool(from_gateway and gateway_seen is True)}))


# --------------------------------------------------------------------------- policy binding


def binding_now(label):
    """The operator-policy binding, read from the active authenticated snapshot."""
    show = server_query(first_remote_command())
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
    observed(f"the operator-policy binding, {label}", ssh_argv() + ["gateway egress --verbose"],
             complete, text or "no output", role="gateway", binding=binding,
             container_query=show)
    return binding


# --------------------------------------------------------------------------- the matrix


def about(run_id):
    """The two fields every step that asks about a run carries: the answer, and what was noticed
    about it. One call, so no step can record one without the other."""
    answer, observed = gateway_record(run_id)
    return {"gateway_record": answer, "answer": observed, **from_record(answer)}


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
        want_container=False, expect_timeout=False):
    argv = [AN, *args]
    started = time.time()
    code, out, err, error_class = launch(argv)
    match = RUN_ID.search(out)
    run_id = match.group(1) if match else ""
    asked = about(run_id)
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
        expect_cleanup=expect_cleanup, expect_timeout=expect_timeout,
        expect_output=True, error_class=error_class,
        run_id=run_id, container=container, **asked))


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
    asked = about(run_id)
    rec.record(step(
        name="the cancelled job left no container behind", role="gateway",
        argv=evidence.redact_argv(ssh_argv() + [listing["command"]]),
        started_at=time.time(), ended_at=time.time(),
        exit_code=listing["exit_code"], expected_exit=0,
        stdout=listing["stdout"], stderr=listing["stderr"],
        error_class=listing["error_class"],
        run_id=run_id, container=container, container_query=query,
        expect_container_gone=True,
        **asked))


def first_remote_command() -> str:
    """The first thing this run asks the far machine, built from the settings as they arrived.

    Named and reachable on its own so the whole start can be rehearsed without a network: the
    three remote paths are in here, and if any of them was changed on the way it is visible in
    the bytes rather than in a failure five steps later.
    """
    return (f"sudo -u {GATEWAY_USER} {GW} gateway egress --dir {STATE} --verbose")


def check_start(argv=None) -> list[str]:
    """Everything that could have changed a remote value before this process existed.

    `EM3C-E5-CLASSIFY-0001`: `check_argv` looked at the ssh command line, and the ssh command line
    was fine. The values had been rewritten in the shell environment before this process started,
    so the one place that was inspected was the one place nothing had happened. Four places are
    inspected here, and the run stops before its first step if any of them says so.

    * the environment this process was started in, refused rather than ignored;
    * this process's own command line, which carries a local path and a digest and must carry no
      remote value at all -- if one is there, something outside the file decided it;
    * the three remote paths as they now sit in this module, checked again by the same rule the
      configuration was checked by, because between reading and using them is where a value that
      was fine on arrival could stop being fine;
    * the bytes of the command the far machine will actually be given.

    Returns what it found. Empty means nothing between the configuration and the wire touched it.
    """
    found: list[str] = []
    try:
        config.refuse_environment()
    except config.ConfigError as exc:
        found.append(str(exc))
    mine = list(sys.argv[1:] if argv is None else argv)
    for value in (GW, STATE, LOG):
        for arg in mine:
            if value and value in arg:
                found.append(
                    "a remote path is on this run's own command line (" + arg + "), and a value "
                    "that is on a command line is one something outside the configuration decided")
    for name, value in zip(config.REMOTE_PATHS, (GW, STATE, LOG)):
        try:
            config.check_remote_path(name, value)
        except config.ConfigError as exc:
            found.append(str(exc))
        if SETTINGS is not None and value != getattr(SETTINGS, name):
            found.append(
                name + " is no longer what the configuration said: " + repr(value) + " against "
                + repr(getattr(SETTINGS, name)))
    # Two texts, and they are not checked by the same rule. The COMMAND is built entirely out of
    # the settings, so nothing in it may be backslashed. The SCRIPT wraps that command in a few
    # lines of shell, and those lines carry a printf format with a backslash in it on purpose --
    # checking the wrapper by the command's rule would be refusing this driver's own punctuation.
    command = first_remote_command()
    if chr(92) in command:
        found.append("the command the far machine would be given contains a backslash: " + command)
    script = one_command(command)
    for text, what in ((command, "command"), (script, "script")):
        lowered = text.lower()
        for prefix in config._SHELL_PREFIXES:
            if prefix in lowered:
                found.append("the " + what + " the far machine would be given contains "
                             + repr(prefix) + ", which is a shell's own installation")
        drive = config._DRIVE_ANYWHERE.search(text)
        if drive:
            found.append("the " + what + " the far machine would be given names a drive on THIS "
                         "machine: " + drive.group(0))
    for arg in check_argv():
        found.append("an ssh argument a shell could rewrite: " + arg)
    return found


def preflight() -> int:
    """Say where this run believes it is happening, and stop. No network, no gateway, no file.

    This is the real entry point with the real configuration; what it prints is what the run
    would use. A rehearsal reads it, and so does a person who wants to know before starting.
    """
    print("  This run was told:")
    for line in config.describe(SETTINGS):
        print(line)
    script = one_command(first_remote_command())
    print("  the first remote command, as bytes it will send:")
    print("    " + script.encode(WIRE).hex())
    print("  and as text:")
    print("    " + first_remote_command())
    found = check_start()
    print("  arguments a shell could rewrite: " + (", ".join(found) if found else "none"))
    return 0 if not found else 2


def main(path=None) -> int:
    global rec
    if not configured():
        print("  This run has not been told where it is happening.")
        return 2
    found = check_start()
    if found:
        print("  This run will not start, and nothing was recorded:")
        for complaint in found:
            print("    " + complaint)
        return 2
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

    observed("the configuration this run was told to use",
             ["(the configuration file)"], True,
             chr(10).join(line.strip() for line in config.describe(SETTINGS)) + chr(10),
             role="client")
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
             ssh_argv() + ["ss -ltn"],
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
        # Bytes here too. The run id is read out of this stream, and a stream that translates
        # its line endings has already changed what it carries.
        slow = subprocess.Popen(slow_argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        launch_error = f"{type(exc).__name__}: {exc}"
    if slow is not None:
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                raw_line = slow.stdout.readline()
            except Exception as exc:                               # noqa: BLE001
                launch_error = f"{type(exc).__name__}: {exc}"
                break
            if not raw_line:
                break
            found = RUN_ID.search(decode(raw_line))
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
        running = about(run_id)
        observed("H2b: the running job has a container, with a name and an id",
                 ssh_argv() + [listing["command"]], bool(container) and bool(sought_id),
                 listing["stdout"][:400] or "nothing came back", role="gateway",
                 run_id=run_id, container=container,
                 container_query={**listing, "names": names, "ids": ids,
                                  "sought_id": sought_id},
                 **running)
        code, out, err, cls = launch([AN, "remote", "cancel", "--run", run_id])
        cancelled = about(run_id)
        rec.record(step(
            name="H3: cancel stops the job", role="client",
            argv=evidence.redact_argv([AN, "remote", "cancel", "--run", run_id]),
            started_at=time.time(), ended_at=time.time(), exit_code=code,
            stdout=out, stderr=err, expected_exit=0, expect_output=True, error_class=cls,
            run_id=run_id, container=container, **cancelled))
        if slow is not None:
            try:
                slow.wait(timeout=300)
            except Exception:                                      # noqa: BLE001
                try:
                    slow.kill()
                except Exception:                                  # noqa: BLE001
                    pass
        container_gone(run_id, container, sought_id)

    # The status the CLI documents for a run its limit ended, and the REASON the gateway
    # recorded -- which is what the rule reads. `EM3C-E4-CLASSIFY-0001`: this expected 0, got
    # -1 through a Windows process boundary as 4294967295, and no number could have been right.
    from agentnode_sdk.gateway.protocol import TIMEOUT_EXIT_STATUS

    cli("H4: a payload that ignores signals is still ended by the timeout",
        ["remote", "run", str(WORK / "stubborn.py"), "--max-seconds", "15",
         "--timeout", "240"], want_container=True, expected_exit=TIMEOUT_EXIT_STATUS,
        expect_timeout=True, expect_cleanup=True)

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


def run(argv=None) -> int:
    """The entry point. A local path to a configuration, and a digest of it. Nothing else.

    Started by the native Windows interpreter, so nothing stands between what the configuration
    says and what this process reads. A local path may be converted on the way here and still
    name the same file; a hex digest cannot be converted at all. The values that must not change
    are inside the file, where nothing on the way has an opinion about them.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="agentnode-external-run", add_help=True)
    parser.add_argument("--config", required=True,
                        help="local path to the configuration file for this run")
    parser.add_argument("--expect", default="",
                        help="the digest the launcher computed for that file")
    parser.add_argument("--preflight", action="store_true",
                        help="say where this run believes it is happening, and stop")
    parser.add_argument("--evidence", default="",
                        help="where to write the record, overriding the configuration")
    args = parser.parse_args(argv)
    try:
        configure(config.load(args.config, args.expect))
    except config.ConfigError as exc:
        print("  This run will not start:")
        print("    " + str(exc))
        print("  Nothing has run and nothing was recorded.")
        return 2
    if args.preflight:
        return preflight()
    return main(args.evidence or None)


if __name__ == "__main__":
    raise SystemExit(run())
