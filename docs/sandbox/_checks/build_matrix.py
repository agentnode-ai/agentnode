#!/usr/bin/env python3
"""Generate the availability matrix from code facts plus recorded test evidence.

Four statuses, and nothing may claim a better one than its evidence supports:

    available-tested   the code path exists AND a named test or CI lane exercised it here
    available-untested the code path exists but nothing has run it on this platform
    experimental       the code exists and is reachable, but is declared inert or unfinished
    planned            it does not exist in the code at all

Every cell carries the fact key or evidence that produced it, so a reader can check a claim instead
of trusting it, and a future change to the code changes the matrix rather than quietly contradicting
it.

    python docs/sandbox/_checks/build_matrix.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
FACTS = json.loads((HERE.parent / "_facts" / "code-facts.json").read_text(encoding="utf-8"))
OUT = HERE.parent / "availability.md"

AVAILABLE_TESTED = "available, tested"
AVAILABLE_UNTESTED = "available, not tested here"
EXPERIMENTAL = "experimental"
PLANNED = "planned"

MARK = {AVAILABLE_TESTED: "✅", AVAILABLE_UNTESTED: "🟡", EXPERIMENTAL: "⚗️", PLANNED: "🔭"}

# What has actually been executed, and where. Nothing is marked tested without a named source.
EVIDENCE = {
    "linux_ci": "GitHub Actions ubuntu-24.04: the five execution lanes, including "
                "lane-runtime-present with Docker 28.0.4 and the pinned image pulled by digest, "
                "and the release artefact smoke that starts a real MCP container",
    "windows_dev": "the maintainer's Windows machine: the runtime-absent path only — the SDK "
                   "refuses and explains, because no container runtime is installed there",
    "none": "nothing has run this here",
}

ENVIRONMENTS = [
    ("Windows + Docker Desktop", "local container sandbox", AVAILABLE_UNTESTED,
     "the runtime probe looks for docker or podman on PATH and does not care which OS it is on "
     "(`sandbox_runtime.runtimes_probed`); no CI lane and no recorded manual run exercises it on "
     "Windows, so it is not claimed as tested", "none"),
    ("Windows + WSL2", "local container sandbox, via the runtime inside WSL2", AVAILABLE_UNTESTED,
     "same probe; whether the SDK runs inside the WSL2 distribution or on the Windows side changes "
     "which PATH is searched. Untested here either way", "none"),
    ("macOS", "local container sandbox", AVAILABLE_UNTESTED,
     "same probe; no macOS machine is in CI", "none"),
    ("Linux PC", "local container sandbox", AVAILABLE_TESTED,
     "exercised end to end in CI on ubuntu-24.04, including a real MCP server started in a "
     "container and the hardening flags asserted on the argv", "linux_ci"),
    ("Linux server", "local container sandbox", AVAILABLE_UNTESTED,
     "technically the same path as the Linux PC row — the CI runner IS a Linux server — but no "
     "headless multi-user server deployment has been exercised as such", "linux_ci"),
    ("Phone or tablet", "no local execution at all", PLANNED,
     "there is no mobile client and no remote backend in the code "
     "(`wired_in.remote_backend_exists` is false), so a phone has nowhere to send work to", "none"),
    ("Your own sandbox server", "self-hosted gateway", PLANNED,
     "no remote backend class exists (`wired_in.sandbox_backend_implementations` is "
     "ContainerBackend and NoSandboxBackend only)", "none"),
    ("AgentNode Sandbox (managed)", "managed service", PLANNED,
     "no managed backend, no service, no billing (`wired_in.managed_backend_exists` is false)",
     "none"),
]

CAPABILITIES = [
    ("Tool packs run in a container", AVAILABLE_TESTED,
     "`installer.py` builds into a sealed volume and runs from it read-only; the lanes cover it",
     "linux_ci"),
    ("MCP servers run in a container", AVAILABLE_TESTED,
     "`runtimes/mcp_runner.py`; the release artefact smoke starts a real one and completes the "
     "initialize handshake", "linux_ci"),
    ("Community agents run in a container", AVAILABLE_TESTED,
     f"`runtimes/agent_sandbox.py` with network={FACTS['execution_paths']['agent']['network']!r} "
     f"and a {FACTS['execution_paths']['agent']['mount']} mount", "linux_ci"),
    ("A community agent's own entrypoint on the host", "removed",
     f"`exceptions.py`: {FACTS['execution_paths']['host_agent_entrypoint']}", "linux_ci"),
    ("Refusal when no runtime exists", AVAILABLE_TESTED,
     "`lane-runtime-absent` removes docker, podman and the socket, verifies they are gone, and "
     "asserts the refusal; also observed by hand on Windows", "windows_dev"),
    ("Limiting which sites a sandboxed program may reach", EXPERIMENTAL,
     "the restricted-network mode builds the command line but never creates the network or the "
     "relay it needs — the source says so in as many words (`sandbox_runtime.egress_is_inert`)", "none"),
    ("Secrets passed to an MCP by name only", EXPERIMENTAL,
     "implemented, allowed only together with the restricted-network mode, and marked inert in the "
     "source with no live caller (`sandbox_runtime.secret_passthrough_is_inert`)", "none"),
    ("Credential broker with a sentinel value", PLANNED,
     "nothing in the code substitutes a credential at a proxy", "none"),
    ("Conformance suite for a backend", PLANNED,
     f"`wired_in.conformance_suite_exists` is {FACTS['wired_in']['conformance_suite_exists']}",
     "none"),
    ("The EM-3 selection contract", PLANNED,
     "on this branch the module is "
     + ("present but imported by nothing" if FACTS["wired_in"]["em3_contract_module_exists"]
        else "not present on main; it is under review in pull request #115")
     + ", so it decides nothing yet", "none"),
]


def rows(items, kind: str) -> str:
    out = [f"| {kind} | status | why that status, in one line | evidence |",
           "|---|---|---|---|"]
    for item in items:
        name, *rest = item
        if kind == "Environment":
            _what, status, why, ev = rest
        else:
            status, why, ev = rest
        mark = MARK.get(status, "🚫")
        out.append(f"| **{name}** | {mark} {status} | {why} | {EVIDENCE[ev]} |")
    return "\n".join(out)


def main() -> int:
    f = FACTS
    md = f"""# What actually works today

