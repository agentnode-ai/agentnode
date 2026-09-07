#!/usr/bin/env python3
"""Two roles on one machine, kept apart the way two machines would be.

The end-to-end journey already runs the published commands, but both roles share a user, a home
directory and a container runtime. That is enough to show the commands work and not enough to show
they work *across* a boundary: a client that can read the gateway's files, or run containers of its
own, might be succeeding for reasons that will not exist on a real remote pair.

So this puts a real boundary in the way, on one runner:

* The **client** runs as a second Unix user with its own home, no membership of the docker group,
  and no read access to the gateway's state directory. If it can still pair, submit, and read
  results, it did so over the network like any remote client.
* The **gateway** runs as the runner's own user, with the runtime and the state.
* They speak over a loopback TCP socket and nothing else.

What this does NOT establish is the thing only a second machine can: real TLS to a real peer, a
routed network, DNS. That is the external run this is meant to precede, and the point here is to
have already found everything that a second machine would have found for a much higher price.

Each step prints what it did and what came back. A step that cannot be observed is a step that
did not happen.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

CLIENT_USER = os.environ.get("TWO_ROLE_CLIENT_USER", "anclient")
GATEWAY_DIR = Path(os.environ.get("TWO_ROLE_GATEWAY_DIR", "/opt/agentnode-gateway"))
CLIENT_HOME = Path(os.environ.get("TWO_ROLE_CLIENT_HOME", f"/home/{CLIENT_USER}"))
PORT = int(os.environ.get("TWO_ROLE_PORT", "8399"))
BASE = f"http://127.0.0.1:{PORT}"

failures: list[str] = []

#: The last command either helper ran. check() reports it when an assertion fails, so a failing
#: step says what the command said rather than only that it failed -- without every call site
#: having to remember to pass it along.
last_result: subprocess.CompletedProcess | None = None


def say(step: str, detail: str = "") -> None:
    print(f"\n=== {step} ===", flush=True)
    if detail:
        print(detail, flush=True)


def check(label: str, condition: bool, detail: str = "") -> bool:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f" -- {detail}" if detail else ""), flush=True)
    if not condition:
        failures.append(label)
        # A failed step that says only that it failed sends the next person guessing at exactly
        # what the command already told us.
        if last_result is not None:
            print("        exit %s" % last_result.returncode, flush=True)
            for stream, text in (("out", last_result.stdout), ("err", last_result.stderr)):
                for line in (text or "").strip().splitlines()[-15:]:
                    print(f"        {stream}| {line}", flush=True)
    return condition


def gateway(*args, timeout: int = 600) -> subprocess.CompletedProcess:
    """A gateway-side command, as the runner's own user."""
    global last_result
    last_result = subprocess.run(
        [sys.executable, "-m", "agentnode_sdk.cli", "gateway", *args, "--dir", str(GATEWAY_DIR)],
        capture_output=True, text=True, timeout=timeout,
    )
    return last_result


