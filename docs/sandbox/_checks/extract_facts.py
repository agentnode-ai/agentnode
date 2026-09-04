#!/usr/bin/env python3
"""Read the SDK source and write down what is actually there.

Every availability claim in the sandbox documentation is generated from this file, so a claim cannot
drift away from the code without the generator noticing. Nothing here imports the SDK — it reads the
source, so it works without an installed package and cannot be fooled by a stale install.

    python docs/sandbox/_checks/extract_facts.py            # writes _facts/code-facts.json
    python docs/sandbox/_checks/extract_facts.py --print    # and shows it
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SDK = ROOT / "sdk" / "agentnode_sdk"
OUT = Path(__file__).resolve().parents[1] / "_facts" / "code-facts.json"


def read(rel: str) -> str:
    return (SDK / rel).read_text(encoding="utf-8")


def cli_tree() -> dict[str, list[str]]:
    text = read("cli/main.py")
    tree: dict[str, list[str]] = {}
    for m in re.finditer(r"(\w+)\.add_parser\(\s*\"([^\"]+)\"", text):
        tree.setdefault(m.group(1), []).append(m.group(2))
    return tree


def config_surface() -> dict:
    text = read("config.py")
    allowed = dict(re.findall(r'"((?:sandbox|agent_sandbox|trust|guard|llm)[a-z_.]*)":\s*\(([^)]*)\)',
                              text))
    descriptions = dict(re.findall(r'"([a-z_.]+)":\s*"((?:[^"\\]|\\.)*)",\n', text))
    defaults = {}
    for key in ("host_trust_policy",):
        m = re.search(rf'"{key}":\s*"([a-z_]+)"', text)
        if m:
            defaults[f"sandbox.{key}"] = m.group(1)
    m = re.search(r'"agent_sandbox":\s*\{[^}]*?"enabled":\s*(True|False)', text, re.S)
    if m:
        defaults["agent_sandbox.enabled"] = m.group(1) == "True"
    return {
        "allowed_values": {k: [v.strip().strip('"') for v in vals.split(",") if v.strip()]
                           for k, vals in allowed.items()},
        "shipped_defaults": defaults,
        "descriptions": {k: v for k, v in descriptions.items() if k.count(".") == 1},
    }


def sandbox_runtime() -> dict:
    cb = read("sandbox/container_backend.py")
    # the constant is written across two source lines, so join the pieces rather than guessing
    m = re.search(r"_BASE_IMAGE\s*=\s*\(([^)]*)\)", cb, re.S)
    image = "".join(re.findall(r'"([^"]*)"', m.group(1))) if m else None
    candidates = re.search(r'candidates\s*=\s*\[self\._runtime\]\s*if\s*self\._runtime\s*else\s*\[([^\]]+)\]', cb)
    flags = sorted(set(re.findall(r'"(--[a-z-]+(?:=[A-Za-z-]+)?)"', cb)))
    types = read("sandbox/types.py")
    networks = re.findall(r'#\s*"none"\s*\|\s*"([a-z]+)"\s*\|\s*"([a-z]+)"\s*\|\s*"([a-z]+)"', types)
    return {
        "pinned_image": image,
        "image_is_placeholder": bool(image and set(image.rsplit(":", 1)[-1]) == {"0"}),
        "runtimes_probed": [c.strip().strip('"') for c in candidates.group(1).split(",")] if candidates else [],
        "hardening_flags": [f for f in flags if f.startswith(("--rm", "--cap", "--security", "--user",
                                                              "--pids", "--memory", "--cpus", "--network",
                                                              "--read-only", "--tmpfs"))],
        "network_modes": ["none", "restricted", "default", "egress"],
        # What each mode ACTUALLY puts on the argv, read out of wrap_command. The names are not
        # self-explanatory: "restricted" emits an ordinary bridge network today.
        "network_mode_flags": _network_mode_flags(cb),
        # SANDBOX-DOCS-0003: these two were read off adjectives in a docstring ("INERT", "does NOT
        # create the network"). Those sentences describe the stage the file was written in, and the
        # product moved on without rewriting them -- so the facts said inert while two run paths
        # called the machinery. A comment is not a fact. Both are now derived from the call graph,
        # and what the source SAYS is kept beside it so the disagreement stays visible.
        "egress_live_callers": _callers_of("start_egress_proxy"),
        "secret_passthrough_live_callers": _callers_of("env_passthrough="),
        "source_docstring_still_says_egress_is_inert":
            "does NOT create the network" in types or "INERTLY" in types,
        "source_docstring_still_says_passthrough_is_inert": "INERT in 3B-2a" in types,
        # A single call is killed after this many seconds; the long-lived agent session has no
        # equivalent wall clock, only per-message receive timeouts.
        "single_call_timeout_seconds": _default_timeout(cb),
        # EM-3B-R1. What happens when that ceiling is reached: the client used to be killed and
        # the container left running. These three say whether that is still true.
        "timeout_removes_the_container": "_end_timed_out_run" in cb and 'rm", "-f"' in cb,
        "timeout_verifies_absence": "could not be shown to be gone" in cb,
        "timeout_failure_is_distinct": "SandboxContainmentError" in cb,
        # The memory ceiling binds only when the swap half of it is bound too.
        "memory_ceiling_includes_swap": _memory_swap_bound(cb),
        "agent_session_has_wall_clock": "wall" in read("sandbox/agent_session.py").lower(),
        # EM-3B-R1 / R3: one classifier, and these are the situations it tells apart.
        "refusal_cases": _refusal_cases(),
        "refusal_is_structured": (SDK / "sandbox" / "refusal.py").is_file(),
        # Three properties of that classifier, read out of it rather than claimed about it.
        "refusal_requires_an_action": _refusal_says("a refusal must carry something the person"),
        "refusal_withholds_unavailable_actions": _refusal_says("a.available_here(which)"),
        "refusal_names_a_recheck": _refusal_says("Then check it worked"),
        # SANDBOX-DOCS-0004: the pages describe the gates a credentialed run must pass. Read them
        # out of the refusal sites rather than trusting the prose.
        "credentialed_run_refusals": _refusal_reasons(),
        # Two facts that exist so a status can be DERIVED rather than asserted (SANDBOX-DOCS-0007):
        # the refusal row needs proof the refusal exists, and the broker row needs proof it does not.
        "refuses_without_runtime": "raise SandboxRequiredError" in cb and "check_available" in cb,
        "credential_broker_exists": (SDK / "sandbox" / "credential_broker.py").is_file(),
        # And the part a reader most needs: `--env NAME` means the container RUNTIME supplies the
        # value inside the container. Name-only keeps the value off the argv and out of this
        # process; it does NOT keep it away from the code in the sandbox.
        "secret_value_is_inside_the_container": 'argv += ["--env", name]' in cb,
        "secret_name_only_requires_egress": "env_passthrough requires network='egress'" in cb,
    }


def _refusal_reasons() -> dict:
    """The reason code of every refusal a credentialed run can hit, per module."""
    out = {}
    for rel in ("runtimes/mcp_runner.py", "runtimes/toolpack_credentials.py"):
        src = read(rel)
        codes = re.findall(r"Refused\(\s*[\"']([a-z_]+)[\"']", src)
        out[rel] = sorted(set(c for c in codes if c))
    return out

def _network_mode_flags(cb: str) -> dict:
    """mode -> the network argument wrap_command emits for it, read from the source."""
    out = {}
    for mode in ("none", "restricted"):
        i = cb.find('spec.network == "%s"' % mode)
        seg = cb[i:i + 220] if i >= 0 else ""
        m = re.search(r'"--network", "(\w+)"', seg)
        out[mode] = "--network " + m.group(1) if m else "unknown"
    out["egress"] = ("--network <a pre-created internal network>; no handle raises instead"
                     if 'argv += ["--network", eg.network_name]' in cb else "unknown")
    out["default"] = ("no --network flag at all: an open network"
                      if 'spec.network == "default"' in cb else "unknown")
    return out


def _callers_of(needle: str) -> list:
    """Product modules that use `needle`, excluding the module defining it and the tests.

    This is the difference between "the code exists" and "something calls it".
    """
    hits = []
    for py in sorted(SDK.rglob("*.py")):
        rel = str(py.relative_to(SDK)).replace(chr(92), "/")
        # The conformance suite calls this machinery in order to MEASURE it. That is not a run
        # path: counting it here would let the instrument answer the question it exists to ask.
        if rel.startswith(("tests/", "conformance/")) or rel == "sandbox/egress.py":
            continue
        for line in py.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if needle in s and not s.startswith("#") and "def " not in s:
                hits.append(rel)
                break
    return hits


def _memory_swap_bound(cb: str) -> bool:
    """True when --memory-swap is passed and equals --memory, which is what makes the total bind."""
    flags = re.search(r"_HARDENED_FLAGS = \[(.*?)\]", cb, re.S)
    if not flags:
        return False
    items = re.findall(r'"([^"]+)"', flags.group(1))
    if "--memory-swap" not in items or "--memory" not in items:
        return False
    return items[items.index("--memory") + 1] == items[items.index("--memory-swap") + 1]


def _refusal_says(needle: str) -> bool:
    """Whether the refusal classifier's own source contains this, so a property is read not told."""
    try:
        return needle in read("sandbox/refusal.py")
    except Exception:                                              # noqa: BLE001
        return False


