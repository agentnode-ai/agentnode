#!/usr/bin/env python3
"""MCP Candidate Verification Pipeline.

Discovers MCP servers from awesome-mcp-servers, resolves npm packages,
extracts metadata, and scores candidates for review.

No DB writes. No auto-seeding. No trust upgrades. Review output only.

Architecture: npm-first. Cheap checks (raw.githubusercontent.com, npmjs.org)
run before expensive checks (GitHub API). GitHub API is only called for
candidates that have a confirmed npm package.

Usage:
    python scripts/verify_mcp_candidates.py -o mcp_candidates.json
    python scripts/verify_mcp_candidates.py -o mcp_candidates.json --test --max 50
    python scripts/verify_mcp_candidates.py -o mcp_candidates.json --github-token ghp_xxx
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

AWESOME_URL = "https://raw.githubusercontent.com/punkpeye/awesome-mcp-servers/main/README.md"
NPM_REGISTRY = "https://registry.npmjs.org"
PYPI_REGISTRY = "https://pypi.org/pypi"
CACHE_DIR = Path(__file__).parent / ".mcp_cache"

PERMISSIVE_LICENSES = {
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC",
    "0BSD", "Unlicense", "CC0-1.0", "MPL-2.0", "Zlib",
}

EXISTING_SLUGS = {
    "mcp-brave-search", "mcp-filesystem", "mcp-github", "mcp-google-drive",
    "mcp-memory", "mcp-postgres", "mcp-puppeteer", "mcp-sequential-thinking",
    "mcp-slack", "mcp-sqlite",
}

ENV_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,}_(?:KEY|TOKEN|SECRET|PASSWORD|URL|ID|ENDPOINT))\b")
NPX_CMD_RE = re.compile(r"npx\s+(?:-y\s+)?([^\s]+)")
UVX_CMD_RE = re.compile(r"uvx\s+(?:--from\s+(\S+)\s+)?(\S+)")
PIP_INSTALL_RE = re.compile(r"pip\s+install\s+(\S+)")
PYPROJECT_NAME_RE = re.compile(r'^\s*name\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
PYPROJECT_SCRIPTS_RE = re.compile(r'\[(?:project\.scripts|tool\.poetry\.scripts)\](.*?)(?:\[|\Z)', re.DOTALL)

DANGEROUS_SCRIPTS = {"preinstall", "postinstall", "install"}

# ---------------------------------------------------------------------------
# Pre-filters: exclude repos that are clearly not MCP servers
# ---------------------------------------------------------------------------

EXCLUDE_REPO_PATTERNS = [
    re.compile(r"^awesome-", re.IGNORECASE),
    re.compile(r"^mcp[-_]?clients?$", re.IGNORECASE),
    re.compile(r"^(?:docs?|documentation|examples?|templates?|tutorials?|demos?|samples?|starters?)$", re.IGNORECASE),
    re.compile(r"[-_](?:docs?|examples?|templates?|tutorial)$", re.IGNORECASE),
    re.compile(r"^(?:\.github|\.devcontainer)$", re.IGNORECASE),
]

EXCLUDE_REPOS_EXACT = {
    "punkpeye/awesome-mcp-servers",
    "punkpeye/awesome-mcp-clients",
    "modelcontextprotocol/modelcontextprotocol",
    "modelcontextprotocol/specification",
    "modelcontextprotocol/docs",
    "modelcontextprotocol/typescript-sdk",
    "modelcontextprotocol/python-sdk",
    "modelcontextprotocol/kotlin-sdk",
    "modelcontextprotocol/java-sdk",
    "modelcontextprotocol/inspector",
    "modelcontextprotocol/create-python-server",
    "modelcontextprotocol/create-typescript-server",
}

EXCLUDE_DESCRIPTION_PATTERNS = [
    re.compile(r"\bclient\s+(?:for|of|to)\b.*\bmcp\b", re.IGNORECASE),
    re.compile(r"\bmcp\s+client\b", re.IGNORECASE),
    re.compile(r"\bcurated\s+list\b", re.IGNORECASE),
    re.compile(r"\bawesome\s+list\b", re.IGNORECASE),
]


def _is_excluded_repo(owner: str, repo: str) -> str | None:
    full = f"{owner}/{repo}"
    if full.lower() in {r.lower() for r in EXCLUDE_REPOS_EXACT}:
        return f"excluded_exact: {full}"
    for pat in EXCLUDE_REPO_PATTERNS:
        if pat.search(repo):
            return f"excluded_pattern: {pat.pattern}"
    return None


def _is_excluded_by_description(description: str | None) -> str | None:
    if not description:
        return None
    for pat in EXCLUDE_DESCRIPTION_PATTERNS:
        if pat.search(description):
            return f"excluded_description: {pat.pattern}"
    return None


_http = httpx.Client(timeout=15, follow_redirects=True)
_github_headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_path(kind: str, key: str) -> Path:
    safe_key = re.sub(r"[^a-zA-Z0-9._-]", "_", key)
    return CACHE_DIR / kind / f"{safe_key}.json"


def _cache_get(kind: str, key: str, max_age_hours: int = 24) -> dict | None:
    p = _cache_path(kind, key)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(data.get("_cached_at", "2000-01-01T00:00:00+00:00"))
        age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
        if age_hours > max_age_hours:
            return None
        return data
    except Exception:
        return None


def _cache_put(kind: str, key: str, data: dict) -> None:
    p = _cache_path(kind, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    data["_cached_at"] = datetime.now(timezone.utc).isoformat()
    p.write_text(json.dumps(data, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Stage 1: Crawl + name filter
# ---------------------------------------------------------------------------

_filter_log: list[dict] = []


def crawl_awesome() -> list[dict]:
    """Fetch awesome-mcp-servers and extract GitHub repos."""
    print("Stage 1: Crawling awesome-mcp-servers...")
    resp = _http.get(AWESOME_URL)
    resp.raise_for_status()
    readme = resp.text

    pattern = re.compile(r"https://github\.com/([a-zA-Z0-9._-]+)/([a-zA-Z0-9._-]+)")
    seen: set[str] = set()
    candidates = []
    filtered = {"exact": 0, "pattern": 0}

    for m in pattern.finditer(readme):
        owner, repo = m.group(1), m.group(2)
        url = f"https://github.com/{owner}/{repo}"
        url = url.rstrip("/").split("#")[0].split("?")[0]
        if url in seen:
            continue
        seen.add(url)

        reason = _is_excluded_repo(owner, repo)
        if reason:
            kind = "exact" if "excluded_exact" in reason else "pattern"
            filtered[kind] += 1
            _filter_log.append({"repo": f"{owner}/{repo}", "reason": reason})
            continue

        slug = re.sub(r"[^a-z0-9-]", "-", repo.lower()).strip("-")
        if not slug.startswith("mcp-") and "mcp" not in slug:
            slug = f"mcp-{slug}"

        candidates.append({
            "slug": slug,
            "owner": owner,
            "repo": repo,
            "source_repo": url,
        })

    total_seen = len(candidates) + filtered["exact"] + filtered["pattern"]
    print(f"  Found {total_seen} unique repos")
    print(f"  Filtered: {filtered['exact']} exact + {filtered['pattern']} pattern = {sum(filtered.values())} removed")
    print(f"  Remaining: {len(candidates)} candidates")
    return candidates


# ---------------------------------------------------------------------------
# Stage 2: npm resolve (cheap — raw.githubusercontent.com + npmjs.org)
# ---------------------------------------------------------------------------

def resolve_npm(candidate: dict) -> None:
    """Try to find and resolve the npm package. No GitHub API calls."""
    owner, repo = candidate["owner"], candidate["repo"]

    pkg_json_url = f"https://raw.githubusercontent.com/{owner}/{repo}/main/package.json"
    npm_name = None

    try:
        resp = _http.get(pkg_json_url)
        if resp.status_code == 404:
            pkg_json_url = f"https://raw.githubusercontent.com/{owner}/{repo}/master/package.json"
            resp = _http.get(pkg_json_url)

        if resp.status_code == 200:
            pkg_data = resp.json()
            npm_name = pkg_data.get("name")
            candidate["_pkg_json"] = {
                "scripts": pkg_data.get("scripts", {}),
                "dependencies": list(pkg_data.get("dependencies", {}).keys()),
                "dep_count": len(pkg_data.get("dependencies", {})),
            }

            dangerous = set(pkg_data.get("scripts", {}).keys()) & DANGEROUS_SCRIPTS
            if dangerous:
                candidate["issues"] = candidate.get("issues", []) + [
                    f"dangerous_scripts: {', '.join(dangerous)}"
                ]
    except Exception:
        pass

    if not npm_name:
        candidate["npm_exists"] = False
        candidate["issues"] = candidate.get("issues", []) + ["no_package_json"]
        return

    try:
        resp = _http.get(f"{NPM_REGISTRY}/{npm_name}")
        if resp.status_code == 404:
            candidate["npm_package"] = npm_name
            candidate["npm_exists"] = False
            candidate["issues"] = candidate.get("issues", []) + ["npm_not_found"]
            return

        resp.raise_for_status()
        npm_data = resp.json()
    except Exception as e:
        candidate["npm_package"] = npm_name
        candidate["npm_exists"] = False
        candidate["issues"] = candidate.get("issues", []) + [f"npm_error: {e}"]
        return

    candidate["npm_package"] = npm_name
    candidate["npm_exists"] = True

    dist_tags = npm_data.get("dist-tags", {})
    latest = dist_tags.get("latest")
    candidate["npm_version"] = latest

    if latest and latest in npm_data.get("versions", {}):
        ver_data = npm_data["versions"][latest]
        dist = ver_data.get("dist", {})
        candidate["npm_shasum"] = dist.get("shasum")
        candidate["npm_integrity"] = dist.get("integrity")

    maintainers = npm_data.get("maintainers", [])
    candidate["npm_maintainers"] = [
        m.get("name", "") for m in maintainers if isinstance(m, dict)
    ]

    time_info = npm_data.get("time", {})
    candidate["npm_created"] = time_info.get("created")
    candidate["npm_modified"] = time_info.get("modified")

    if latest:
        candidate["command"] = ["npx", "-y", f"{npm_name}@{latest}"]


# ---------------------------------------------------------------------------
# Stage 2b: Python/uvx resolve (cheap — raw.githubusercontent.com + pypi.org)
# Only called when npm resolution found nothing.
# ---------------------------------------------------------------------------

def resolve_python(candidate: dict) -> None:
    """Try to find a Python/uvx package. No GitHub API calls."""
    owner, repo = candidate["owner"], candidate["repo"]

    pypi_name = None
    entry_points: list[str] = []

    for branch in ("main", "master"):
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/pyproject.toml"
        try:
            resp = _http.get(url)
            if resp.status_code != 200:
                continue

            toml_text = resp.text
            candidate["python_runtime"] = True

            name_match = PYPROJECT_NAME_RE.search(toml_text)
            if name_match:
                pypi_name = name_match.group(1)

            scripts_match = PYPROJECT_SCRIPTS_RE.search(toml_text)
            if scripts_match:
                scripts_block = scripts_match.group(1)
                for line in scripts_block.strip().splitlines():
                    line = line.strip()
                    if "=" in line and not line.startswith("#") and not line.startswith("["):
                        ep_name = line.split("=")[0].strip().strip('"').strip("'")
                        if ep_name:
                            entry_points.append(ep_name)
            break
        except Exception:
            continue

    if not pypi_name and not candidate.get("python_runtime"):
        for branch in ("main", "master"):
            for filename in ("setup.py", "setup.cfg"):
                url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{filename}"
                try:
                    resp = _http.get(url)
                    if resp.status_code == 200:
                        candidate["python_runtime"] = True
                        name_m = re.search(r'name\s*=\s*["\']([^"\']+)["\']', resp.text)
                        if name_m:
                            pypi_name = name_m.group(1)
                        break
                except Exception:
                    continue
            if candidate.get("python_runtime"):
                break

    if not candidate.get("python_runtime"):
        return

    readme_text = candidate.get("_readme_text", "")
    if not readme_text:
        for branch in ("main", "master"):
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md"
            try:
                resp = _http.get(url)
                if resp.status_code == 200:
                    readme_text = resp.text
                    break
            except Exception:
                continue

    uvx_from_readme = None
    if readme_text:
        uvx_match = UVX_CMD_RE.search(readme_text)
        if uvx_match:
            uvx_from_readme = uvx_match.group(1) or uvx_match.group(2)

        if not pypi_name:
            pip_match = PIP_INSTALL_RE.search(readme_text)
            if pip_match:
                raw = pip_match.group(1)
                pypi_name = re.split(r"[>=<\[!]", raw)[0].strip()

    if not pypi_name:
        candidate["pypi_exists"] = False
        candidate["issues"] = candidate.get("issues", []) + ["no_pypi_name"]
        return

    try:
        resp = _http.get(f"{PYPI_REGISTRY}/{pypi_name}/json")
        if resp.status_code == 404:
            candidate["pypi_package"] = pypi_name
            candidate["pypi_exists"] = False
            candidate["issues"] = candidate.get("issues", []) + ["pypi_not_found"]
            return

        resp.raise_for_status()
        pypi_data = resp.json()
    except Exception as e:
        candidate["pypi_package"] = pypi_name
        candidate["pypi_exists"] = False
        candidate["issues"] = candidate.get("issues", []) + [f"pypi_error: {e}"]
        return

    candidate["pypi_package"] = pypi_name
    candidate["pypi_exists"] = True

    info = pypi_data.get("info", {})
    candidate["pypi_version"] = info.get("version")
    candidate["pypi_author"] = info.get("author") or info.get("maintainer") or ""
    candidate["pypi_license"] = info.get("license") or ""
    candidate["pypi_summary"] = info.get("summary") or ""

    if entry_points:
        candidate["python_entry_points"] = entry_points

    cmd_name = uvx_from_readme or (entry_points[0] if entry_points else pypi_name)
    version = candidate["pypi_version"]
    if version:
        candidate["command"] = ["uvx", f"{cmd_name}@{version}"]
    else:
        candidate["command"] = ["uvx", cmd_name]


# ---------------------------------------------------------------------------
# Stage 3: Extract env_keys from README (cheap — raw.githubusercontent.com)
# ---------------------------------------------------------------------------

def extract_env_keys(candidate: dict) -> None:
    """Extract likely env var names from the repo README."""
    owner, repo = candidate["owner"], candidate["repo"]

    for branch in ("main", "master"):
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md"
        try:
            resp = _http.get(url)
            if resp.status_code == 200:
                readme = resp.text
                keys = sorted(set(ENV_KEY_RE.findall(readme)))
                candidate["env_keys"] = keys
                return
        except Exception:
            continue

    candidate["env_keys"] = []


# ---------------------------------------------------------------------------
# Stage 4: GitHub metadata (expensive — rate-limited API)
# Only called for candidates that passed npm resolution.
# ---------------------------------------------------------------------------

_github_api_calls = 0


def enrich_github(candidate: dict) -> None:
    """Fetch GitHub repo metadata. Uses disk cache."""
    global _github_api_calls
    owner, repo = candidate["owner"], candidate["repo"]
    cache_key = f"{owner}_{repo}"

    cached = _cache_get("github", cache_key)
    if cached:
        cached.pop("_cached_at", None)
        candidate.update(cached)
        return

    url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        resp = _http.get(url, headers=_github_headers)
        _github_api_calls += 1

        if resp.status_code == 403:
            remaining = resp.headers.get("x-ratelimit-remaining", "?")
            reset = resp.headers.get("x-ratelimit-reset", "?")
            print(f"    Rate limited (remaining={remaining}, reset={reset}), sleeping 60s...")
            time.sleep(60)
            resp = _http.get(url, headers=_github_headers)
            _github_api_calls += 1

        if resp.status_code == 404:
            candidate["issues"] = candidate.get("issues", []) + ["repo_not_found"]
            return
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        candidate["issues"] = candidate.get("issues", []) + [f"github_error: {e}"]
        return

    gh_data = {
        "name": data.get("name", repo),
        "description": data.get("description", ""),
        "stars": data.get("stargazers_count", 0),
        "archived": data.get("archived", False),
        "last_push": data.get("pushed_at", ""),
        "license": (data.get("license") or {}).get("spdx_id", None),
    }

    if gh_data["archived"]:
        candidate["issues"] = candidate.get("issues", []) + ["repo_archived"]

    _cache_put("github", cache_key, gh_data)
    candidate.update(gh_data)


# ---------------------------------------------------------------------------
# Stage 5: Protocol test (optional)
# ---------------------------------------------------------------------------

def _resolve_command(command: list[str]) -> list[str]:
    """Resolve command to full path, handling Windows .cmd/.bat wrappers."""
    import shutil
    exe = command[0]
    resolved = shutil.which(exe)
    if resolved:
        return [resolved] + command[1:]
    return command


def protocol_test(candidate: dict) -> None:
    """Start the MCP server, do initialize + tools/list, stop."""
    command = candidate.get("command")
    if not command:
        return

    env_keys = candidate.get("env_keys", [])
    if env_keys:
        candidate["protocol_tested"] = False
        candidate["protocol_skip_reason"] = "requires_env_keys"
        return

    resolved_cmd = _resolve_command(command)

    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", tempfile.gettempdir())}
    if sys.platform == "win32":
        env["USERPROFILE"] = os.environ.get("USERPROFILE", "")
        env["APPDATA"] = os.environ.get("APPDATA", "")
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
        env["LOCALAPPDATA"] = os.environ.get("LOCALAPPDATA", "")

    try:
        proc = subprocess.Popen(
            resolved_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            cwd=tempfile.gettempdir(),
        )
    except Exception as e:
        candidate["protocol_tested"] = True
        candidate["protocol_passed"] = False
        candidate["protocol_error"] = f"start_failed: {e}"
        return

    import itertools
    _id = itertools.count(1)

    def send(msg):
        try:
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
        except Exception:
            pass

    def recv(timeout=10):
        import threading

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
        send({
            "jsonrpc": "2.0", "id": next(_id), "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "agentnode-verify", "version": "0.1.0"},
            },
        })
        resp = recv(timeout=15)
        if not resp or "error" in resp:
            candidate["protocol_tested"] = True
            candidate["protocol_passed"] = False
            candidate["protocol_error"] = f"initialize_failed: {resp}"
            return

        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        candidate["protocol_tested"] = True
        candidate["protocol_passed"] = True

        send({"jsonrpc": "2.0", "id": next(_id), "method": "tools/list", "params": {}})
        tools_resp = recv(timeout=10)
        if tools_resp and "result" in tools_resp:
            tools = tools_resp["result"].get("tools", [])
            candidate["tools_snapshot"] = [
                {
                    "name": t.get("name", ""),
                    "description": (t.get("description", "") or "")[:200],
                    "input_schema_keys": list(
                        t.get("inputSchema", {}).get("properties", {}).keys()
                    )[:10],
                }
                for t in tools[:50]
            ]
            candidate["tools_count"] = len(tools)
        else:
            candidate["tools_snapshot"] = []
            candidate["tools_count"] = 0

    except Exception as e:
        candidate["protocol_tested"] = True
        candidate["protocol_passed"] = False
        candidate["protocol_error"] = str(e)
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
# Stage 6: Confidence score
# ---------------------------------------------------------------------------

def compute_confidence(candidate: dict) -> str:
    issues = candidate.get("issues", [])

    if any(i in issues for i in ["repo_not_found", "repo_archived"]):
        return "REJECT"
    if candidate.get("license") and candidate["license"] not in PERMISSIVE_LICENSES and candidate["license"] != "NOASSERTION":
        return "REJECT"
    if any("dangerous_scripts" in i for i in issues):
        return "REJECT"

    has_package = candidate.get("npm_exists") or candidate.get("pypi_exists")
    if not has_package:
        return "LOW"

    if candidate.get("protocol_passed"):
        return "HIGH"

    if candidate.get("command"):
        if candidate.get("npm_version") or candidate.get("pypi_version"):
            return "MEDIUM"

    return "LOW"


# ---------------------------------------------------------------------------
# Stage 7: Output
# ---------------------------------------------------------------------------

def build_candidate_metadata(c: dict) -> dict | None:
    is_npm = c.get("npm_exists") and c.get("npm_version")
    is_pypi = c.get("pypi_exists") and c.get("pypi_version")
    if not is_npm and not is_pypi:
        return None

    tools = []
    for t in c.get("tools_snapshot", []):
        cap_id = "general"
        name_lower = t["name"].lower()
        for kw, cid in [("search", "web_search"), ("file", "file_read"), ("query", "database_query"),
                         ("read", "file_read"), ("write", "file_write"), ("list", "file_read"),
                         ("create", "data_storage"), ("delete", "data_storage"),
                         ("send", "messaging"), ("post", "messaging")]:
            if kw in name_lower:
                cap_id = cid
                break
        tools.append({
            "name": t["name"],
            "capability_id": cap_id,
            "description": t["description"][:100] if t["description"] else t["name"],
        })

    if not tools:
        tools = [{"name": "unknown", "capability_id": "general", "description": "Tool discovery pending"}]

    if is_npm:
        runtime = "node"
        package = c["npm_package"]
        version = c["npm_version"]
    else:
        runtime = "python"
        package = c["pypi_package"]
        version = c["pypi_version"]

    return {
        "slug": c["slug"],
        "name": c.get("name", c["slug"]),
        "runtime": runtime,
        "package": package,
        "pinned_version": version,
        "source_repo": c["source_repo"],
        "summary": (c.get("description") or c.get("pypi_summary") or f"MCP server: {c.get('name', c['slug'])}")[:200],
        "description": c.get("description") or "",
        "env_keys": c.get("env_keys", []),
        "tools": tools,
        "tags": ["mcp", "mcp-server"],
        "categories": ["general"],
    }


def write_review_md(candidates: list[dict], path: str, stats: dict) -> None:
    lines = [
        "# MCP Candidate Review",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Total candidates processed: {len(candidates)}",
        "",
        "## Pipeline Statistics",
        "",
        f"- Repos crawled: {stats.get('repos_crawled', '?')}",
        f"- Filtered by name (exact): {stats.get('filtered_exact', '?')}",
        f"- Filtered by name (pattern): {stats.get('filtered_pattern', '?')}",
        f"- Filtered by description: {stats.get('filtered_description', '?')}",
        f"- Candidates after filters: {stats.get('candidates_after_filter', '?')}",
        f"- npm packages found: {stats.get('npm_found', '?')}",
        f"- PyPI packages found: {stats.get('pypi_found', '?')}",
        f"- GitHub API calls: {stats.get('github_api_calls', '?')}",
        f"- GitHub cache hits: {stats.get('github_cache_hits', '?')}",
        "",
    ]

    if _filter_log:
        lines.append("## Filtered Repos (top 30)")
        lines.append("")
        lines.append("| Repo | Reason |")
        lines.append("|---|---|")
        for entry in _filter_log[:30]:
            lines.append(f"| {entry['repo']} | {entry['reason']} |")
        lines.append("")

    by_confidence = {"HIGH": [], "MEDIUM": [], "LOW": [], "REJECT": []}
    for c in candidates:
        by_confidence[c.get("confidence", "LOW")].append(c)

    for level in ["HIGH", "MEDIUM", "LOW", "REJECT"]:
        items = by_confidence[level]
        lines.append(f"## {level} ({len(items)})")
        lines.append("")

        if not items:
            lines.append("None.")
            lines.append("")
            continue

        lines.append("| Slug | Runtime | Package | Version | Stars | Tools | env_keys | Issues |")
        lines.append("|---|---|---|---|---|---|---|---|")

        for c in sorted(items, key=lambda x: x.get("stars", 0) or 0, reverse=True):
            if c.get("npm_exists"):
                runtime = "node"
                pkg = c.get("npm_package", "-")
                ver = c.get("npm_version", "-")
            elif c.get("pypi_exists"):
                runtime = "python"
                pkg = c.get("pypi_package", "-")
                ver = c.get("pypi_version", "-")
            elif c.get("python_runtime"):
                runtime = "python?"
                pkg = c.get("pypi_package", "-")
                ver = "-"
            else:
                runtime = "-"
                pkg = "-"
                ver = "-"
            stars = c.get("stars", "-")
            tools = c.get("tools_count", len(c.get("tools_snapshot", [])))
            env = len(c.get("env_keys", []))
            issues = "; ".join(c.get("issues", [])) or "-"
            lines.append(f"| {c['slug']} | {runtime} | {pkg} | {ver} | {stars} | {tools} | {env} | {issues} |")

        lines.append("")

    Path(path).write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main — npm-first pipeline
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="MCP Candidate Verification Pipeline")
    parser.add_argument("-o", "--output", default="mcp_candidates.json", help="JSON output path")
    parser.add_argument("--review", default="MCP_CANDIDATE_REVIEW.md", help="Review markdown path")
    parser.add_argument("--github-token", "-t", help="GitHub token for higher rate limits")
    parser.add_argument("--test", action="store_true", help="Enable protocol smoke test")
    parser.add_argument("--max", type=int, default=0, help="Max candidates to process (0=all)")
    parser.add_argument("--skip-existing", action="store_true", help="Skip already-seeded slugs")
    parser.add_argument("--cache-hours", type=int, default=24, help="GitHub cache TTL in hours")
    args = parser.parse_args()

    if args.github_token:
        _github_headers["Authorization"] = f"Bearer {args.github_token}"

    global _github_api_calls

    # --- Stage 1: Crawl + name filter ---
    candidates = crawl_awesome()
    if args.max > 0:
        candidates = candidates[:args.max]

    stats = {
        "repos_crawled": len(candidates) + len(_filter_log),
        "filtered_exact": sum(1 for e in _filter_log if "excluded_exact" in e["reason"]),
        "filtered_pattern": sum(1 for e in _filter_log if "excluded_pattern" in e["reason"]),
        "filtered_description": 0,
        "candidates_after_filter": len(candidates),
        "npm_found": 0,
        "pypi_found": 0,
        "github_api_calls": 0,
        "github_cache_hits": 0,
    }

    total = len(candidates)
    package_resolved = []
    desc_filtered = []

    for i, c in enumerate(candidates):
        slug = c["slug"]
        c["issues"] = []

        if args.skip_existing and slug in EXISTING_SLUGS:
            c["confidence"] = "SKIP"
            c["issues"].append("already_seeded")
            print(f"  [{i+1}/{total}] {slug}: SKIP (already seeded)")
            continue

        print(f"  [{i+1}/{total}] {c['owner']}/{c['repo']}")

        # --- Stage 2: npm resolve (cheap) ---
        resolve_npm(c)

        # --- Stage 2b: Python/uvx resolve if no npm ---
        if not c.get("npm_exists"):
            resolve_python(c)

        # --- Stage 3: env_keys (cheap) ---
        extract_env_keys(c)

        has_package = c.get("npm_exists") or c.get("pypi_exists")
        if not has_package:
            c["confidence"] = compute_confidence(c)
            runtime_hint = "python?" if c.get("python_runtime") else "no package"
            print(f"    -> {c['confidence']} ({runtime_hint})")
            continue

        if c.get("npm_exists"):
            stats["npm_found"] += 1
            print(f"    -> npm: {c.get('npm_package')}@{c.get('npm_version', '?')}")
        else:
            stats["pypi_found"] += 1
            print(f"    -> pypi: {c.get('pypi_package')}@{c.get('pypi_version', '?')}")

        package_resolved.append(c)

    # --- Stage 4: GitHub metadata — only for package-resolved candidates ---
    print(f"\nStage 4: GitHub metadata for {len(package_resolved)} package-resolved candidates "
          f"(npm={stats['npm_found']}, pypi={stats['pypi_found']})...")
    for i, c in enumerate(package_resolved):
        cache_key = f"{c['owner']}_{c['repo']}"
        had_cache = _cache_get("github", cache_key) is not None
        if had_cache:
            stats["github_cache_hits"] += 1

        enrich_github(c)

        if c.get("archived"):
            c["confidence"] = "REJECT"
            print(f"  [{i+1}/{len(package_resolved)}] {c['slug']}: REJECT (archived)")
            continue

        desc_reason = _is_excluded_by_description(c.get("description"))
        if desc_reason:
            c["confidence"] = "REJECT"
            c["issues"].append(desc_reason)
            stats["filtered_description"] += 1
            desc_filtered.append({"repo": f"{c['owner']}/{c['repo']}", "reason": desc_reason})
            print(f"  [{i+1}/{len(package_resolved)}] {c['slug']}: REJECT ({desc_reason})")
            continue

        # --- Stage 5: protocol test (optional) ---
        if args.test and c.get("command"):
            print(f"  [{i+1}/{len(package_resolved)}] {c['slug']}: testing protocol...")
            protocol_test(c)
            if c.get("protocol_passed"):
                print(f"    -> Protocol OK, {c.get('tools_count', 0)} tools")
            elif c.get("protocol_skip_reason"):
                print(f"    -> Skipped: {c['protocol_skip_reason']}")
            else:
                print(f"    -> Protocol FAILED: {c.get('protocol_error', '?')}")

        # --- Stage 6: confidence ---
        c["confidence"] = compute_confidence(c)

        if c["confidence"] in ("HIGH", "MEDIUM"):
            c["candidate_metadata"] = build_candidate_metadata(c)

        print(f"  [{i+1}/{len(package_resolved)}] {c['slug']}: {c['confidence']}")

        time.sleep(0.3)

    stats["github_api_calls"] = _github_api_calls

    # --- Stage 7: Output ---
    output_candidates = [c for c in candidates if c.get("confidence") != "SKIP"]
    for c in output_candidates:
        c.pop("owner", None)
        c.pop("repo", None)
        c.pop("_pkg_json", None)

    Path(args.output).write_text(
        json.dumps(output_candidates, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\nOutput: {args.output} ({len(output_candidates)} candidates)")

    _filter_log.extend(desc_filtered)
    write_review_md(output_candidates, args.review, stats)
    print(f"Review: {args.review}")

    counts = {}
    for c in output_candidates:
        conf = c.get("confidence", "?")
        counts[conf] = counts.get(conf, 0) + 1
    print(f"\nSummary: {counts}")
    print(f"GitHub API calls: {_github_api_calls} (cache hits: {stats['github_cache_hits']})")


if __name__ == "__main__":
    main()