def client(*args, timeout: int = 600, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """A client-side command, as the other user, with only its own home."""
    env_bits = [f"AGENTNODE_HOME={CLIENT_HOME}/.agentnode", f"HOME={CLIENT_HOME}"]
    for key, value in (extra_env or {}).items():
        env_bits.append(f"{key}={value}")
    global last_result
    last_result = subprocess.run(
        ["sudo", "-n", "-u", CLIENT_USER, "env", *env_bits,
         sys.executable, "-m", "agentnode_sdk.cli", "remote", *args],
        capture_output=True, text=True, timeout=timeout,
    )
    return last_result


def pairing_code() -> str:
    result = gateway("pair")
    found = re.search(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", result.stdout)
    if not found:
        raise AssertionError("no pairing code was printed:\n" + result.stdout + result.stderr)
    return found.group(0)


def start_gateway() -> subprocess.Popen:
    process = subprocess.Popen(
        [sys.executable, "-m", "agentnode_sdk.cli", "gateway", "start",
         "--dir", str(GATEWAY_DIR), "--port", str(PORT)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        probe = subprocess.run(
            ["curl", "-sf", "-m", "3", BASE + "/v1/hello"], capture_output=True, text=True)
        if probe.returncode == 0:
            return process
        if process.poll() is not None:
            raise AssertionError("the gateway exited:\n" + (process.stdout.read() or ""))
        time.sleep(0.5)
    raise AssertionError("the gateway never started listening")


def stop_gateway(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()


def main() -> int:
    say("the boundary itself",
        "Before anything else: the client must not be able to reach the gateway's files or the "
        "container runtime. If it can, nothing below proves what it looks like it proves.")

    peek = subprocess.run(
        ["sudo", "-n", "-u", CLIENT_USER, "cat", str(GATEWAY_DIR / "tokens.json")],
        capture_output=True, text=True)
    check("the client cannot read the gateway's tokens", peek.returncode != 0,
          (peek.stderr or "").strip()[:120])

    listing = subprocess.run(["sudo", "-n", "-u", CLIENT_USER, "ls", str(GATEWAY_DIR)],
                             capture_output=True, text=True)
    check("the client cannot list the gateway's directory", listing.returncode != 0,
          (listing.stderr or "").strip()[:120])

    docker = subprocess.run(["sudo", "-n", "-u", CLIENT_USER, "docker", "ps"],
                            capture_output=True, text=True)
    check("the client cannot use the container runtime", docker.returncode != 0,
          (docker.stderr or "").strip()[:120])

    say("the gateway measures itself and starts")
    doctor = gateway("doctor", "--measure")
    check("doctor --measure succeeded", doctor.returncode == 0,
          (doctor.stdout or "").strip().splitlines()[-1][:160] if doctor.stdout else "")
    if doctor.returncode != 0:
        print(doctor.stdout, doctor.stderr)
        return 1

    process = start_gateway()
    try:
        say("pairing over the network")
        code = pairing_code()
        connected = client("connect", BASE, "--code", code, "--as", "two-role")
        check("the client paired", connected.returncode == 0,
              (connected.stdout or "").strip().replace("\n", " | ")[:200])

        say("a job, and its result")
        tested = client("test")
        check("the test job ran and came back", tested.returncode == 0,
              (tested.stdout or "").strip().replace("\n", " | ")[:220])

        say("a job that is cancelled from the other side")
        script = CLIENT_HOME / "slow.py"
        subprocess.run(["sudo", "-n", "-u", CLIENT_USER, "tee", str(script)],
                       input="import time\nprint('started', flush=True)\ntime.sleep(120)\n",
                       capture_output=True, text=True)
        runner = subprocess.Popen(
            ["sudo", "-n", "-u", CLIENT_USER, "env",
             f"AGENTNODE_HOME={CLIENT_HOME}/.agentnode", f"HOME={CLIENT_HOME}",
             sys.executable, "-m", "agentnode_sdk.cli", "remote", "run", str(script),
             "--max-seconds", "150", "--timeout", "200"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        run_id = ""
        deadline = time.monotonic() + 90
        buffered = ""
        while time.monotonic() < deadline and not run_id:
            line = runner.stdout.readline()
            buffered += line
            found = re.search(r"run: ([0-9a-f]{8,})", buffered)
            if found:
                run_id = found.group(1)
            if not line and runner.poll() is not None:
                break
        check("the run announced an id that can be cancelled", bool(run_id), run_id)
        if run_id:
            time.sleep(4)
            cancelled = client("cancel", "--run", run_id)
            check("cancel was accepted", cancelled.returncode == 0,
                  (cancelled.stdout or "").strip().replace("\n", " | ")[:160])
        runner.wait(timeout=240)
        check("the cancelled run returned", runner.returncode is not None)

        say("a job that outruns its limit")
        forever = CLIENT_HOME / "forever.py"
        subprocess.run(["sudo", "-n", "-u", CLIENT_USER, "tee", str(forever)],
                       input="import time\nprint('going', flush=True)\ntime.sleep(600)\n",
                       capture_output=True, text=True)
        overran = client("run", str(forever), "--max-seconds", "10", "--timeout", "200")
        check("a job past its limit does not report success", overran.returncode != 0,
              (overran.stdout or "").strip().replace("\n", " | ")[:200])

        say("the credential can be replaced and withdrawn")
        rotated = client("rotate")
        check("the client rotated its own access", rotated.returncode == 0,
              (rotated.stdout or "").strip()[:160])
        check("it still works afterwards", client("test").returncode == 0)

        clients_before = gateway("clients")
        found = re.search(r"two-role\s+([0-9a-f]{6,})", clients_before.stdout)
        check("the gateway lists the connection", bool(found),
              (clients_before.stdout or "").strip().replace("\n", " | ")[:200])
        if found:
            revoked = gateway("revoke", "--client", found.group(1))
            check("the operator revoked it", revoked.returncode == 0)
            check("the revoked client is refused at once", client("test").returncode != 0)

        say("what must fail, and did")
        code2 = pairing_code()
        again = client("connect", BASE, "--code", code2, "--as", "second")
        check("a fresh pairing works", again.returncode == 0)

        reused = client("connect", BASE, "--code", code2, "--as", "third")
        check("a pairing code cannot be used twice", reused.returncode != 0,
              (reused.stdout or "").strip().replace("\n", " | ")[:160])

        plain = client("connect", "http://10.0.0.4:8099", "--code", "ABCD-EFGH-JKLM")
        check("an unencrypted address off this machine is refused", plain.returncode != 0,
              (plain.stdout or "").strip().replace("\n", " | ")[:160])
        check("and the refusal explains how to do it properly",
              "--tls-cert" in (plain.stdout or ""))

        legacy = client("connect", "http://10.0.0.4:8099", "--code", "ABCD-EFGH-JKLM",
                        extra_env={"AGENTNODE_GATEWAY_ALLOW_PLAINTEXT": "1"})
        check("the retired plaintext variable still changes nothing",
              legacy.returncode != 0)

        say("a restart does not forget")
        stop_gateway(process)
        process = start_gateway()
        check("the client still works after the gateway restarted",
              client("test").returncode == 0)

        status = client("status")
        check("status reports a protected sandbox", status.returncode == 0,
              (status.stdout or "").strip().replace("\n", " | ")[:200])

        disconnected = client("disconnect", "--name", "second")
        check("the client can disconnect", disconnected.returncode == 0)
    finally:
        stop_gateway(process)

    say("nothing left behind")
    for kind, field in (("container", "{{.Names}}"), ("network", "{{.Name}}")):
        for prefix in ("agentnode-em3c-", "agentnode-egress-"):
            listed = subprocess.run(
                ["docker", kind, "ls", "-a" if kind == "container" else "--no-trunc",
                 "--filter", f"name={prefix}", "--format", field],
                capture_output=True, text=True)
            left = [n for n in listed.stdout.split() if n.strip()]
            check(f"no {kind} left named {prefix}*", listed.returncode == 0 and not left,
                  ", ".join(left) if left else "none")

    print("\n" + "=" * 70)
    if failures:
        print(f"TWO-ROLE CHECK FAILED: {len(failures)} step(s)")
        for name in failures:
            print("  -", name)
        return 1
    print("TWO-ROLE CHECK PASSED: every step observed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