def _refusal_cases() -> list:
    """The situations the refusal classifier tells apart, read from the enum."""
    try:
        src = read("sandbox/refusal.py")
    except Exception:                                              # noqa: BLE001
        return []
    body = re.search(r"class RefusalCase\(str, Enum\):(.*?)(\n\n|\nclass )", src, re.S)
    return re.findall(r'"([a-z_]+)"', body.group(1)) if body else []


def _default_timeout(cb: str):
    i = cb.find("def run_process(")
    m = re.search(r"timeout: float = ([0-9.]+)", cb[i:i + 600]) if i >= 0 else None
    return float(m.group(1)) if m else None


def what_is_wired_in() -> dict:
    """Which of the newer pieces are actually reachable from a run, and which only exist."""
    wired = {}
    contract = SDK / "sandbox" / "contract.py"
    wired["em3_contract_module_exists"] = contract.is_file()
    importers = []
    for py in SDK.rglob("*.py"):
        if py == contract:
            continue
        if re.search(r"from\s+\.?contract\s+import|sandbox\.contract", py.read_text(encoding="utf-8")):
            importers.append(str(py.relative_to(SDK)))
    wired["em3_contract_importers"] = importers
    backends = []
    for py in (SDK / "sandbox").glob("*.py"):
        src = py.read_text(encoding="utf-8")
        backends += [m for m in re.findall(r"class\s+(\w+)\(SandboxBackend\)", src)]
    wired["sandbox_backend_implementations"] = sorted(backends)
    wired["remote_backend_exists"] = any("remote" in b.lower() for b in backends)
    wired["managed_backend_exists"] = any("managed" in b.lower() for b in backends)
    wired["conformance_suite_exists"] = (ROOT / "sdk" / "tests" / "conformance").exists()
    return wired


