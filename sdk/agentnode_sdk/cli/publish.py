"""agentnode publish — publish a package to the AgentNode registry."""
from __future__ import annotations

import io
import json
import os
import sys
import tarfile
from pathlib import Path

from agentnode_sdk.cli.output import bold, dim, kv, section

PUBLISH_EXCLUDE_DIRS = {
    "__pycache__", ".git", ".venv", ".egg-info",
    ".pytest_cache", "node_modules", ".mypy_cache",
    ".ruff_cache", ".tox", ".DS_Store",
}

PUBLISH_EXCLUDE_SUFFIXES = {".pyc", ".pyo"}

PUBLISH_EXCLUDE_FILES = {".env"}

MAX_SINGLE_FILE_WARN_BYTES = 10 * 1024 * 1024

SKILL_ALLOWED_EXTENSIONS = frozenset({
    ".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".xml",
    ".html", ".css", ".svg", ".png", ".jpg", ".jpeg", ".webp",
})

SKILL_FORBIDDEN_FILES = frozenset({
    "package.json", "pyproject.toml", "setup.py", "setup.cfg",
    "makefile", "dockerfile", "cargo.toml", "go.mod", "go.sum",
    "gemfile", "rakefile", "cmakelists.txt", "meson.build",
    "requirements.txt", "pipfile", "poetry.lock",
})


def _should_exclude(entry: tarfile.TarInfo) -> bool:
    """Check if a tar entry should be excluded from the artifact."""
    p = Path(entry.name)
    if any(part in PUBLISH_EXCLUDE_DIRS for part in p.parts):
        return True
    if p.suffix in PUBLISH_EXCLUDE_SUFFIXES:
        return True
    if p.name in PUBLISH_EXCLUDE_FILES:
        return True
    return False


def _skill_allowed_files(pkg_path: Path) -> set[str] | None:
    """Compute the set of allowed relative paths for a skill package.

    Returns None if the package is not a skill (caller should skip filtering).
    """
    manifest_path = pkg_path / "agentnode.yaml"
    if not manifest_path.is_file():
        return None

    try:
        import yaml
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if not isinstance(manifest, dict):
        return None
    if manifest.get("package_type") != "skill":
        return None

    allowed: set[str] = {"agentnode.yaml", "SKILL.md"}

    for asset in manifest.get("assets", []):
        if isinstance(asset, dict) and asset.get("path"):
            allowed.add(asset["path"])

    caps = manifest.get("capabilities", {})
    for prompt in caps.get("prompts", []) if isinstance(caps, dict) else []:
        if isinstance(prompt, dict) and prompt.get("template"):
            allowed.add(prompt["template"])

    return allowed


def _build_artifact(pkg_path: Path, arcname: str) -> tuple[bytes, int]:
    """Build a tar.gz artifact from a package directory.

    Returns (artifact_bytes, file_count).
    Excludes symlinks, absolute paths, and path traversal entries.
    For skill packages, only declared files with allowed extensions are included.
    """
    skill_allowed = _skill_allowed_files(pkg_path)
    skipped_skill_files: list[str] = []

    buf = io.BytesIO()
    file_count = 0
    large_files: list[str] = []

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for item in sorted(pkg_path.rglob("*")):
            if item.is_symlink():
                continue
            rel = item.relative_to(pkg_path)
            rel_posix = rel.as_posix()
            entry_name = f"{arcname}/{rel_posix}"

            info = tarfile.TarInfo(name=entry_name)
            if _should_exclude(info):
                continue

            if ".." in rel.parts:
                continue

            if item.is_dir():
                if skill_allowed is not None:
                    # Only include directories that are parents of allowed files
                    if not any(a.startswith(rel_posix + "/") for a in skill_allowed):
                        continue
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            elif item.is_file():
                if skill_allowed is not None:
                    if rel_posix not in skill_allowed:
                        skipped_skill_files.append(rel_posix)
                        continue
                    basename = item.name.lower()
                    if basename in SKILL_FORBIDDEN_FILES:
                        skipped_skill_files.append(rel_posix)
                        continue
                    ext = item.suffix.lower()
                    if ext and ext not in SKILL_ALLOWED_EXTENSIONS:
                        skipped_skill_files.append(rel_posix)
                        continue

                data = item.read_bytes()
                info.size = len(data)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(data))
                file_count += 1
                if len(data) > MAX_SINGLE_FILE_WARN_BYTES:
                    large_files.append(f"{rel} ({len(data) / 1024 / 1024:.1f} MB)")

    if skipped_skill_files:
        print(
            f"  Skill boundary: {len(skipped_skill_files)} file(s) excluded "
            "(not declared in manifest or forbidden extension):",
            file=sys.stderr,
        )
        for sf in skipped_skill_files[:10]:
            print(f"    - {sf}", file=sys.stderr)
        if len(skipped_skill_files) > 10:
            print(f"    ... and {len(skipped_skill_files) - 10} more", file=sys.stderr)

    if large_files:
        for lf in large_files:
            print(f"  Warning: large file in artifact: {lf}", file=sys.stderr)

    return buf.getvalue(), file_count


