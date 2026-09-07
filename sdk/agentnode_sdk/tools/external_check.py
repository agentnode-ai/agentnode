#!/usr/bin/env python3
"""The external validation run, as one command per machine.

Everything in CI so far runs on a single host: one runner, one kernel, loopback, and two Unix users
standing in for two machines. That found what it could find. What it cannot reach is the part that
only exists between two real computers -- an encrypted link to a peer that had to be authenticated,
a network that can drop packets, a client on an operating system the gateway has never seen.

This is that run, and it is deliberately one command on each side so that a person doing it is not
also debugging a test harness.

    # on the Linux machine with Docker
    python -m agentnode_sdk.tools.external_check --role gateway

    # on the Windows laptop, with the address the gateway printed
    python -m agentnode_sdk.tools.external_check --role client --gateway https://...

The gateway side prepares and then waits, printing a pairing code. The client side runs the whole
journey against it and prints a result per step. Neither side reaches into the other: the client
uses only the published commands and the network.

Steps that cannot be checked automatically -- did the operator really type the code from a screen,
is this really a second machine -- are printed as things for the person to confirm, not silently
assumed. A step nobody observed is a step that did not happen.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

results: list[tuple[str, bool, str]] = []


def step(label: str, ok: bool, detail: str = "") -> bool:
    results.append((label, ok, detail))
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""),
          flush=True)
    return ok


def run(*argv, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "agentnode_sdk.cli", *argv],
                          capture_output=True, text=True, timeout=timeout)


def heading(text: str) -> None:
    print(f"\n=== {text} ===", flush=True)


# --------------------------------------------------------------------------- the gateway machine


def gateway_role(args) -> int:
    heading("what this machine is")
    import platform

    print(f"  {platform.platform()}")
    print(f"  python {platform.python_version()}")
    version = run("--version")
    print(f"  agentnode {(version.stdout or version.stderr).strip()}")

    heading("the runtime")
    doctor = run("gateway", "doctor")
    step("a container runtime is available", doctor.returncode == 0,
         (doctor.stdout or "").strip().splitlines()[-1][:120] if doctor.stdout else "")

    heading("measuring what it enforces (this takes a minute or two)")
    measured = run("gateway", "doctor", "--measure")
    print((measured.stdout or "")[-1500:])
    if not step("the gateway measured itself", measured.returncode == 0):
        print("\nThe gateway will refuse every job until this passes. Stop here and fix it.")
        return 1

    heading("how the client will reach this machine")
    print("  This run is only meaningful over an encrypted link to another computer.")
    print("  If you have not set one up yet, `agentnode gateway doctor` printed the command.")
    print("  Start the gateway in another terminal:")
    print("      agentnode gateway start")
    print("  and, if you are using a tunnel:")
    print("      tailscale serve --bg 8099")

    heading("the pairing code")
    paired = run("gateway", "pair")
    print(paired.stdout)
    found = re.search(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", paired.stdout or "")
    if not step("a pairing code was issued", bool(found)):
        return 1

    print("\n  Type that code on the other machine. Do not paste it into a chat.")
    print("  Then run there:")
    print(f"      python -m agentnode_sdk.tools.external_check --role client "
          f"--gateway <the https address> --code {found.group(0)}")
    print("\n  Confirm by hand, because no script can:")
    print("    [ ] the client is a different physical or virtual machine")
    print("    [ ] the address the client uses is https:// or a tunnel address")
    print("    [ ] the code was read from this screen, not copied through a shared file")
    return 0


# --------------------------------------------------------------------------- the client machine


def client_role(args) -> int:
    heading("what this machine is")
    import platform

    print(f"  {platform.platform()}")
    print(f"  python {platform.python_version()}")

    url = args.gateway.rstrip("/")
    step("the gateway address is not plain http to another machine",
         url.startswith("https://") or "127.0.0.1" in url or "localhost" in url,
         url)

    heading("connecting")
    connected = run("remote", "connect", url, "--code", args.code, "--as", "external")
    print((connected.stdout or "")[-900:])
    if not step("paired with the gateway", connected.returncode == 0):
        return _summary()

    heading("a job, end to end")
    tested = run("remote", "test")
    print((tested.stdout or "")[-900:])
    step("the sandbox ran a job and returned its output", tested.returncode == 0)

    heading("a job that is not allowed to reach the network")
    offline = Path("external-offline.py")
    offline.write_text(
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 80), timeout=8).close()\n"
        "    print('REACHED')\n"
        "except Exception as e:\n"
        "    print('NO ROUTE', type(e).__name__)\n",
        encoding="utf-8")
    out = run("remote", "run", str(offline))
    print((out.stdout or "")[-600:])
    blocked = "NO ROUTE" in (out.stdout or "")
    # A sandbox that is simply broken also fails to reach anything, so being blocked is only
    # meaningful next to something that succeeds. The allowed-host step below is that control; if
    # it fails too, this result says nothing and is reported as such rather than as a pass.
    step("a job with no network reported no route", blocked,
         "meaningful only if the allowed-host step below succeeds")

    heading("a job allowed to reach exactly one host")
    limited = Path("external-limited.py")
    limited.write_text(
        "import json, urllib.error, urllib.request\n"
        "seen = {}\n"
        "for name, url in (('allowed', 'https://example.com'),\n"
        "                  ('denied', 'https://www.google.com')):\n"
        "    try:\n"
        "        with urllib.request.urlopen(url, timeout=25) as r:\n"
        "            seen[name] = r.status\n"
        "    except urllib.error.HTTPError as e:\n"
        "        seen[name] = 'REFUSED:' + str(e.code)\n"
        "    except Exception as e:\n"
        "        seen[name] = 'UNREACHABLE:' + type(e).__name__\n"
        "print('EGRESS ' + json.dumps(seen))\n",
        encoding="utf-8")
    egress = run("remote", "run", str(limited), "--allow", "example.com",
                 "--max-seconds", "180")
    print((egress.stdout or "")[-600:])
    line = next((ln for ln in (egress.stdout or "").splitlines() if "EGRESS " in ln), "")
    if step("the restricted-network job reported a result", bool(line), line.strip()):
        seen = json.loads(line.split("EGRESS ", 1)[1])
        # This is the control for the no-network step above as well: if an allowed host is
        # reachable, then the earlier "no route" was policy rather than a sandbox that cannot
        # reach anything at all.
        step("the allowed host was reachable", seen.get("allowed") == 200, str(seen.get("allowed")))
        # A refusal the proxy ANSWERED with is policy. "Could not reach it" is also what
        # selective DNS failure, a routing problem or a dead host look like, and accepting that
        # would let the step pass without establishing anything -- EM3C-EXTERNAL-0002 found
        # exactly that. UNREACHABLE is reported as inconclusive rather than as a pass.
        denied = str(seen.get("denied"))
        step("a host that was not allowed was refused by the proxy",
             denied.startswith("REFUSED"),
             denied + (" -- inconclusive: this is also what a network fault looks like"
                       if denied.startswith("UNREACHABLE") else ""))

    heading("stopping a job from here")
    slow = Path("external-slow.py")
    slow.write_text("import time\nprint('started', flush=True)\ntime.sleep(120)\n",
                    encoding="utf-8")
    started = subprocess.Popen(
        [sys.executable, "-u", "-m", "agentnode_sdk.cli", "remote", "run", str(slow),
         "--max-seconds", "150", "--timeout", "200"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    run_id, buffered, deadline = "", "", time.monotonic() + 90
    while time.monotonic() < deadline and not run_id:
        line = started.stdout.readline()
        buffered += line
        found = re.search(r"run: ([0-9a-f]{8,})", buffered)
        if found:
            run_id = found.group(1)
        if not line and started.poll() is not None:
            break
    if step("the run announced an id", bool(run_id), run_id):
        time.sleep(4)
        cancelled = run("remote", "cancel", "--run", run_id)
        step("cancelling it was accepted", cancelled.returncode == 0)
    tail = (started.stdout.read() or "") if started.stdout else ""
    started.wait(timeout=300)
    whole = buffered + tail
    # This used to be step(..., True), which is not a check: it recorded a pass whatever happened.
    step("the cancelled run came back, and said so",
         started.returncode is not None and (
             "cancel" in whole.lower() or "did not finish" in whole.lower()),
         (whole.strip().splitlines() or ["(no output)"])[-1][:120])

    heading("a job that outruns its limit")
    forever = Path("external-forever.py")
    forever.write_text("import time\nprint('going', flush=True)\ntime.sleep(600)\n",
                       encoding="utf-8")
    overran = run("remote", "run", str(forever), "--max-seconds", "10", "--timeout", "200")
    step("a job past its limit does not report success", overran.returncode != 0)

    heading("replacing and withdrawing access")
    rotated = run("remote", "rotate")
    step("access was replaced", rotated.returncode == 0)
    step("and still works", run("remote", "test").returncode == 0)

    heading("what must fail")
    plain = run("remote", "connect", "http://198.51.100.9:8099", "--code", "ABCD-EFGH-JKLM")
    step("plain http to another machine is refused", plain.returncode != 0)
    step("and the refusal names a way through",
         "tailscale" in (plain.stdout or "") or "--tls-cert" in (plain.stdout or ""))

    reused = run("remote", "connect", url, "--code", args.code, "--as", "again")
    step("the pairing code cannot be used twice", reused.returncode != 0)

    print("\n  Confirm by hand, because no script can:")
    print("    [ ] ask the operator to run `agentnode gateway revoke --client <id>`,")
    print("        then `agentnode remote test` here must fail")
    print("    [ ] ask the operator to stop and restart the gateway,")
    print("        then `agentnode remote test` here must work again")
    print("    [ ] ask the operator for `docker ps -a` -- nothing named agentnode-* may remain")
    return _summary()


def _summary() -> int:
    failed = [name for name, ok, _ in results if not ok]
    print("\n" + "=" * 70)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    print("=" * 70)
    if failed:
        print(f"EXTERNAL CHECK FAILED: {len(failed)} step(s)")
        return 1
    print("EXTERNAL CHECK: every automated step passed")
    print("The hand-confirmed items above are not covered by that sentence.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="EM-3C external validation")
    parser.add_argument("--role", required=True, choices=("gateway", "client"))
    parser.add_argument("--gateway", default="", help="the gateway's address, for the client role")
    parser.add_argument("--code", default="", help="the pairing code, for the client role")
    args = parser.parse_args(argv)

    if args.role == "gateway":
        return gateway_role(args)
    if not args.gateway or not args.code:
        parser.error("the client role needs --gateway and --code")
    return client_role(args)


if __name__ == "__main__":
    sys.exit(main())