Generated by `_checks/build_matrix.py` from `_facts/code-facts.json`, which is read out of the SDK
source rather than written by hand. If the code changes and this file is not regenerated, the check
in `_checks/check_docs.py` fails.

**SDK version this describes: {f['sdk_version']}.**

Four statuses, and nothing here claims a better one than its evidence carries:

| | meaning |
|---|---|
| ✅ available, tested | the code path exists **and** something has run it in that setting |
| 🟡 available, not tested here | the code path exists; nobody has run it in that setting |
| ⚗️ experimental | the code exists and is reachable, but is declared unfinished in the source |
| 🔭 planned | it is not in the code at all |

A planned thing is never written as if you could use it today.

## Where AgentNode can run other people's code

{rows(ENVIRONMENTS, "Environment")}

## What the sandbox does and does not do yet

{rows(CAPABILITIES, "What it does")}

## The container it uses

The image is pinned by digest, so "the sandbox image" is one exact image and not a moving tag:

```
{f['sandbox_runtime']['pinned_image']}
```

It is started with `{'`, `'.join(f['sandbox_runtime']['hardening_flags'])}`.

## What is switched on when you install it

| setting | ships as | what else it accepts |
|---|---|---|
{chr(10).join(f"| `{k}` | `{v}` | {', '.join('`' + x + '`' for x in f['config']['allowed_values'].get(k, [])) or '—'} |" for k, v in sorted(f['config']['shipped_defaults'].items()))}

## Two honest limits

**Only Linux has been tested.** Everything above marked 🟡 is a reasonable expectation from reading
the code, not a result. The runtime probe does not branch on the operating system, which is why the
expectation is reasonable — and it is still not a test.

**Nothing here has been tried by people who did not build it.** The ten-person usability test is
prepared and has not been run. Until it has, no page in this documentation says the setup is easy;
it says what the steps are and lets you judge.
"""
    OUT.write_text(md, encoding="utf-8")
    print(f"  wrote {OUT.name}: {len(ENVIRONMENTS)} environments, {len(CAPABILITIES)} capabilities")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