def _resolve_api_base() -> str:
    """Resolve API base URL using the same source as AgentNodeClient."""
    from agentnode_sdk.client import DEFAULT_BASE_URL
    return os.environ.get("AGENTNODE_API_URL", DEFAULT_BASE_URL).rstrip("/")


def _post_publish(
    manifest_json: str,
    artifact_bytes: bytes,
    api_key: str,
) -> dict:
    """POST multipart publish request. Returns response dict or raises."""
    import httpx

    api_base = _resolve_api_base()
    url = f"{api_base}/v1/packages/publish"

    slug = json.loads(manifest_json).get("package_id", "package")
    resp = httpx.post(
        url,
        headers={"X-API-Key": api_key},
        data={"manifest": manifest_json},
        files={"artifact": (f"{slug}.tar.gz", artifact_bytes, "application/gzip")},
        timeout=120,
    )

    if resp.status_code == 201:
        return resp.json()

    try:
        data = resp.json()
        err = data.get("error", {})
        code = err.get("code", "UNKNOWN")
        message = err.get("message", resp.text[:200])
        details = err.get("details", [])
    except (ValueError, KeyError, TypeError):
        code = "UNKNOWN"
        message = resp.text[:200]
        details = []

    return {
        "_error": True,
        "status": resp.status_code,
        "code": code,
        "message": message,
        "details": details,
    }


