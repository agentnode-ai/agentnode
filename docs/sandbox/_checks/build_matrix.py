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
SELFTEST_CASES = 8


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


# SANDBOX-DOCS-0007 / F-D2-NONTESTED-STATUS-NOT-DERIVED. Execution evidence carried the tested
# rows, but every other status was a constant somebody typed. A row could be downgraded to hide a
# capability, or a planned row could survive after the thing was built, and nothing would object.
# Each row now names a fact, and the status has to agree with what that fact says:
#
#   exists   the fact is truthy   -> the path is in the code (tested / untested / experimental)
#   absent   the fact is falsey   -> nothing in the code does this (planned)
#   removed  the fact says so     -> it was taken out, and the source records the refusal


def _fact(path: str):
    node = FACTS
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            _refuse(f"no such code fact: {path!r}")
        node = node[part]
    return node


def _require_fact(name: str, status: str, fact_path: str) -> None:
    value = _fact(fact_path)
    present = bool(value)
    if status == REMOVED:
        if not (isinstance(value, str) and "refus" in value.lower()):
            _refuse(f"{name!r} is {REMOVED!r}, but {fact_path} is {value!r} and does not record a "
                    "refusal. A removed thing has to be removed in the source")
        return
    if status == PLANNED and present:
        _refuse(f"{name!r} is {PLANNED!r}, but {fact_path} is {value!r}: the code has it. A planned "
                "row that exists is the mistake this check is for")
    if status in (AVAILABLE_TESTED, AVAILABLE_UNTESTED, EXPERIMENTAL) and not present:
        _refuse(f"{name!r} claims {status!r}, but {fact_path} is {value!r}: nothing in the code "
                "carries it")


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
        ("a planned row the code has", lambda: _require_fact(
            "x", PLANNED, "sandbox_runtime.egress_live_callers")),
        ("an available row the code lacks", lambda: _require_fact(
            "x", AVAILABLE_UNTESTED, "sandbox_runtime.credential_broker_exists")),
        ("a removed row with nothing removed", lambda: _require_fact(
            "x", REMOVED, "sandbox_runtime.runtimes_probed")),
        ("a fact that does not exist", lambda: _require_fact(
            "x", PLANNED, "sandbox_runtime.no_such_fact")),
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
     "Windows, so it is not claimed as tested", "none", "sandbox_runtime.runtimes_probed"),
    ("Windows + WSL2", "local container sandbox, via the runtime inside WSL2", AVAILABLE_UNTESTED,
     "same probe; whether the SDK runs inside the WSL2 distribution or on the Windows side changes "
     "which PATH is searched. Untested here either way", "none", "sandbox_runtime.runtimes_probed"),
    ("macOS", "local container sandbox", AVAILABLE_UNTESTED,
     "same probe; no macOS machine is in CI", "none", "sandbox_runtime.runtimes_probed"),
    ("Linux PC", "local container sandbox", AVAILABLE_TESTED,
     "exercised end to end in CI on ubuntu-24.04, including a real MCP server started in a "
     "container and the hardening flags asserted on the argv", "linux_local_sandbox", "sandbox_runtime.runtimes_probed"),
    ("Linux server", "local container sandbox", AVAILABLE_UNTESTED,
     "technically the same path as the Linux PC row — the CI runner IS a Linux server — but no "
     "headless multi-user server deployment has been exercised as such", "none", "sandbox_runtime.runtimes_probed"),
    ("Phone or tablet", "no local execution at all", PLANNED,
     "there is no mobile client and no remote backend in the code "
     "(`wired_in.remote_backend_exists` is false), so a phone has nowhere to send work to", "none", "wired_in.remote_backend_exists"),
    ("Your own sandbox server", "self-hosted gateway", PLANNED,
     "no remote backend class exists (`wired_in.sandbox_backend_implementations` is "
     "ContainerBackend and NoSandboxBackend only)", "none", "wired_in.remote_backend_exists"),
    ("AgentNode Sandbox (managed)", "managed service", PLANNED,
     "no managed backend, no service, no billing (`wired_in.managed_backend_exists` is false)",
     "none", "wired_in.managed_backend_exists"),
]

