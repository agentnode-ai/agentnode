"""Verify an agentnode.yaml MCP manifest against registries and protocol.

Reads an existing agentnode.yaml (manifest_version 0.3, runtime=mcp),
validates the structure, resolves the npm/PyPI package, checks owner
match, and optionally runs a protocol test.

No code is executed without --test. Default mode is read-only checks.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

NPM_REGISTRY = "https://registry.npmjs.org"
PYPI_REGISTRY = "https://pypi.org/pypi"

PERMISSIVE_LICENSES = {
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC",
    "0BSD", "Unlicense", "CC0-1.0", "MPL-2.0", "Zlib",
    "MIT OR Apache-2.0",
}

VALID_NETWORK = {"none", "restricted", "unrestricted"}
VALID_FILESYSTEM = {"none", "read_only", "workspace_write", "any"}
VALID_CODE_EXECUTION = {"none", "limited_subprocess", "shell"}

CRYPTO_RE = re.compile(
    r"\b(?:USDC|USDT|Bitcoin|Lightning|L402|x402|crypto|wallet|blockchain|"
    r"on[- ]?chain|web3|ETH|Ethereum|Solana|Base)\b", re.IGNORECASE
)
PAYMENT_RE = re.compile(
    r"(?:\$\d+\.?\d*\s*(?:per|/|each|USDC|USD))|"
    r"\b(?:pay[- ]?per[- ]?call|billing|pricing|subscription|paid\s+(?:API|tier|plan))\b",
    re.IGNORECASE,
)

NETWORK_HINTS = re.compile(r"\b(url|uri|endpoint|host|domain|fetch|request|http|api_url)\b", re.IGNORECASE)
FILESYSTEM_HINTS = re.compile(r"\b(path|file|filename|directory|folder|filepath)\b", re.IGNORECASE)
CODE_EXEC_HINTS = re.compile(r"\b(execute|eval|run|code|script|command|shell|unsafe)\b", re.IGNORECASE)

_http = httpx.Client(timeout=15, follow_redirects=True)


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    blocking: bool = False


@dataclass
class Action:
    severity: str  # high, medium, low
    code: str
    title: str
    detail: str
    fix: str = ""


@dataclass
class VerifyReport:
    status: str = "INVALID"
    summary: str = ""
    manifest_version: str = ""
    package: dict = field(default_factory=dict)
    source: dict = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    permissions: dict = field(default_factory=dict)
    requirements: dict = field(default_factory=dict)
    tools_snapshot: list[dict] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "summary": self.summary,
            "manifest_version": self.manifest_version,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "package": self.package,
            "source": self.source,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
            "actions": [
                {"severity": a.severity, "code": a.code, "title": a.title,
                 "detail": a.detail, "fix": a.fix}
                for a in self.actions
            ],
            "permissions": self.permissions,
            "requirements": self.requirements,
            "tools_snapshot": self.tools_snapshot,
            "risk_flags": self.risk_flags,
            "warnings": self.warnings,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Check 1: Schema — validates existing agentnode.yaml v0.3 with runtime=mcp
# ---------------------------------------------------------------------------

def check_schema(manifest: dict, report: VerifyReport) -> bool:
    mv = manifest.get("manifest_version")
    if mv not in ("0.1", "0.2", "0.3"):
        report.checks.append(Check("schema", False, f"unsupported manifest_version: {mv}", blocking=True))
        report.errors.append(f"Unsupported manifest_version: {mv}")
        return False

    report.manifest_version = str(mv)

    runtime = manifest.get("runtime")
    if runtime != "mcp":
        report.checks.append(Check("schema", False, f"runtime is '{runtime}', expected 'mcp'", blocking=True))
        report.errors.append("This command verifies MCP manifests (runtime: mcp)")
        return False

    mcp = manifest.get("mcp_server")
    if not isinstance(mcp, dict):
        report.checks.append(Check("schema", False, "missing mcp_server block", blocking=True))
        report.errors.append("MCP manifest requires mcp_server block")
        return False

    if not isinstance(mcp.get("command"), list) or not mcp["command"]:
        report.checks.append(Check("schema", False, "mcp_server.command must be a non-empty list", blocking=True))
        return False

    if not mcp.get("npm_package") and not mcp.get("pypi_package"):
        report.checks.append(Check("schema", False, "mcp_server needs npm_package or pypi_package", blocking=True))
        return False

    report.checks.append(Check("schema", True, f"manifest v{mv}, runtime=mcp"))
    return True


# ---------------------------------------------------------------------------
# Check 2+3: Package + version resolve
# ---------------------------------------------------------------------------

def check_package(manifest: dict, report: VerifyReport) -> bool:
    mcp = manifest["mcp_server"]
    npm_name = mcp.get("npm_package")
    pypi_name = mcp.get("pypi_package")

    version = None
    cmd = mcp.get("command", [])
    for part in cmd:
        if "@" in part and not part.startswith("@"):
            version = part.split("@")[-1]
            break
        if "@" in part and part.startswith("@"):
            parts = part.split("@")
            if len(parts) == 3:
                version = parts[2]
                break

    if npm_name:
        report.package = {"registry": "npm", "name": npm_name, "version": version}
        return _check_npm(npm_name, version, report)
    elif pypi_name:
        report.package = {"registry": "pypi", "name": pypi_name, "version": version}
        return _check_pypi(pypi_name, version, report)

    report.checks.append(Check("package_exists", False, "no package name found", blocking=True))
    return False


def _check_npm(name: str, version: str | None, report: VerifyReport) -> bool:
    try:
        resp = _http.get(f"{NPM_REGISTRY}/{name}")
        if resp.status_code == 404:
            report.checks.append(Check("package_exists", False, f"{name} not found on npm", blocking=True))
            report.errors.append(f"Package {name} not found on npm")
            return False
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        report.checks.append(Check("package_exists", False, f"npm error: {e}", blocking=True))
        return False

    report.checks.append(Check("package_exists", True, f"{name} on npm"))

    if version:
        versions = data.get("versions", {})
        if version not in versions:
            report.checks.append(Check("version_exists", False, f"version {version} not found", blocking=True))
            report.errors.append(f"Version {version} not found on npm")
            return False

        ver_data = versions[version]
        dist = ver_data.get("dist", {})
        report.package["shasum"] = dist.get("shasum")
        report.package["integrity"] = dist.get("integrity")
        report.checks.append(Check(
            "version_exists", True,
            f"{version} -- shasum: {(dist.get('shasum') or '?')[:16]}..."
        ))
    else:
        report.checks.append(Check("version_exists", False, "no version pinned in command"))
        report.warnings.append("No pinned version detected in command")

    maintainers = data.get("maintainers", [])
    report.package["maintainers"] = [m.get("name", "") for m in maintainers if isinstance(m, dict)]

    repo_info = data.get("repository", {})
    if isinstance(repo_info, str):
        report.package["registry_repo_url"] = repo_info
    elif isinstance(repo_info, dict):
        report.package["registry_repo_url"] = repo_info.get("url", "")

    return True


def _check_pypi(name: str, version: str | None, report: VerifyReport) -> bool:
    url = f"{PYPI_REGISTRY}/{name}/json" if not version else f"{PYPI_REGISTRY}/{name}/{version}/json"
    try:
        resp = _http.get(url)
        if resp.status_code == 404:
            report.checks.append(Check("package_exists", False, f"{name} not found on PyPI", blocking=True))
            return False
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        report.checks.append(Check("package_exists", False, f"PyPI error: {e}", blocking=True))
        return False

    info = data.get("info", {})
    report.checks.append(Check("package_exists", True, f"{name} on PyPI"))
    if version:
        report.checks.append(Check("version_exists", True, f"{version}"))

    project_urls = info.get("project_urls") or {}
    repo_url = (
        project_urls.get("Repository") or project_urls.get("Source")
        or project_urls.get("Source Code") or project_urls.get("GitHub")
        or project_urls.get("Homepage") or info.get("home_page") or ""
    )
    if repo_url:
        report.package["registry_repo_url"] = repo_url

    return True


# ---------------------------------------------------------------------------
# Check 4: Version pinning
# ---------------------------------------------------------------------------

def check_pinning(manifest: dict, report: VerifyReport) -> None:
    cmd = manifest["mcp_server"]["command"]
    version = report.package.get("version")
    cmd_str = " ".join(cmd)

    if version and (f"@{version}" in cmd_str or f"=={version}" in cmd_str):
        report.checks.append(Check("version_pinned", True, cmd_str))
    else:
        report.checks.append(Check("version_pinned", False, "command does not pin exact version"))
        report.warnings.append("Command does not include pinned version")


# ---------------------------------------------------------------------------
# Check 5: Owner verification
# ---------------------------------------------------------------------------

def check_owner(manifest: dict, report: VerifyReport) -> None:
    source_repo = manifest["mcp_server"].get("source_repo", "")
    registry_repo = report.package.get("registry_repo_url", "")

    if not source_repo:
        report.checks.append(Check("owner_verified", False, "no source_repo in mcp_server"))
        report.warnings.append("No source_repo declared -- cannot verify owner")
        return

    report.source = {"declared": source_repo}

    if not registry_repo:
        report.checks.append(Check("owner_verified", False, "no repository URL in registry"))
        report.warnings.append("Registry has no repository URL -- cannot verify owner")
        return

    def _extract(url: str) -> str | None:
        m = re.search(r"github\.com/([a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+)", url)
        return m.group(1).removesuffix(".git").lower() if m else None

    declared = _extract(source_repo)
    registry = _extract(registry_repo)
    report.source["registry"] = registry_repo

    if not declared or not registry:
        report.checks.append(Check("owner_verified", False, "cannot parse GitHub URLs"))
        return

    if declared == registry:
        report.checks.append(Check("owner_verified", True, f"{declared} matches registry"))
    else:
        report.checks.append(Check("owner_verified", False, f"declared={declared}, registry={registry}"))
        report.warnings.append(f"Owner mismatch: manifest says {declared}, registry says {registry}")


# ---------------------------------------------------------------------------
# Check 6: Protocol test (only with --test)
# ---------------------------------------------------------------------------

def check_protocol(manifest: dict, report: VerifyReport) -> None:
    command = list(manifest["mcp_server"]["command"])
    exe = shutil.which(command[0])
    if exe:
        command[0] = exe

    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", tempfile.gettempdir())}
    if sys.platform == "win32":
        for k in ("USERPROFILE", "APPDATA", "SYSTEMROOT", "LOCALAPPDATA", "PROGRAMFILES"):
            env[k] = os.environ.get(k, "")

    try:
        proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, text=True, cwd=tempfile.gettempdir(),
        )
    except Exception as e:
        report.checks.append(Check("protocol_test", False, f"start failed: {e}"))
        return

    def recv(timeout=30):
        result = [None]
        def _read():
            try:
                line = proc.stdout.readline()
                if line and line.strip():
                    result[0] = json.loads(line)
            except Exception:
                pass
        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(timeout=timeout)
        return result[0]

    try:
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "agentnode-verify", "version": "0.1.0"},
            },
        }) + "\n")
        proc.stdin.flush()

        resp = recv(timeout=30)
        if not resp or "error" in resp:
            report.checks.append(Check("protocol_test", False, f"initialize failed: {resp}"))
            return

        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n")
        proc.stdin.flush()

        tools_resp = recv(timeout=15)
        if tools_resp and "result" in tools_resp:
            tools = tools_resp["result"].get("tools", [])
            report.tools_snapshot = [
                {
                    "name": t.get("name", ""),
                    "description": (t.get("description", "") or "")[:200],
                    "input_schema_keys": list(t.get("inputSchema", {}).get("properties", {}).keys())[:10],
                }
                for t in tools[:50]
            ]
            report.checks.append(Check("protocol_test", True, f"{len(tools)} tools discovered"))
        else:
            report.checks.append(Check("protocol_test", False, f"tools/list failed: {tools_resp}"))
    except Exception as e:
        report.checks.append(Check("protocol_test", False, str(e)))
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Check 7: Permission honesty
# ---------------------------------------------------------------------------

def check_permissions(manifest: dict, report: VerifyReport) -> None:
    perms = manifest.get("permissions", {})
    declared = {
        "network": perms.get("network", {}).get("level", "none"),
        "filesystem": perms.get("filesystem", {}).get("level", "none"),
        "code_execution": perms.get("code_execution", {}).get("level", "none"),
    }
    report.permissions = {"declared": declared, "detected": {}, "mismatches": []}

    tools = report.tools_snapshot
    if not tools:
        return

    all_keys = []
    all_names = []
    for t in tools:
        all_keys.extend(t.get("input_schema_keys", []))
        all_names.append(t.get("name", ""))
    combined = " ".join(all_keys) + " " + " ".join(all_names)

    detected_net = bool(NETWORK_HINTS.search(combined))
    detected_fs = bool(FILESYSTEM_HINTS.search(combined))
    detected_exec = bool(CODE_EXEC_HINTS.search(combined))

    report.permissions["detected"] = {
        "network": "likely" if detected_net else "none",
        "filesystem": "likely" if detected_fs else "none",
        "code_execution": "likely" if detected_exec else "none",
    }

    if detected_net and declared["network"] == "none":
        mm = "declared network:none but tools suggest network access"
        report.permissions["mismatches"].append(mm)
        report.warnings.append(f"Permission mismatch: {mm}")

    if detected_fs and declared["filesystem"] == "none":
        mm = "declared filesystem:none but tools suggest filesystem access"
        report.permissions["mismatches"].append(mm)
        report.warnings.append(f"Permission mismatch: {mm}")

    if detected_exec and declared["code_execution"] == "none":
        mm = "declared code_execution:none but tools suggest code execution"
        report.permissions["mismatches"].append(mm)
        report.warnings.append(f"Permission mismatch: {mm}")

    if report.permissions["mismatches"]:
        report.checks.append(Check("permission_honesty", False, f"{len(report.permissions['mismatches'])} mismatch(es)"))
    else:
        report.checks.append(Check("permission_honesty", True, "declarations match detected capabilities"))


# ---------------------------------------------------------------------------
# Check 8: Risk flags
# ---------------------------------------------------------------------------

def check_risk_flags(manifest: dict, report: VerifyReport) -> None:
    tool_text = " ".join(
        f"{t.get('name', '')} {t.get('description', '')}"
        for t in report.tools_snapshot
    )
    summary = manifest.get("summary", "") + " " + manifest.get("description", "")

    if CRYPTO_RE.search(summary) or CRYPTO_RE.search(tool_text):
        report.risk_flags.append("crypto_payment")
    if PAYMENT_RE.search(summary) or PAYMENT_RE.search(tool_text):
        report.risk_flags.append("paid_api")


# ---------------------------------------------------------------------------
# Action derivation — turns checks/warnings into maintainer instructions
# ---------------------------------------------------------------------------

def derive_actions(report: VerifyReport) -> None:
    for c in report.checks:
        if c.passed:
            continue

        if c.name == "schema" and c.blocking:
            report.actions.append(Action(
                severity="high", code="SCHEMA_INVALID",
                title="Fix manifest schema",
                detail=c.detail,
                fix="Ensure agentnode.yaml has manifest_version, runtime: mcp, and a valid mcp_server block.",
            ))

        elif c.name == "package_exists":
            report.actions.append(Action(
                severity="high", code="PACKAGE_NOT_FOUND",
                title="Package not found on registry",
                detail=c.detail,
                fix="Publish your package to npm or PyPI before submitting to AgentNode.",
            ))

        elif c.name == "version_exists":
            report.actions.append(Action(
                severity="high", code="VERSION_NOT_FOUND",
                title="Pinned version not found",
                detail=c.detail,
                fix="Ensure the version in mcp_server.command matches a published version.",
            ))

        elif c.name == "version_pinned":
            report.actions.append(Action(
                severity="medium", code="VERSION_NOT_PINNED",
                title="Command does not pin exact version",
                detail=c.detail,
                fix="Add @version to the package name in mcp_server.command (e.g. @org/pkg@1.2.3).",
            ))

        elif c.name == "owner_verified":
            report.actions.append(Action(
                severity="high", code="OWNER_METADATA_MISMATCH",
                title="Source repo does not match registry",
                detail=c.detail,
                fix="Update source_repo in mcp_server to match the repository URL in your npm/PyPI package, or update your package metadata.",
            ))

        elif c.name == "protocol_test":
            report.actions.append(Action(
                severity="medium", code="PROTOCOL_TEST_FAILED",
                title="MCP protocol test failed",
                detail=c.detail,
                fix="Ensure your server responds to initialize and tools/list via stdio. Run: agentnode mcp verify . --test",
            ))

        elif c.name == "permission_honesty":
            mismatches = report.permissions.get("mismatches", [])
            for mm in mismatches:
                report.actions.append(Action(
                    severity="medium", code="PERMISSION_MISMATCH",
                    title="Declared permissions may be too low",
                    detail=mm,
                    fix="Update the permission level in your manifest to match actual tool capabilities.",
                ))

    if report.risk_flags:
        for flag in report.risk_flags:
            report.actions.append(Action(
                severity="low", code=f"RISK_FLAG_{flag.upper()}",
                title=f"Risk signal: {flag.replace('_', ' ')}",
                detail=f"Detected risk indicator: {flag}",
                fix="This may require additional review before catalog inclusion.",
            ))


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def verify_manifest(manifest_path: Path, run_test: bool = False) -> VerifyReport:
    report = VerifyReport()

    try:
        raw = manifest_path.read_text(encoding="utf-8")
        manifest = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        report.errors.append(f"YAML parse error: {e}")
        report.checks.append(Check("schema", False, f"YAML parse error: {e}", blocking=True))
        derive_actions(report)
        report.summary = "Manifest has syntax errors."
        return report
    except Exception as e:
        report.errors.append(f"Cannot read file: {e}")
        return report

    if not isinstance(manifest, dict):
        report.errors.append("Manifest must be a YAML mapping")
        report.checks.append(Check("schema", False, "not a YAML mapping", blocking=True))
        derive_actions(report)
        report.summary = "Manifest is not a valid YAML mapping."
        return report

    if not check_schema(manifest, report):
        derive_actions(report)
        report.summary = "Manifest schema is invalid."
        return report

    if not check_package(manifest, report):
        derive_actions(report)
        report.summary = "Package could not be resolved on registry."
        return report

    check_pinning(manifest, report)
    check_owner(manifest, report)

    if run_test:
        check_protocol(manifest, report)

    check_permissions(manifest, report)
    check_risk_flags(manifest, report)
    derive_actions(report)

    # --- Status determination ---
    has_blocking = any(c.blocking and not c.passed for c in report.checks)
    has_high_actions = any(a.severity == "high" for a in report.actions)
    has_medium_actions = any(a.severity == "medium" for a in report.actions)
    protocol_passed = any(c.name == "protocol_test" and c.passed for c in report.checks)
    package_resolved = any(c.name == "package_exists" and c.passed for c in report.checks)

    if has_blocking:
        report.status = "INVALID"
    elif has_high_actions:
        report.status = "MAINTAINER_ACTION_REQUIRED"
    elif run_test and protocol_passed and not has_medium_actions:
        report.status = "TESTED"
    elif run_test and protocol_passed and has_medium_actions:
        report.status = "REVIEW_NEEDED"
    elif package_resolved:
        report.status = "RESOLVED"
    else:
        report.status = "INVALID"

    # --- Summary ---
    n_actions = len(report.actions)
    n_tools = len(report.tools_snapshot)
    pkg_name = report.package.get("name", "?")

    if report.status == "INVALID":
        report.summary = f"{pkg_name}: manifest has blocking errors."
    elif report.status == "MAINTAINER_ACTION_REQUIRED":
        high_count = sum(1 for a in report.actions if a.severity == "high")
        report.summary = f"{pkg_name}: {high_count} issue(s) must be fixed before catalog inclusion."
    elif report.status == "REVIEW_NEEDED":
        report.summary = f"{pkg_name}: protocol test passed ({n_tools} tools), but {n_actions} warning(s) need review."
    elif report.status == "TESTED":
        report.summary = f"{pkg_name}: all checks passed, {n_tools} tools discovered. Ready for review."
    elif report.status == "RESOLVED":
        report.summary = f"{pkg_name}: package resolved on registry. Run with --test for full verification."

    return report


# ---------------------------------------------------------------------------
# CLI output
# ---------------------------------------------------------------------------

def cmd_mcp_verify(path_str: str, test: bool = False, json_output: bool = False) -> int:
    manifest_path = Path(path_str).resolve()

    if manifest_path.is_dir():
        manifest_path = manifest_path / "agentnode.yaml"

    if not manifest_path.exists():
        if json_output:
            print(json.dumps({"status": "INVALID", "errors": [f"File not found: {manifest_path}"]}))
        else:
            print(f"  Error: {manifest_path} not found")
        return 1

    report = verify_manifest(manifest_path, run_test=test)

    if json_output:
        print(json.dumps(report.to_dict(), indent=2))
        return 0 if report.status != "INVALID" else 1

    print()
    print("  agentnode.yaml -- MCP Verification Report")
    print("  " + "-" * 42)

    # Summary
    status_color = {
        "INVALID": "\033[31m",
        "RESOLVED": "\033[36m",
        "TESTED": "\033[32m",
        "REVIEW_NEEDED": "\033[33m",
        "MAINTAINER_ACTION_REQUIRED": "\033[33m",
    }.get(report.status, "")
    print(f"  {status_color}{report.status}\033[0m: {report.summary}")
    print()

    # Package info
    pkg = report.package
    if pkg:
        print(f"  Package:  {pkg.get('name', '?')}@{pkg.get('version', '?')}")
    if report.source.get("declared"):
        src = report.source["declared"]
        m = re.search(r"github\.com/(.+?)(?:\.git)?$", src)
        print(f"  Source:   {m.group(1) if m else src}")
    print()

    # Checks
    print("  Checks:")
    for c in report.checks:
        marker = "[OK]" if c.passed else "[!!]"
        color = "\033[32m" if c.passed else "\033[33m" if not c.blocking else "\033[31m"
        detail = f" -- {c.detail}" if c.detail else ""
        print(f"    {color}{marker}\033[0m {c.name}{detail}")

    # Permission Profile
    if report.permissions.get("declared"):
        print()
        print("  Permission Profile:")
        for key in ("network", "filesystem", "code_execution"):
            val = report.permissions["declared"].get(key, "none")
            color = "\033[32m" if val == "none" else "\033[33m"
            label = key.replace("_", " ").title()
            print(f"    {label + ':':<20} {color}{val}\033[0m")

    # Risk Flags
    if report.risk_flags:
        print(f"\n  Risk Flags: {', '.join(report.risk_flags)}")
    elif any(c.name == "package_exists" and c.passed for c in report.checks):
        print("\n  Risk Flags: none")

    # Required Actions
    if report.actions:
        print()
        high = [a for a in report.actions if a.severity == "high"]
        medium = [a for a in report.actions if a.severity == "medium"]
        low = [a for a in report.actions if a.severity == "low"]

        if high:
            print("  \033[31mRequired Actions:\033[0m")
            for a in high:
                print(f"    [{a.code}] {a.title}")
                print(f"      {a.detail}")
                if a.fix:
                    print(f"      Fix: {a.fix}")

        if medium:
            print("  \033[33mRecommended:\033[0m")
            for a in medium:
                print(f"    [{a.code}] {a.title}")
                print(f"      {a.detail}")
                if a.fix:
                    print(f"      Fix: {a.fix}")

        if low:
            print("  Notes:")
            for a in low:
                print(f"    [{a.code}] {a.title}")

    # Next steps
    if report.status == "RESOLVED":
        print("\n  Next: run \033[1magentnode mcp verify . --test\033[0m for full protocol verification")
    elif report.status == "TESTED":
        print("\n  Next: this MCP is ready for catalog review submission")
    elif report.status == "MAINTAINER_ACTION_REQUIRED":
        print(f"\n  Next: fix the {sum(1 for a in report.actions if a.severity == 'high')} required action(s) above, then re-verify")

    print()
    return 0 if report.status not in ("INVALID",) else 1