def cmd_publish(
    path_str: str,
    *,
    dry_run: bool = False,
    skip_validate: bool = False,
    token: str | None = None,
) -> int:
    """Publish a package to the AgentNode registry."""
    from agentnode_sdk.cli.validate import validate_package_dir

    pkg_path = Path(path_str).resolve()
    if not pkg_path.is_dir():
        print(f"  Error: '{path_str}' is not a directory.", file=sys.stderr)
        return 1

    manifest_path = pkg_path / "agentnode.yaml"
    if not manifest_path.exists():
        print("  Error: agentnode.yaml not found.", file=sys.stderr)
        return 1

    try:
        import yaml
    except ImportError:
        print("  Error: pyyaml is required. Install it with: pip install pyyaml", file=sys.stderr)
        return 1

    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  Error: Failed to parse agentnode.yaml: {e}", file=sys.stderr)
        return 1

    if not isinstance(manifest, dict):
        print("  Error: agentnode.yaml root must be a mapping.", file=sys.stderr)
        return 1

    pkg_id = manifest.get("package_id", "")
    version = manifest.get("version", "")
    pkg_type = manifest.get("package_type", "")

    if not pkg_id or not version:
        print("  Error: package_id and version are required in agentnode.yaml.", file=sys.stderr)
        return 1

    print()
    print(section("  AgentNode Publish"))
    print(kv("Package", f"{pkg_id}@{version}"))
    if pkg_type:
        print(kv("Type", pkg_type))
    print()

    result = validate_package_dir(pkg_path)
    passed = sum(1 for c in result.checks if c.passed)
    failed = sum(1 for c in result.checks if not c.passed)

    if failed == 0:
        print(f"  Validation    {passed} checks passed")
    else:
        print(f"  Validation    {passed} passed, {failed} failed")

    if result.has_errors and not skip_validate:
        print()
        print(bold("  Validation Errors"))
        for check in result.checks:
            if not check.passed:
                detail = f" — {check.detail}" if check.detail else ""
                print(f"  [FAIL] {check.label}{detail}")
        print()
        print("  Fix validation errors or use --skip-validate to continue.")
        return 1

    if result.has_errors and skip_validate:
        print(f"  Warning: {failed} validation error(s) ignored (--skip-validate)")

    artifact_bytes, file_count = _build_artifact(pkg_path, pkg_id)
    artifact_kb = len(artifact_bytes) / 1024
    if artifact_kb >= 1024:
        size_str = f"{artifact_kb / 1024:.1f} MB"
    else:
        size_str = f"{artifact_kb:.1f} KB"
    print(f"  Artifact      {size_str}, {file_count} files")
    print()

    if dry_run:
        print(dim("  Dry run — not publishing."))
        print(dim(f"  To publish: agentnode publish {path_str}"))
        print()
        return 0

    api_key = token or os.environ.get("AGENTNODE_API_KEY", "")
    if not api_key:
        print(
            "  Error: No AgentNode API key configured.\n\n"
            "  Create an account or sign in at https://agentnode.net,\n"
            "  then create an API key in your dashboard.\n\n"
            "  Set it locally with:\n"
            "    export AGENTNODE_API_KEY=...\n\n"
            "  or run:\n"
            f"    agentnode publish {path_str} --token <key>\n",
            file=sys.stderr,
        )
        return 1

    manifest_json = json.dumps(manifest, default=str)

    api_host = _resolve_api_base().replace("https://", "").replace("http://", "")
    print(f"  Publishing to {api_host}...")

    try:
        resp = _post_publish(manifest_json, artifact_bytes, api_key)
    except Exception as e:
        print(f"  Error: Connection failed: {e}", file=sys.stderr)
        return 1

    if resp.get("_error"):
        status = resp["status"]
        code = resp["code"]
        message = resp["message"]
        details = resp.get("details", [])

        if status == 409:
            print(f"\n  Error: Version {version} already exists for {pkg_id}.")
            print("  Bump the version in agentnode.yaml and try again.\n")
        elif status == 403:
            if "PUBLISHER" in code:
                print("\n  Error: You need a publisher profile to publish packages.")
                print("  Create one at https://agentnode.net/dashboard\n")
            elif "NOT_OWNED" in code:
                print(f"\n  Error: You don't own the package '{pkg_id}'.")
                print("  You can only publish new versions of packages you created.\n")
            else:
                print(f"\n  Error: {message}\n")
        elif status == 413:
            print(f"\n  Error: Artifact too large. {message}\n")
        elif status == 422:
            print(f"\n  Error: {message}")
            if details:
                for d in details[:10]:
                    print(f"    - {d}")
            print()
        elif status == 429:
            print("\n  Error: Rate limited. Try again in a minute.\n")
        elif status == 401:
            print("\n  Error: Invalid API key.")
            print("  Check your AGENTNODE_API_KEY or --token value.\n")
        else:
            print(f"\n  Error ({status}): {message}\n")
        return 1

    slug = resp.get("slug", pkg_id)
    ver = resp.get("version", version)
    msg = resp.get("message", "")

    print(f"  Published {slug}@{ver}")
    if "warning" in msg.lower():
        print(f"  {msg}")
    print()
    print(f"  https://agentnode.net/packages/{slug}")
    print()
    return 0