CAPABILITIES = [
    ("Tool packs run in a container", AVAILABLE_TESTED,
     "`installer.py` builds into a sealed volume and runs from it read-only",
     "linux_toolpack_container", "execution_paths.build.network"),
    ("MCP servers run in a container", AVAILABLE_TESTED,
     "`runtimes/mcp_runner.py`; the release artefact smoke starts a real one and completes the "
     "initialize handshake", "linux_mcp_container", "execution_paths.mcp.networks_used"),
    ("Community agents run in a container", AVAILABLE_TESTED,
     f"`runtimes/agent_sandbox.py` with network={FACTS['execution_paths']['agent']['network']!r} "
     f"and a {FACTS['execution_paths']['agent']['mount']} mount", "linux_agent_container", "execution_paths.agent.network"),
    ("A community agent's own entrypoint on the host", REMOVED,
     f"`exceptions.py`: {FACTS['execution_paths']['host_agent_entrypoint']}", "code_only", "execution_paths.host_agent_entrypoint"),
    ("Refusal when no runtime exists", AVAILABLE_TESTED,
     "`lane-runtime-absent` removes docker, podman and the socket, verifies they are gone, and "
     "asserts the refusal; also observed by hand on Windows", "runtime_absent_refusal", "sandbox_runtime.refuses_without_runtime"),
    ("A sandboxed program reaching only the sites its install sealed", AVAILABLE_TESTED,
     "the `egress` mode joins an internal network with no route out except a proxy that allows "
     "exactly the sealed names; two run paths call it "
     f"({', '.join('`' + c + '`' for c in FACTS['sandbox_runtime']['egress_live_callers'])}), and "
     "the end-to-end test runs the bypass matrix from inside the container. Reached only where an "
     "install sealed an allowlist and consent was recorded — everything else is refused, not opened",
     "linux_egress_topology", "sandbox_runtime.egress_live_callers"),
    ("Secrets reaching an MCP or tool pack by name only", AVAILABLE_UNTESTED,
     "the same two run paths pass consented names rather than values "
     f"({', '.join('`' + c + '`' for c in FACTS['sandbox_runtime']['secret_passthrough_live_callers'])}), "
     "behind the refusals listed in `sandbox_runtime.credentialed_run_refusals`. **The value still "
     "reaches the container** — name-only keeps it off the command line and out of the logs, not "
     "away from the program; no end-to-end run of this path is recorded here",
     "none", "sandbox_runtime.secret_passthrough_live_callers"),
    ("Credential broker with a sentinel value", PLANNED,
     "nothing in the code substitutes a credential at a proxy", "none", "sandbox_runtime.credential_broker_exists"),
    ("Conformance suite for a backend", PLANNED,
     f"`wired_in.conformance_suite_exists` is {FACTS['wired_in']['conformance_suite_exists']}",
     "code_only", "wired_in.conformance_suite_exists"),
    ("The EM-3 selection contract", PLANNED,
     "on this branch the module is "
     + ("present but imported by nothing" if FACTS["wired_in"]["em3_contract_module_exists"]
        else "not present on main; it is under review in pull request #115")
     + ", so it decides nothing yet", "none", "wired_in.em3_contract_importers"),
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
            _what, status, why, ev, fact = rest
        else:
            status, why, ev, fact = rest
        _require_evidence(name, status, ev, claimed)
        _require_fact(name, status, fact)
        out.append(f"| **{name}** | {MARK[status]} {status} | {why} | {_cite(ev)}<br>"
                   f"*status derived from* `{fact}` |")
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

**And every other status is derived too.** Each row names the code fact its status rests on, shown
in the last column. A row calling something planned while the fact shows the code has it, or calling
something available while the fact shows it absent, stops the generator. So a row cannot be quietly
downgraded to hide what the software does, and a planned row cannot survive the day the thing is
built.

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

## What each network mode actually does

The mode names are not self-explanatory, so here is what each one puts on the command line:

| mode | what it emits |
|---|---|
{chr(10).join(f"| `{k}` | {v} |" for k, v in sorted(f['sandbox_runtime']['network_mode_flags'].items()))}

Two of those deserve a sentence. **`restricted` restricts nothing today** — it emits an ordinary
bridge network, and no run path selects it. The mode that does restrict is `egress`, which has no
usable form without a real internal network and proxy: without them it raises instead of emitting an
open network.

## The memory ceiling

`--memory` and `--memory-swap` are set to the **same** value, so the total of memory and swap is the
limit (`sandbox_runtime.memory_ceiling_includes_swap` is
{f['sandbox_runtime']['memory_ceiling_includes_swap']}, derived by reading both out of the flag
list).

That matters because `--memory` on its own did not bind here: with only it set, the conformance
suite watched a 768 MiB allocation finish with exit code 0 under a declared 512 MiB limit. With both
set, the same allocation is stopped. Those are two measurements against a real container, not a
statement about runtimes in general.

An engine that cannot account for swap cannot hold the ceiling either way, and the sandbox refuses
on such a machine rather than running under a limit that might not bind.

## The one time limit there is, and what reaching it does

A single call is killed after **{f['sandbox_runtime']['single_call_timeout_seconds']:.0f} seconds**.
A long-lived agent session has no such clock
(`sandbox_runtime.agent_session_has_wall_clock` is {f['sandbox_runtime']['agent_session_has_wall_clock']}):
it has per-message receive timeouts, which is not the same thing. Anything written here about "time
limits" means that one number and nothing more.

Reaching it **ends the program**, which is worth saying because for a long time it did not. The
runtime client was stopped and the container carried on; the conformance suite measured that and it
is fixed. Each run now carries its own identity, the ceiling removes that exact container, waits,
and checks it is gone — by id and by name
(`sandbox_runtime.timeout_removes_the_container` is {f['sandbox_runtime']['timeout_removes_the_container']},
`timeout_verifies_absence` is {f['sandbox_runtime']['timeout_verifies_absence']}). If any of that
cannot be shown, the result is a distinct containment error rather than an ordinary timeout
(`timeout_failure_is_distinct` is {f['sandbox_runtime']['timeout_failure_is_distinct']}): a stop
nobody could verify is not reported as a stop.

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
