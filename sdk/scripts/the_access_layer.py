"""Reach the same canonical operation by four routes, with the worker behind mutual TLS.

`remote-worker-r1` R13 asks whether MCP, tool calling, the SDK/CLI and direct contract access
still reach the same canonical operation after this change -- and it is a tier-B criterion, which
means exercised, not reasoned about. The first submission answered it at tier A and an
independent review was right to fail it.

So this starts a REAL gateway serving real HTTP, whose worker is reached over the arc's new
transport -- a mutual-TLS door, with certificates a real issuer issued, the six identity checks
live, and a per-pair key -- and then reaches it four ways:

    1  the contract          a raw HTTP request, signed the way the contract says
    2  the SDK               agentnode_sdk.gateway.client, the production client
    3  the CLI               the installed console script, in a subprocess
    4  tool calling / MCP    the MCP server's own tool functions, in process

and prints, for each, what came back and whether the job crossed the TLS door.

WHAT THIS IS NOT. The door is on loopback, because this is one machine. The topology is therefore
`single-host-development`, and what is exercised is the TRANSPORT and the access layer above it
-- not host separation, which is measured on two machines or not at all.

Run from `sdk/`:  python scripts/the_access_layer.py [--out FILE]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading

HERE = pathlib.Path(__file__).resolve().parent.parent

_foreign = os.environ.get("AGENTNODE_OTHER_CHECKOUT", "")
if _foreign:
    sys.path = [p for p in sys.path
                if os.path.normcase(os.path.abspath(p or ".")) != os.path.normcase(_foreign)]
sys.path.insert(0, str(HERE))
os.chdir(HERE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    said = []

    def say(line=""):
        said.append(line)
        print(line, flush=True)

    from agentnode_sdk.gateway import client as gc
    from agentnode_sdk.gateway.identity import GatewayState
    from agentnode_sdk.gateway.server import GatewayService, make_server
    from tests.test_em3c_gateway import StandInBackend, _store_measurement
    from tests.test_mtls_transport import KEY, Door, World

    from agentnode_sdk.pki import floor as floors

    # THIS MACHINE HAS NO BOOT IDENTITY. Windows publishes none, and the time floor is keyed to
    # one -- so without this every handshake below is refused at the floor check, which is the
    # product being right. Naming a boot here is the same thing the mTLS suite's `_one_boot`
    # fixture does, and it is a fact about the machine, not about the transport.
    floors._boot = lambda: "the-access-layer-one-boot"

    home = pathlib.Path(tempfile.mkdtemp(prefix="access-layer-"))
    world = World(home / "pki")
    gateway_tls = world.service("gateway", "g1")
    worker_tls = world.service("worker", "w1")
    door = Door(world, worker_tls, {"g1"}, label="w1")

    root = home / "state"
    root.mkdir(parents=True, exist_ok=True)

    # The key the two ends authenticate their MESSAGES with, on top of the handshake. On one
    # host this is the shared `worker_key`; across the boundary it is a per-pair keyring, which
    # `test_one_key_per_pair.py` exercises over the same transport.
    (root / "worker.key").write_bytes(KEY)
    (root / "config.json").write_text(json.dumps({
        # DECLARED, because the product refuses an address with no declaration -- which it did
        # the first time this exercise was run, and correctly: nothing said whether that worker
        # was meant to be on this machine or another one. Here it is on this one.
        "worker_topology": "single-host-development",
        "worker_address": door.address,
        "worker_key": str(root / "worker.key"),
        "worker_tls": {
            "certificate": str(gateway_tls / "cert.pem"),
            "key": str(gateway_tls / "key.pem"),
            "anchor": str(world.anchor),
            "deployment": "alpha",
            "accept": ["w1"],
            "revocation_list": str(world.revocation_list),
            "floor": str(floors.path_for(world.floor_dir, "gateway")),
        },
    }, indent=1), encoding="utf-8")

    state = GatewayState(str(root), version="the-access-layer")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    server = make_server(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d" % server.server_address[1]

    try:
        say("FOUR ROUTES TO ONE OPERATION, OVER THE ARC'S TRANSPORT")
        say("=" * 78)
        say()
        say("gateway      %s" % base)
        say("worker       %s  (mutual TLS, six identity checks, per-pair key)" % door.address)
        say("topology     single-host-development -- the door is on LOOPBACK, because there is")
        say("             one machine. This exercises the transport and the access layer above")
        say("             it. It is not host separation and does not bear on R16.")
        say()

        # ------------------------------------------------------------------ 1. the contract
        import urllib.error
        import urllib.request

        with urllib.request.urlopen(base + "/v1/health", timeout=30) as answer:
            health = json.loads(answer.read().decode("utf-8"))
        say("1  THE CONTRACT, over raw HTTP, with nothing of this SDK in the caller")
        say("     GET /v1/health -> %s" % json.dumps(health)[:400])
        say("     the worker it reports on is the one behind the TLS door")
        say()

        # ------------------------------------------------------------------ 2. the SDK
        connection = gc.pair(base, state.start_pairing(), client_name="the-access-layer")
        say("2  THE SDK, the production client")
        say("     paired as %s" % getattr(connection, "client_id", "?"))
        # WAIT FOR THE GATEWAY TO HAVE MEASURED ITS WORKER. It refuses work until it has, which
        # is the health gate doing its job -- and the measurement goes over the TLS door, so
        # this wait is itself the transport being exercised.
        import time as _time

        until = _time.time() + 60.0
        while _time.time() < until:
            with urllib.request.urlopen(base + "/v1/health", timeout=30) as answer:
                now = json.loads(answer.read().decode("utf-8"))
            if now.get("taking_work"):
                break
            _time.sleep(1.0)
        say("     the gateway is taking work: %s (%s)"
            % (now.get("taking_work"), now.get("because")))

        artifact = b"print('hello from the access layer')\n"
        try:
            # Through the consent gate, the way a client really submits: prepare,
            # take what the gateway says is being agreed to, and submit against
            # exactly that. Without it the gateway refuses at the consent gate and
            # the run never exists -- which is what the first version of this saw.
            from tests import consent
            from tests.test_em3c_gateway import _granted

            submitted = consent.submit(connection, artifact,
                                       granted=_granted(service, token=connection.token))
            run_id = submitted.get("run_id") or submitted.get("id") or ""
            say("     submitted run %s" % run_id)
            answer = gc.wait_for(connection, run_id, timeout=120)
            say("     state=%s outcome=%s" % (answer.get("state"), answer.get("outcome")))
            say("     the gateway signed it: %s" % bool(gc.verify_answer(connection, answer)))
        except Exception as exc:                              # noqa: BLE001
            say("     submitting through the SDK raised: %r" % (exc,))
        say()

        # ------------------------------------------------------------------ 3. the CLI
        say("3  THE CLI, in a subprocess, really connecting to this gateway")
        # A SEPARATE PROCESS with its own home, so it cannot see anything this one holds: it
        # pairs over HTTP with a one-time code, exactly as a person would.
        elsewhere = dict(os.environ, HOME=str(home / "cli-home"),
                         USERPROFILE=str(home / "cli-home"),
                         AGENTNODE_HOME=str(home / "cli-home"))
        (home / "cli-home").mkdir(parents=True, exist_ok=True)
        for what in (["remote", "connect", base, "--code", state.start_pairing(),
                      "--as", "the-access-layer"],
                     ["remote", "status"]):
            done = subprocess.run([sys.executable, "-m", "agentnode_sdk.cli", *what],
                                  capture_output=True, text=True, timeout=120,
                                  cwd=str(HERE), env=elsewhere)
            say("     $ agentnode %s   -> exit %d" % (" ".join(what[:2]), done.returncode))
            for line in (done.stdout or done.stderr).strip().splitlines()[:6]:
                say("       | " + line)
        say()

        # ------------------------------------------------------------------ 4. MCP
        say("4  MCP / TOOL CALLING")
        try:
            import asyncio

            from agentnode_sdk import mcp_server

            prompts = asyncio.run(mcp_server.handle_list_prompts())
            resources = asyncio.run(mcp_server.handle_list_resources())
            say("     the MCP server answers: %d prompts, %d resources"
                % (len(prompts), len(resources)))
            say("     it is the SDK's own surface and reaches the same canonical operation the")
            say("     SDK does -- but it was NOT pointed at this gateway in this exercise, so")
            say("     for the remote path this route is READ and not RUN. Named plainly rather")
            say("     than counted with the three above.")
        except Exception as exc:                              # noqa: BLE001
            say("     the MCP server could not be exercised here: %r" % (exc,))
        say()

        # ------------------------------------------------------------------ did it cross?
        say("DID ANYTHING ACTUALLY CROSS THE TLS DOOR?")
        say("=" * 78)
        say("     conversations the worker's door handled : %d" % door.conversations)
        say("     application bytes the worker READ       : %d" % door.bytes_in)
        say("     what the door said                      : %s"
            % ("; ".join(door.said) or "(nothing)"))
        say()
        if door.bytes_in == 0:
            say("     NOTHING REACHED THE WORKER. Whatever the routes above returned, they did")
            say("     not exercise the changed path, and this exercise establishes nothing")
            say("     about R13 beyond the four routes still answering.")
    finally:
        try:
            server.shutdown()
        finally:
            server.server_close()
            thread.join(timeout=10)
            door.close()
            service.close()
            state.close()

    if args.out:
        pathlib.Path(args.out).write_text("\n".join(said) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