def execution_paths() -> dict:
    """Where foreign code can run today, read from the runners rather than from a plan."""
    out = {}
    ag = read("runtimes/agent_sandbox.py")
    out["agent"] = {"network": re.search(r'network="(\w+)"', ag).group(1),
                    "mount": "read-only /pack" if "read_only=True" in ag else "unknown"}
    mcp = read("runtimes/mcp_runner.py")
    out["mcp"] = {"networks_used": sorted(set(re.findall(r'network="(\w+)"', mcp)))}
    inst = read("installer.py")
    out["build"] = {"network": "default (build step only)" if 'network="default"' in inst else "unknown"}
    exc = read("exceptions.py")
    out["host_agent_entrypoint"] = ("refused: HostAgentExecutionUnsupported"
                                    if "HostAgentExecutionUnsupported" in exc else "unknown")
    return out


def main() -> int:
    facts = {
        "generated_from": "sdk/agentnode_sdk source, read not imported",
        "sdk_version": re.search(r'__version__\s*=\s*"([^"]+)"', read("__init__.py")).group(1),
        "cli": cli_tree(),
        "config": config_surface(),
        "sandbox_runtime": sandbox_runtime(),
        "wired_in": what_is_wired_in(),
        "execution_paths": execution_paths(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(facts, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"  wrote {OUT.relative_to(ROOT)}")
    if "--print" in sys.argv:
        print(json.dumps(facts, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
