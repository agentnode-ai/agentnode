"""Offline package validation for agentnode validate command."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SLUG_PATTERN = re.compile(r"^[a-z0-9-]{3,60}$")
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$")
CASSETTE_PATH_RE = re.compile(r"^fixtures/cassettes/[\w.-]+\.(yaml|yml|json)$")
KNOWN_SYSTEM_REQUIREMENTS = {"browser", "ffmpeg", "tesseract", "imagemagick"}


@dataclass
class CheckResult:
    passed: bool
    label: str
    detail: str = ""


@dataclass
class ValidateResult:
    package_id: str = ""
    version: str = ""
    package_type: str = ""
    checks: list[CheckResult] = field(default_factory=list)
    max_tier: str = "unverified"
    verification_mode: str = "none"
    cases_count: int = 0
    missing_items: list[str] = field(default_factory=list)
    risk_level: str = ""
    risk_score: int = 0
    risk_signals: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    risk_hints: list[str] = field(default_factory=list)
    connector_provider: str = ""
    connector_auth_type: str = ""
    connector_scopes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(not c.passed for c in self.checks)


def validate_package_dir(path: Path) -> ValidateResult:
    """Validate an agentnode package directory offline."""
    result = ValidateResult()

    manifest_path = path / "agentnode.yaml"
    if not manifest_path.exists():
        result.checks.append(CheckResult(False, "agentnode.yaml", "File not found"))
        return result
    result.checks.append(CheckResult(True, "agentnode.yaml", "Found"))

    try:
        import yaml
    except ImportError:
        result.checks.append(CheckResult(False, "YAML parser", "pyyaml not installed"))
        return result

    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        result.checks.append(CheckResult(False, "YAML parse", str(e)[:80]))
        return result

    if not isinstance(manifest, dict):
        result.checks.append(CheckResult(False, "YAML parse", "Root must be a mapping"))
        return result
    result.checks.append(CheckResult(True, "YAML parse", "Valid"))

    _check_required_fields(manifest, result)

    if manifest.get("package_type") == "skill":
        _check_skill_files(manifest, path, result)
        _check_risk_preview(manifest, result)
        return result

    _check_verification(manifest, path, result)
    _check_risk_preview(manifest, result)

    return result


def _check_required_fields(manifest: dict, result: ValidateResult) -> None:
    pkg_id = manifest.get("package_id", "")
    result.package_id = pkg_id
    if not pkg_id or not SLUG_PATTERN.match(pkg_id):
        result.checks.append(CheckResult(False, "package_id", f"Invalid: '{pkg_id}'"))
    else:
        result.checks.append(CheckResult(True, "package_id", pkg_id))

    version = manifest.get("version", "")
    result.version = version
    if not version or not SEMVER_PATTERN.match(version):
        result.checks.append(CheckResult(False, "version", f"Invalid semver: '{version}'"))
    else:
        result.checks.append(CheckResult(True, "version", version))

    pkg_type = manifest.get("package_type", "")
    result.package_type = pkg_type
    if pkg_type not in ("toolpack", "agent", "upgrade", "skill"):
        result.checks.append(CheckResult(False, "package_type", f"Invalid: '{pkg_type}'"))
    else:
        result.checks.append(CheckResult(True, "package_type", pkg_type))

    summary = manifest.get("summary", "")
    if not summary or len(summary) < 20:
        result.checks.append(CheckResult(False, "summary", f"Too short ({len(summary)} chars, min 20)"))
    elif len(summary) > 200:
        result.checks.append(CheckResult(False, "summary", f"Too long ({len(summary)} chars, max 200)"))
    else:
        result.checks.append(CheckResult(True, "summary", f"{len(summary)} chars"))

    if pkg_type == "toolpack":
        caps = manifest.get("capabilities", {})
        tools = caps.get("tools", []) if isinstance(caps, dict) else []
        tools_with_cap = [t for t in tools if isinstance(t, dict) and t.get("capability_id")]
        if not tools_with_cap:
            result.checks.append(CheckResult(False, "tools", "No tools with capability_id"))
        else:
            result.checks.append(CheckResult(True, "tools", f"{len(tools_with_cap)} tool(s) with capability IDs"))

    if pkg_type == "skill":
        caps = manifest.get("capabilities", {})
        prompts = caps.get("prompts", []) if isinstance(caps, dict) else []
        valid_prompts = [p for p in prompts if isinstance(p, dict) and p.get("name") and p.get("template")]
        if not valid_prompts:
            result.checks.append(CheckResult(False, "prompts", "Skills require at least 1 prompt with name and template"))
        else:
            result.checks.append(CheckResult(True, "prompts", f"{len(valid_prompts)} prompt(s)"))

        assets = manifest.get("assets", [])
        if assets and isinstance(assets, list):
            _check_assets(assets, result)
        elif assets:
            result.checks.append(CheckResult(False, "assets", "assets must be an array"))


ASSET_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
VALID_ASSET_TYPES = {"document", "data", "template"}


def _check_skill_files(manifest: dict, path: Path, result: ValidateResult) -> None:
    """Validate skill-specific files: SKILL.md and asset files on disk."""
    skill_md = path / "SKILL.md"
    if not skill_md.exists():
        result.checks.append(CheckResult(False, "SKILL.md", "Required file not found"))
    else:
        size = skill_md.stat().st_size
        if size == 0:
            result.checks.append(CheckResult(False, "SKILL.md", "File is empty"))
        else:
            result.checks.append(CheckResult(True, "SKILL.md", f"Found ({size} bytes)"))

    assets = manifest.get("assets", [])
    if isinstance(assets, list):
        for i, asset in enumerate(assets):
            if not isinstance(asset, dict):
                continue
            asset_path = asset.get("path", "")
            if asset_path:
                full = path / asset_path
                if not full.exists():
                    result.checks.append(CheckResult(False, f"assets[{i}].path", f"File not found: {asset_path}"))

    result.max_tier = "verified"
    result.verification_mode = "static"
    result.checks.append(CheckResult(True, "verification", "Skills use static validation only"))


def _check_assets(assets: list, result: ValidateResult) -> None:
    """Validate assets[] entries in the manifest."""
    seen_ids: set[str] = set()
    valid = 0
    for i, asset in enumerate(assets):
        if not isinstance(asset, dict):
            result.checks.append(CheckResult(False, f"assets[{i}]", "Must be an object"))
            continue

        asset_id = asset.get("id", "")
        if not asset_id:
            result.checks.append(CheckResult(False, f"assets[{i}].id", "Required"))
            continue
        if not ASSET_ID_RE.match(asset_id):
            result.checks.append(CheckResult(False, f"assets[{i}].id", f"Invalid format: '{asset_id}'"))
            continue
        if asset_id in seen_ids:
            result.checks.append(CheckResult(False, f"assets[{i}].id", f"Duplicate: '{asset_id}'"))
            continue
        seen_ids.add(asset_id)

        asset_type = asset.get("type", "")
        if asset_type not in VALID_ASSET_TYPES:
            result.checks.append(CheckResult(False, f"assets[{i}].type", f"Invalid: '{asset_type}'"))
            continue

        path = asset.get("path", "")
        if not path:
            result.checks.append(CheckResult(False, f"assets[{i}].path", "Required"))
            continue
        if ".." in path or path.startswith("/") or path.startswith("\\"):
            result.checks.append(CheckResult(False, f"assets[{i}].path", f"Invalid path: '{path}'"))
            continue

        valid += 1

    if valid > 0:
        result.checks.append(CheckResult(True, "assets", f"{valid} valid asset(s)"))


def _check_verification(manifest: dict, path: Path, result: ValidateResult) -> None:
    pkg_type = manifest.get("package_type", "toolpack")

    if pkg_type == "agent":
        _check_agent_verification(manifest, result)
        return

    verification = manifest.get("verification")
    if not verification or not isinstance(verification, dict):
        result.max_tier = "verified"
        result.verification_mode = "real_auto"
        result.missing_items.append("No verification section defined")
        result.checks.append(CheckResult(False, "verification.cases", "Not defined — max tier: Verified"))
        return

    cases = verification.get("cases")
    fixtures = verification.get("fixtures")
    test_input = verification.get("test_input")

    if cases and isinstance(cases, list):
        _check_cases(cases, path, result)
    elif fixtures and isinstance(fixtures, list):
        result.cases_count = len(fixtures)
        result.verification_mode = "fixture"
        result.max_tier = "gold" if len(fixtures) >= 2 else "verified"
        result.checks.append(CheckResult(True, "verification.fixtures", f"{len(fixtures)} fixture(s) (legacy format)"))
        if len(fixtures) < 2:
            result.missing_items.append("Only 1 fixture — at least 2 for Gold")
    elif test_input and isinstance(test_input, dict):
        result.cases_count = 1
        result.verification_mode = "cases_real"
        result.max_tier = "verified"
        result.missing_items.append("test_input provides only 1 implicit case — use verification.cases for Gold")
        result.checks.append(CheckResult(True, "verification.test_input", "Legacy format — max tier: Verified"))
    else:
        result.max_tier = "verified"
        result.verification_mode = "real_auto"
        result.missing_items.append("No verification.cases defined")
        result.checks.append(CheckResult(False, "verification.cases", "Not defined — max tier: Verified"))

    sys_reqs = verification.get("system_requirements", [])
    if sys_reqs and isinstance(sys_reqs, list):
        unknown = [r for r in sys_reqs if r not in KNOWN_SYSTEM_REQUIREMENTS]
        if unknown:
            result.checks.append(CheckResult(True, "system_requirements", f"Warning: unknown {unknown}"))

    _check_manifest_in(path, result)


def _check_cases(cases: list, path: Path, result: ValidateResult) -> None:
    result.cases_count = len(cases)
    has_fixture = False
    valid_cases = 0

    for i, case in enumerate(cases):
        if not isinstance(case, dict):
            continue

        name = case.get("name", "")
        if not name or len(name) < 3:
            result.checks.append(CheckResult(False, f"cases[{i}].name", "Required, min 3 chars"))
            continue

        c_input = case.get("input")
        if not isinstance(c_input, dict):
            result.checks.append(CheckResult(False, f"cases[{i}].input", "Required, must be object"))
            continue

        cassette = case.get("cassette")
        if cassette:
            has_fixture = True
            if ".." in cassette or cassette.startswith("/"):
                result.checks.append(CheckResult(False, f"cases[{i}].cassette", "Path traversal not allowed"))
                continue
            if not CASSETTE_PATH_RE.match(cassette):
                result.checks.append(CheckResult(False, f"cases[{i}].cassette", f"Must match fixtures/cassettes/<name>.yaml"))
                continue
            cassette_path = path / cassette
            if not cassette_path.exists():
                result.missing_items.append(f"Cassette file '{cassette}' not found in package")
                result.checks.append(CheckResult(False, f"cases[{i}].cassette", f"File not found: {cassette}"))
                continue

        expected = case.get("expected")
        if not expected:
            result.missing_items.append(f"Case '{name}' has no expected output contract")

        valid_cases += 1

    result.verification_mode = "fixture" if has_fixture else "cases_real"
    result.max_tier = "gold" if valid_cases >= 2 else "verified"

    if valid_cases < 2:
        result.missing_items.append("Fewer than 2 valid cases — at least 2 for Gold")

    label = f"{valid_cases} valid case(s)"
    if has_fixture:
        label += " (fixture mode)"
    result.checks.append(CheckResult(True, "verification.cases", label))


def _check_agent_verification(manifest: dict, result: ValidateResult) -> None:
    agent = manifest.get("agent", {})
    if not isinstance(agent, dict):
        agent = {}

    verification = agent.get("verification", {})
    if not isinstance(verification, dict):
        verification = {}

    cases = verification.get("cases", [])
    if not isinstance(cases, list):
        cases = []

    result.cases_count = len(cases)
    result.verification_mode = "cases_real"

    if len(cases) == 0:
        result.max_tier = "verified"
        result.missing_items.append("No agent verification cases defined")
        result.checks.append(CheckResult(False, "agent.verification.cases", "Not defined — max tier: Verified"))
    elif len(cases) < 2:
        result.max_tier = "verified"
        result.missing_items.append("Only 1 case — at least 2 for agent Gold")
        result.checks.append(CheckResult(True, "agent.verification.cases", "1 case (need 2 for Gold)"))
    else:
        result.max_tier = "gold"
        result.checks.append(CheckResult(True, "agent.verification.cases", f"{len(cases)} cases — Gold-eligible"))


def _check_manifest_in(path: Path, result: ValidateResult) -> None:
    has_cassettes = any(
        item.detail and "fixture" in item.detail.lower()
        for item in result.checks
        if item.passed
    ) or result.verification_mode == "fixture"

    if not has_cassettes:
        return

    manifest_in = path / "MANIFEST.in"
    if not manifest_in.exists():
        result.missing_items.append("MANIFEST.in not found — fixtures won't be included in artifact")
        result.checks.append(CheckResult(False, "MANIFEST.in", "Missing — fixtures won't be packaged"))
        return

    content = manifest_in.read_text(encoding="utf-8", errors="ignore")
    if "fixtures" not in content:
        result.missing_items.append("MANIFEST.in does not reference fixtures directory")
        result.checks.append(CheckResult(False, "MANIFEST.in", "Does not include fixtures"))
    else:
        result.checks.append(CheckResult(True, "MANIFEST.in", "Includes fixtures"))


# ---------------------------------------------------------------------------
# Usage Risk Preview
# ---------------------------------------------------------------------------

_NETWORK_LEVEL_MAP = {
    "unrestricted": "external",
    "restricted": "internal",
    "none": "none",
}


def _manifest_to_risk_entry(manifest: dict) -> dict:
    """Convert manifest permissions to lockfile-style entry for risk scoring."""
    perms = manifest.get("permissions") or {}

    net_raw = perms.get("network", {})
    net_level = net_raw.get("level", "none") if isinstance(net_raw, dict) else "none"

    fs_raw = perms.get("filesystem", {})
    fs_level = fs_raw.get("level", "none") if isinstance(fs_raw, dict) else "none"

    code_raw = perms.get("code_execution", {})
    code_level = code_raw.get("level", "none") if isinstance(code_raw, dict) else "none"

    caps = manifest.get("capabilities", {})
    tools = caps.get("tools", []) if isinstance(caps, dict) else []
    capability_ids = [
        t["capability_id"] for t in tools
        if isinstance(t, dict) and t.get("capability_id")
    ]

    raw_connector = manifest.get("connector")
    connector = None
    if isinstance(raw_connector, dict) and raw_connector.get("auth_type"):
        connector = {"auth_type": raw_connector["auth_type"]}

    return {
        "permissions": {
            "network_level": _NETWORK_LEVEL_MAP.get(net_level, net_level),
            "filesystem_level": fs_level,
            "code_execution_level": code_level,
        },
        "trust_level": "preview",
        "connector": connector,
        "capability_ids": capability_ids,
        "_manifest_permissions_declared": bool(manifest.get("permissions")),
        "_connector_declared": bool(raw_connector),
        "_connector_scopes": (
            raw_connector.get("scopes", [])
            if isinstance(raw_connector, dict) else []
        ),
    }


def _check_risk_preview(manifest: dict, result: ValidateResult) -> None:
    """Compute usage risk preview from manifest data."""
    from agentnode_sdk.risk_profile import compute_risk_profile

    entry = _manifest_to_risk_entry(manifest)
    slug = manifest.get("package_id", "unknown")
    profile = compute_risk_profile(slug, entry)

    result.risk_level = profile.risk_level
    result.risk_score = profile.risk_score
    result.risk_signals = profile.signals
    result.risk_flags = profile.risk_flags
    result.risk_hints = _build_risk_hints(profile, entry)

    raw_connector = manifest.get("connector")
    if isinstance(raw_connector, dict):
        result.connector_provider = raw_connector.get("provider", "")
        result.connector_auth_type = raw_connector.get("auth_type", "")
        scopes = raw_connector.get("scopes")
        result.connector_scopes = scopes if isinstance(scopes, list) else []

    _check_connector_warning(entry, result)


def _build_risk_hints(profile, entry: dict) -> list[str]:
    """Generate actionable hints for publishers."""
    from agentnode_sdk.risk_profile import RiskProfile

    hints: list[str] = []

    if "external_write_capable" in profile.risk_flags:
        hints.append(
            "Package will be flagged as external_write_capable "
            "— users can configure risk policies for this flag"
        )

    perms = entry.get("permissions") or {}
    if perms.get("network_level") in ("external", "full"):
        hints.append(
            "Declare network permissions accurately "
            "— overly broad declarations increase risk score"
        )

    if not entry.get("_manifest_permissions_declared"):
        hints.append(
            "No permissions declared "
            "— risk scoring will use conservative defaults"
        )

    scopes = entry.get("_connector_scopes", [])
    if entry.get("_connector_declared"):
        write_scopes = [s for s in scopes if ":write" in s or s.startswith("write")]
        if write_scopes:
            hints.append(
                f"Connector declares write scopes ({', '.join(write_scopes)}) "
                "— contributes to external_write_capable detection"
            )

    if profile.risk_level == "high":
        hints.append(
            "High usage risk "
            "— consider whether all declared permissions are necessary"
        )

    hints.append(
        "Trust level is assigned after publish "
        "— not included in preview score"
    )

    return hints


def _check_connector_warning(entry: dict, result: ValidateResult) -> None:
    """Warn when external network is declared without connector metadata."""
    perms = entry.get("permissions") or {}
    net = perms.get("network_level", "none")
    if net in ("external", "full") and entry.get("connector") is None:
        result.warnings.append(
            "Missing connector metadata: package uses external network access "
            "but does not declare a connector. This may lead to inaccurate risk "
            "scoring and missing flags like external_write_capable. "
            "Add a connector block to improve risk detection."
        )
