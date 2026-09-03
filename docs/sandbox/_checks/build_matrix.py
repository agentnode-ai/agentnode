#!/usr/bin/env python3
"""Generate the availability matrix from code facts plus recorded test evidence.

Five statuses, and nothing may claim a better one than its evidence supports:

    available-tested   the code path exists AND a named test or check exercised THIS row here
    available-untested the code path exists but nothing has run it on this platform
    experimental       the code exists and is reachable, but is declared inert or unfinished
    planned            it does not exist in the code at all
    removed            it existed once and was deliberately taken out; it cannot be selected

There is no sixth. An unknown status is refused rather than printed with a fallback marker.

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
TESTS = json.loads((HERE.parent / "_facts" / "test-evidence.json").read_text(encoding="utf-8"))
OUT = HERE.parent / "availability.md"

AVAILABLE_TESTED = "available, tested"
AVAILABLE_UNTESTED = "available, not tested here"
EXPERIMENTAL = "experimental"
PLANNED = "planned"
REMOVED = "removed"

MARK = {AVAILABLE_TESTED: "✅", AVAILABLE_UNTESTED: "🟡", EXPERIMENTAL: "⚗️",
        PLANNED: "🔭", REMOVED: "⛔"}

EVIDENCE = TESTS["evidence"]
# An assertion may support a tested claim only with one of these outcomes, and a human observation
# ("observed") can never carry a row on its own -- see _require_evidence.
GOOD = {"passed", "observed"}
MACHINE = {"pytest", "check"}
SELFTEST_CASES = 4


def _assertions(key: str) -> list:
    return EVIDENCE.get(key, {}).get("assertions", [])


def _refuse(msg: str) -> None:
    raise SystemExit("refusing to generate: " + msg)


# SANDBOX-DOCS-0002 / F-D2-EVIDENCE-NOT-ROW-SPECIFIC. The first version asked only whether a shared
# bucket held any run at all, so one unrelated Linux result marked every Linux row tested. Now a key
# belongs to exactly one row, and each of its assertions has to name what was executed and what was
# recorded for it. What a row may not claim, this function refuses to print.
def _require_evidence(name: str, status: str, key: str, claimed: dict) -> None:
    if status not in MARK:
        _refuse(f"{name!r} has status {status!r}, which is not one of the five declared statuses")
    asserts = _assertions(key)
    if status != AVAILABLE_TESTED:
        if asserts:
            _refuse(f"{name!r} is {status!r} but cites evidence key {key!r}, which records runs. "
                    "A row that is not tested must not cite execution evidence")
        return
    if key in claimed:
        _refuse(f"{name!r} claims {AVAILABLE_TESTED!r} on evidence key {key!r}, which already "
                f"belongs to {claimed[key]!r}. Shared evidence cannot make two rows tested")
    claimed[key] = name
    if not asserts:
        _refuse(f"{name!r} claims {AVAILABLE_TESTED!r} but evidence key {key!r} records no "
                "assertion in _facts/test-evidence.json")
    for a in asserts:
        what = a.get("node_id") or a.get("check") or "<unnamed>"
        if a.get("outcome") not in GOOD:
            _refuse(f"{name!r} claims {AVAILABLE_TESTED!r} but its evidence records "
                    f"{a.get('outcome')!r} for {what!r}")
        if not a.get("run_id") or not a.get("raw_record"):
            _refuse(f"{name!r} cites {what!r} without a run id and a raw record")
    if not any(a.get("kind") in MACHINE for a in asserts):
        _refuse(f"{name!r} claims {AVAILABLE_TESTED!r} on hand observation alone")


def _selftest() -> None:
    """Prove, on every run, that the three refusals above actually fire.

    A checker nobody checks is decoration. These feed _require_evidence the exact mistakes it
    exists to stop -- an absent record, a borrowed one, and one that records a skip -- and the
    generator stops if any of them gets through.
    """
    cases = [
        ("absent evidence", lambda: _require_evidence("x", AVAILABLE_TESTED, "none", {})),
        ("borrowed evidence", lambda: _require_evidence(
            "x", AVAILABLE_TESTED, "linux_mcp_container", {"linux_mcp_container": "someone else"})),
        ("an unknown status", lambda: _require_evidence("x", "mostly fine", "none", {})),
    ]
    caught = 0
    for label, run in cases:
        try:
            run()
        except SystemExit:
            caught += 1
            continue
        raise SystemExit(f"self-test failed: {label} was accepted")
    # and one that records a skip rather than a pass
    global EVIDENCE
    EVIDENCE = dict(EVIDENCE, _selftest={"summary": "", "assertions": [
        {"kind": "pytest", "node_id": "t::t", "outcome": "skipped", "run_id": "0",
         "raw_record": "none"}]})
    try:
        _require_evidence("x", AVAILABLE_TESTED, "_selftest", {})
    except SystemExit:
        pass
    else:
        raise SystemExit("self-test failed: a recorded skip was accepted as a pass")
    finally:
        EVIDENCE = TESTS["evidence"]
    caught += 1
    # A self-test that can be quietly emptied is not one. The count is written out here rather than
    # derived from the list, so deleting a case is itself a failure instead of a smaller self-test.
    if caught != SELFTEST_CASES:
        raise SystemExit(
            f"self-test failed: {caught} of {SELFTEST_CASES} cases actually ran. The self-test was "
            "weakened, skipped, or had a case removed")


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
     "container and the hardening flags asserted on the argv", "linux_local_sandbox"),
    ("Linux server", "local container sandbox", AVAILABLE_UNTESTED,
     "technically the same path as the Linux PC row — the CI runner IS a Linux server — but no "
     "headless multi-user server deployment has been exercised as such", "none"),
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
     "`installer.py` builds into a sealed volume and runs from it read-only",
     "linux_toolpack_container"),
    ("MCP servers run in a container", AVAILABLE_TESTED,
     "`runtimes/mcp_runner.py`; the release artefact smoke starts a real one and completes the "
     "initialize handshake", "linux_mcp_container"),
    ("Community agents run in a container", AVAILABLE_TESTED,
     f"`runtimes/agent_sandbox.py` with network={FACTS['execution_paths']['agent']['network']!r} "
     f"and a {FACTS['execution_paths']['agent']['mount']} mount", "linux_agent_container"),
    ("A community agent's own entrypoint on the host", REMOVED,
     f"`exceptions.py`: {FACTS['execution_paths']['host_agent_entrypoint']}", "code_only"),
    ("Refusal when no runtime exists", AVAILABLE_TESTED,
     "`lane-runtime-absent` removes docker, podman and the socket, verifies they are gone, and "
     "asserts the refusal; also observed by hand on Windows", "runtime_absent_refusal"),
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
     "code_only"),
    ("The EM-3 selection contract", PLANNED,
     "on this branch the module is "
     + ("present but imported by nothing" if FACTS["wired_in"]["em3_contract_module_exists"]
        else "not present on main; it is under review in pull request #115")
     + ", so it decides nothing yet", "none"),
]


def _cite(key):
    entry = EVIDENCE[key]
    asserts = entry["assertions"]
    if not asserts:
        return entry["summary"]
    named = [f"`{a.get('node_id') or a.get('check')}` → {a['outcome']} (run {a['run_id']})"
             for a in asserts]
    return entry["summary"] + ".<br>" + "<br>".join(named)


def rows(items, kind: str, claimed: dict) -> str:
    out = [f"| {kind} | status | why that status, in one line | evidence |",
           "|---|---|---|---|"]
    for item in items:
        name, *rest = item
        if kind == "Environment":
            _what, status, why, ev = rest
        else:
            status, why, ev = rest
        _require_evidence(name, status, ev, claimed)
        out.append(f"| **{name}** | {MARK[status]} {status} | {why} | {_cite(ev)} |")
    return chr(10).join(out)


def main() -> int:
    _selftest()
    claimed: dict = {}
    f = FACTS
    md = f"""# What actually works today

