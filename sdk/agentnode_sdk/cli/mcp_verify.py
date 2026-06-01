"""Verify an agentnode.yaml manifest for MCP servers.

Reads a manifest file, validates the schema, resolves the package on
npm/PyPI, checks owner match, and optionally runs a protocol test.
Outputs a Verification Report.

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

REQUIRED_FIELDS = ["agentnode", "name", "summary", "package", "transport", "command", "permissions", "env_keys"]
REQUIRED_PACKAGE_FIELDS = ["registry", "name", "version"]
REQUIRED_PERMISSION_FIELDS = ["network", "filesystem", "code_execution"]
VALID_REGISTRIES = {"npm", "pypi"}
VALID_TRANSPORTS = {"stdio", "sse"}
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

_http = httpx.Client(timeout=15, follow_redirects=True)


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    blocking: bool = False


@dataclass
class VerifyReport:
    status: str = "INVALID"
    manifest_version: str = ""
    package: dict = field(default_factory=dict)
    source: dict = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    permissions: dict = field(default_factory=dict)
    requirements: dict = field(default_factory=dict)
    tools_snapshot: list[dict] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "manifest_version": self.manifest_version,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "package": self.package,
            "source": self.source,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
            "permissions": self.permissions,
            "requirements": self.requirements,
            "tools_snapshot": self.tools_snapshot,
            "risk_flags": self.risk_flags,
            "warnings": self.warnings,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Check 1: Schema validation
# ---------------------------------------------------------------------------

def check_schema(manifest: dict, report: VerifyReport) -> bool:
    for f in REQUIRED_FIELDS:
        if f not in manifest:
            report.checks.append(Check("schema", False, f"missing required field: {f}", blocking=True))
            report.errors.append(f"Missing required field: {f}")
            return False

    pkg = manifest.get("package", {})
    for f in REQUIRED_PACKAGE_FIELDS:
        if f not in pkg:
            report.checks.append(Check("schema", False, f"missing package.{f}", blocking=True))
            report.errors.append(f"Missing package.{f}")
            return False

    if pkg["registry"] not in VALID_REGISTRIES:
        report.checks.append(Check("schema", False, f"invalid registry: {pkg['registry']}", blocking=True))
        return False

    if manifest["transport"] not in VALID_TRANSPORTS:
        report.checks.append(Check("schema", False, f"invalid transport: {manifest['transport']}", blocking=True))
        return False

    perms = manifest.get("permissions", {})
    for f in REQUIRED_PERMISSION_FIELDS:
        if f not in perms:
            report.checks.append(Check("schema", False, f"missing permissions.{f}", blocking=True))
            return False

    if perms["network"] not in VALID_NETWORK:
        report.checks.append(Check("schema", False, f"invalid network level: {perms['network']}", blocking=True))
        return False
    if perms["filesystem"] not in VALID_FILESYSTEM:
        report.checks.append(Check("schema", False, f"invalid filesystem level: {perms['filesystem']}", blocking=True))
        return False
    if perms["code_execution"] not in VALID_CODE_EXECUTION:
        report.checks.append(Check("schema", False, f"invalid code_execution level: {perms['code_execution']}", blocking=True))
        return False

    if not isinstance(manifest.get("command"), list) or not manifest["command"]:
        report.checks.append(Check("schema", False, "command must be a non-empty list", blocking=True))
        return False

    if not isinstance(manifest.get("env_keys"), list):
        report.checks.append(Check("schema", False, "env_keys must be a list", blocking=True))
        return False

    report.manifest_version = str(manifest.get("agentnode", ""))
    report.checks.append(Check("schema", True, f"v{report.manifest_version}"))
    return True


# ---------------------------------------------------------------------------
# Check 2+3: Package + version resolve
# ---------------------------------------------------------------------------

def check_package(manifest: dict, report: VerifyReport) -> bool:
    pkg = manifest["package"]
    registry = pkg["registry"]
    name = pkg["name"]
    version = pkg["version"]

    report.package = {"registry": registry, "name": name, "version": version}

    if registry == "npm":
        return _check_npm(name, version, report)
    elif registry == "pypi":
        return _check_pypi(name, version, report)
    return False


def _check_npm(name: str, version: str, report: VerifyReport) -> bool:
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

    versions = data.get("versions", {})
    if version not in versions:
        report.checks.append(Check("version_exists", False, f"version {version} not found", blocking=True))
        report.errors.append(f"Version {version} not found on npm")
        return False

    ver_data = versions[version]
    dist = ver_data.get("dist", {})
    report.package["shasum"] = dist.get("shasum")
    report.package["integrity"] = dist.get("integrity")

    maintainers = data.get("maintainers", [])
    report.package["maintainers"] = [m.get("name", "") for m in maintainers if isinstance(m, dict)]

    repo_info = data.get("repository", {})
    if isinstance(repo_info, str):
        report.package["registry_repo_url"] = repo_info
    elif isinstance(repo_info, dict):
        report.package["registry_repo_url"] = repo_info.get("url", "")

    report.checks.append(Check(
        "version_exists", True,
        f"{version} — shasum: {(dist.get('shasum') or '?')[:16]}..."
    ))
    return True


def _check_pypi(name: str, version: str, report: VerifyReport) -> bool:
    try:
        resp = _http.get(f"{PYPI_REGISTRY}/{name}/{version}/json")
        if resp.status_code == 404:
            resp2 = _http.get(f"{PYPI_REGISTRY}/{name}/json")
            if resp2.status_code == 404:
                report.checks.append(Check("package_exists", False, f"{name} not found on PyPI", blocking=True))
                return False
            report.checks.append(Check("package_exists", True, f"{name} on PyPI"))
            report.checks.append(Check("version_exists", False, f"version {version} not found", blocking=True))
            return False
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        report.checks.append(Check("package_exists", False, f"PyPI error: {e}", blocking=True))
        return False

    info = data.get("info", {})
    report.checks.append(Check("package_exists", True, f"{name} on PyPI"))
    report.checks.append(Check("version_exists", True, f"{version}"))

    project_urls = info.get("project_urls") or {}
    repo_url = (
        project_urls.get("Repository")
        or project_urls.get("Source")
        or project_urls.get("Source Code")
        or project_urls.get("GitHub")
        or project_urls.get("Homepage")
        or info.get("home_page")
        or ""
    )
    if repo_url:
        report.package["registry_repo_url"] = repo_url

    return True


# ---------------------------------------------------------------------------
# Check 4: Command pinning
# ---------------------------------------------------------------------------

def check_pinning(manifest: dict, report: VerifyReport) -> None:
    command = manifest["command"]
    version = manifest["package"]["version"]
    cmd_str = " ".join(command)

    if f"@{version}" in cmd_str or f"=={version}" in cmd_str:
        report.checks.append(Check("version_pinned", True, cmd_str))
    else:
        report.checks.append(Check("version_pinned", False, f"command does not pin to {version}"))
        report.warnings.append(f"Command does not include pinned version @{version}")


# ---------------------------------------------------------------------------
# Check 5: Owner verification
# ---------------------------------------------------------------------------

def check_owner(manifest: dict, report: VerifyReport) -> None:
    source_repo = manifest.get("source_repo", "")
    registry_repo = report.package.get("registry_repo_url", "")

    if not source_repo:
        report.checks.append(Check("owner_verified", False, "no source_repo declared"))
        report.warnings.append("No source_repo in manifest — cannot verify owner")
        return

    report.source = {"declared": source_repo}

    if not registry_repo:
        report.checks.append(Check("owner_verified", False, "no repository URL in registry"))
        report.warnings.append("Registry has no repository URL — cannot verify owner")
        return

    def _extract_owner_repo(url: str) -> str | None:
        m = re.search(r"github\.com/([a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+)", url)
        if m:
            return m.group(1).removesuffix(".git").lower()
        return None

    declared = _extract_owner_repo(source_repo)
    registry = _extract_owner_repo(registry_repo)

    report.source["registry"] = registry_repo

    if not declared or not registry:
        report.checks.append(Check("owner_verified", False, "cannot parse GitHub URLs"))
        report.warnings.append("Cannot parse GitHub owner from URLs")
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
    command = list(manifest["command"])
    exe = shutil.which(command[0])
    if exe:
        command[0] = exe

    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", tempfile.gettempdir())}
    if sys.platform == "win32":
        for k in ("USERPROFILE", "APPDATA", "SYSTEMROOT", "LOCALAPPDATA", "PROGRAMFILES"):
            env[k] = os.environ.get(k, "")

    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            cwd=tempfile.gettempdir(),
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

NETWORK_HINTS = re.compile(r"\b(url|uri|endpoint|host|domain|fetch|request|http|api_url)\b", re.IGNORECASE)
FILESYSTEM_HINTS = re.compile(r"\b(path|file|filename|directory|folder|filepath)\b", re.IGNORECASE)
CODE_EXEC_HINTS = re.compile(r"\b(execute|eval|run|code|script|command|shell|unsafe)\b", re.IGNORECASE)


def check_permissions(manifest: dict, report: VerifyReport) -> None:
    declared = manifest["permissions"]
    report.permissions = {
        "declared": dict(declared),
        "detected": {},
        "mismatches": [],
    }

    tools = report.tools_snapshot
    if not tools:
        return

    all_keys = []
    all_names = []
    for t in tools:
        all_keys.extend(t.get("input_schema_keys", []))
        all_names.append(t.get("name", ""))

    keys_text = " ".join(all_keys)
    names_text = " ".join(all_names)
    combined = f"{keys_text} {names_text}"

    detected_network = bool(NETWORK_HINTS.search(combined))
    detected_fs = bool(FILESYSTEM_HINTS.search(combined))
    detected_exec = bool(CODE_EXEC_HINTS.search(combined))

    report.permissions["detected"] = {
        "network": "likely" if detected_network else "none",
        "filesystem": "likely" if detected_fs else "none",
        "code_execution": "likely" if detected_exec else "none",
    }

    if detected_network and declared["network"] == "none":
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

    has_mismatches = len(report.permissions["mismatches"]) > 0
    if has_mismatches:
        report.checks.append(Check(
            "permission_honesty", False,
            f"{len(report.permissions['mismatches'])} mismatch(es)"
        ))
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

    license_val = manifest.get("license", "")
    if not license_val:
        report.risk_flags.append("missing_license")
    elif license_val not in PERMISSIVE_LICENSES:
        report.risk_flags.append("non_permissive_license")

    report.requirements = manifest.get("requirements", {})


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
        return report
    except Exception as e:
        report.errors.append(f"Cannot read file: {e}")
        return report

    if not isinstance(manifest, dict):
        report.errors.append("Manifest must be a YAML mapping")
        report.checks.append(Check("schema", False, "not a YAML mapping", blocking=True))
        return report

    if not check_schema(manifest, report):
        return report

    if not check_package(manifest, report):
        return report

    check_pinning(manifest, report)
    check_owner(manifest, report)

    if run_test:
        check_protocol(manifest, report)

    check_permissions(manifest, report)
    check_risk_flags(manifest, report)

    has_blocking = any(c.blocking and not c.passed for c in report.checks)
    if has_blocking:
        report.status = "INVALID"
    elif run_test and any(c.name == "protocol_test" and c.passed for c in report.checks):
        report.status = "TESTED"
    elif any(c.name == "package_exists" and c.passed for c in report.checks):
        report.status = "RESOLVED"
    else:
        report.status = "INVALID"

    if report.warnings:
        report.status = max(report.status, "RESOLVED", key=["INVALID", "RESOLVED", "TESTED"].index)
        if report.status != "INVALID":
            has_serious = any("mismatch" in w.lower() for w in report.warnings)
            if has_serious and report.status == "TESTED":
                report.status = "REVIEW_NEEDED"

    return report


# ---------------------------------------------------------------------------
# CLI command
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

    # Human-readable output
    print()
    print("  agentnode.yaml -- Verification Report")
    print("  " + "-" * 40)
    print()

    pkg = report.package
    if pkg:
        pkg_name = pkg.get("name", "?")
        pkg_ver = pkg.get("version", "?")
        print(f"  Package:  {pkg_name}@{pkg_ver}")
    if report.source.get("declared"):
        src = report.source["declared"]
        m = re.search(r"github\.com/(.+?)(?:\.git)?$", src)
        print(f"  Source:   {m.group(1) if m else src}")
    print()

    for c in report.checks:
        marker = "[OK]" if c.passed else "[!!]"
        color = "\033[32m" if c.passed else "\033[33m" if not c.blocking else "\033[31m"
        detail = f" — {c.detail}" if c.detail else ""
        print(f"  {color}{marker}\033[0m {c.name}{detail}")

    if report.permissions.get("declared"):
        print()
        print("  Permission Profile:")
        for key in ("network", "filesystem", "code_execution"):
            val = report.permissions["declared"].get(key, "none")
            color = "\033[32m" if val == "none" else "\033[33m"
            label = key.replace("_", " ").title()
            print(f"    {label + ':':<20} {color}{val}\033[0m")

    if report.risk_flags:
        print(f"\n  Risk Flags: {', '.join(report.risk_flags)}")
    elif any(c.name == "package_exists" and c.passed for c in report.checks):
        print("\n  Risk Flags: none")

    if report.warnings:
        print()
        for w in report.warnings:
            print(f"  \033[33m!!\033[0m {w}")

    status_color = {
        "INVALID": "\033[31m",
        "RESOLVED": "\033[36m",
        "TESTED": "\033[32m",
        "REVIEW_NEEDED": "\033[33m",
    }.get(report.status, "")
    print(f"\n  Status: {status_color}{report.status}\033[0m")
    print()

    return 0 if report.status != "INVALID" else 1