Generated by `_checks/build_matrix.py` from `_facts/code-facts.json`, which is read out of the SDK
source rather than written by hand. If the code changes and this file is not regenerated, the check
in `_checks/check_docs.py` fails.

**SDK version this describes: {f['sdk_version']}.**

Five statuses, and nothing here claims a better one than its evidence carries:

| | meaning |
|---|---|
| ✅ available, tested | the code path exists **and** a named test or check exercised **this row** |
| 🟡 available, not tested here | the code path exists; nobody has run it in that setting |
| ⚗️ experimental | the code exists and is reachable, but is declared unfinished in the source |
| 🔭 planned | it is not in the code at all |
| ⛔ removed | it existed once and was deliberately taken out; it cannot be selected |

There is no sixth status: a row whose status is not one of these five stops the generator instead of
being printed. A planned thing is never written as if you could use it today, and a removed thing is
never written as if it were coming back.

**What "tested" is allowed to mean here.** Every ✅ row names the test node ids or the named checks
that were recorded for *that row*, with the run they came from. The evidence for one row cannot be
reused by another, and a hand observation on its own is never enough. The per-test outcomes come
from the lane reports that the run uploaded, not from a summary anybody typed.

## Where AgentNode can run other people's code

{rows(ENVIRONMENTS, "Environment", claimed)}

## What the sandbox does and does not do yet

{rows(CAPABILITIES, "What it does", claimed)}

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
