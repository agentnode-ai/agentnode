#!/usr/bin/env python3
"""MCP Candidate Verification Pipeline.

Discovers MCP servers from awesome-mcp-servers, resolves npm packages,
extracts metadata, and scores candidates for review.

No DB writes. No auto-seeding. No trust upgrades. Review output only.

Usage:
    python scripts/verify_mcp_candidates.py -o mcp_candidates.json
    python scripts/verify_mcp_candidates.py -o mcp_candidates.json --test
    python scripts/verify_mcp_candidates.py -o mcp_candidates.json --test --max 10
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

DANGEROUS_SCRIPTS = {"preinstall", "postinstall", "install"}

_http = httpx.Client(timeout=15, follow_redirects=True)
_github_headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}


# ---------------------------------------------------------------------------
# Stage 1: Crawl
# ---------------------------------------------------------------------------

def crawl_awesome() -> list[dict]:
    """Fetch awesome-mcp-servers and extract GitHub repos."""
    print("Stage 1: Crawling awesome-mcp-servers...")
    resp = _http.get(AWESOME_URL)
    resp.raise_for_status()
    readme = resp.text

    pattern = re.compile(r"https://github\.com/([a-zA-Z0-9._-]+)/([a-zA-Z0-9._-]+)")
    seen: set[str] = set()
    candidates = []

    for m in pattern.finditer(readme):
        owner, repo = m.group(1), m.group(2)
        url = f"https://github.com/{owner}/{repo}"
        url = url.rstrip("/").split("#")[0].split("?")[0]
        if url in seen:
            continue
        seen.add(url)
        slug = re.sub(r"[^a-z0-9-]", "-", repo.lower()).strip("-")
        if not slug.startswith("mcp-") and "mcp" not in slug:
            slug = f"mcp-{slug}"

        candidates.append({
            "slug": slug,
            "owner": owner,
            "repo": repo,
            "source_repo": url,
        })

    print(f"  Found {len(candidates)} unique repos")
    return candidates


# ---------------------------------------------------------------------------
# Stage 2: GitHub metadata
# ---------------------------------------------------------------------------

def enrich_github(candidate: dict) -> None:
    """Fetch GitHub repo metadata."""
    owner, repo = candidate["owner"], candidate["repo"]
    url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        resp = _http.get(url, headers=_github_headers)
        if resp.status_code == 403:
            print(f"    Rate limited, sleeping 60s...")
            time.sleep(60)
            resp = _http.get(url, headers=_github_headers)
        if resp.status_code == 404:
            candidate["issues"] = candidate.get("issues", []) + ["repo_not_found"]
            return
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        candidate["issues"] = candidate.get("issues", []) + [f"github_error: {e}"]
        return

    candidate["name"] = data.get("name", repo)
    candidate["description"] = data.get("description", "")
    candidate["stars"] = data.get("stargazers_count", 0)
    candidate["archived"] = data.get("archived", False)
    candidate["last_push"] = data.get("pushed_at", "")

    license_info = data.get("license")
    if license_info:
        candidate["license"] = license_info.get("spdx_id", "NOASSERTION")
    else:
        candidate["license"] = None

    if candidate.get("archived"):
        candidate["issues"] = candidate.get("issues", []) + ["repo_archived"]


# ---------------------------------------------------------------------------
# Stage 3: Resolve npm package
# ---------------------------------------------------------------------------

def resolve_npm(candidate: dict) -> None:
    """Try to find and resolve the npm package."""
    owner, repo = candidate["owner"], candidate["repo"]

    # Try package.json from GitHub
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

    # Check npm registry
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

    # Derive command
    if latest:
        candidate["command"] = ["npx", "-y", f"{npm_name}@{latest}"]


# ---------------------------------------------------------------------------
# Stage 4: Extract env_keys from README
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
# Stage 5: Protocol test (optional)
# ---------------------------------------------------------------------------

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

    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", tempfile.gettempdir())}
    if sys.platform == "win32":
        env["USERPROFILE"] = os.environ.get("USERPROFILE", "")
        env["APPDATA"] = os.environ.get("APPDATA", "")
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")

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
        import select
        if sys.platform == "win32":
            try:
                line = proc.stdout.readline()
                return json.loads(line) if line.strip() else None
            except Exception:
                return None
        else:
            ready, _, _ = select.select([proc.stdout], [], [], timeout)
            if ready:
                line = proc.stdout.readline()
                return json.loads(line) if line.strip() else None
            return None

    try:
        # Initialize
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

        # tools/list
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

    if not candidate.get("npm_exists"):
        return "LOW"

    if candidate.get("protocol_passed"):
        return "HIGH"

    if candidate.get("npm_version") and candidate.get("command"):
        if candidate.get("protocol_tested") and not candidate.get("protocol_passed"):
            return "MEDIUM"
        return "MEDIUM"

    return "LOW"


# ---------------------------------------------------------------------------
# Stage 7: Output
# ---------------------------------------------------------------------------

def build_suggested_seed(c: dict) -> dict | None:
    """Build a suggested seed catalog entry for HIGH/MEDIUM candidates."""
    if not c.get("npm_exists") or not c.get("npm_version"):
        return None

    slug = c["slug"]
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

    return {
        "slug": slug,
        "name": c.get("name", slug),
        "npm_package": c["npm_package"],
        "pinned_version": c["npm_version"],
        "source_repo": c["source_repo"],
        "summary": (c.get("description") or f"MCP server: {c.get('name', slug)}")[:200],
        "description": c.get("description") or "",
        "env_keys": c.get("env_keys", []),
        "tools": tools,
        "tags": ["mcp", "mcp-server"],
        "categories": ["general"],
    }


def write_review_md(candidates: list[dict], path: str) -> None:
    """Write human-readable review markdown."""
    lines = [
        "# MCP Candidate Review",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Total candidates: {len(candidates)}",
        "",
    ]

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

        lines.append("| Slug | npm | Version | Stars | Tools | env_keys | Issues |")
        lines.append("|---|---|---|---|---|---|---|")

        for c in sorted(items, key=lambda x: x.get("stars", 0) or 0, reverse=True):
            npm = c.get("npm_package", "-")
            ver = c.get("npm_version", "-")
            stars = c.get("stars", "-")
            tools = c.get("tools_count", len(c.get("tools_snapshot", [])))
            env = len(c.get("env_keys", []))
            issues = "; ".join(c.get("issues", [])) or "-"
            lines.append(f"| {c['slug']} | {npm} | {ver} | {stars} | {tools} | {env} | {issues} |")

        lines.append("")

    Path(path).write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="MCP Candidate Verification Pipeline")
    parser.add_argument("-o", "--output", default="mcp_candidates.json", help="JSON output path")
    parser.add_argument("--review", default="MCP_CANDIDATE_REVIEW.md", help="Review markdown path")
    parser.add_argument("--github-token", "-t", help="GitHub token for higher rate limits")
    parser.add_argument("--test", action="store_true", help="Enable protocol smoke test")
    parser.add_argument("--max", type=int, default=0, help="Max candidates to process (0=all)")
    parser.add_argument("--skip-existing", action="store_true", help="Skip already-seeded slugs")
    args = parser.parse_args()

    if args.github_token:
        _github_headers["Authorization"] = f"Bearer {args.github_token}"

    # Stage 1
    candidates = crawl_awesome()
    if args.max > 0:
        candidates = candidates[:args.max]

    total = len(candidates)
    for i, c in enumerate(candidates):
        slug = c["slug"]
        c["issues"] = []

        if args.skip_existing and slug in EXISTING_SLUGS:
            c["confidence"] = "SKIP"
            c["issues"].append("already_seeded")
            print(f"  [{i+1}/{total}] {slug}: SKIP (already seeded)")
            continue

        print(f"  [{i+1}/{total}] {c['owner']}/{c['repo']}")

        # Stage 2: GitHub
        enrich_github(c)
        if c.get("archived"):
            c["confidence"] = "REJECT"
            print(f"    -> REJECT (archived)")
            continue

        time.sleep(0.3)

        # Stage 3: npm
        resolve_npm(c)
        time.sleep(0.2)

        # Stage 4: env_keys
        extract_env_keys(c)

        # Stage 5: protocol test
        if args.test and c.get("npm_exists") and c.get("command"):
            print(f"    Testing protocol...")
            protocol_test(c)
            if c.get("protocol_passed"):
                print(f"    -> Protocol OK, {c.get('tools_count', 0)} tools")
            elif c.get("protocol_skip_reason"):
                print(f"    -> Skipped: {c['protocol_skip_reason']}")
            else:
                print(f"    -> Protocol FAILED: {c.get('protocol_error', '?')}")

        # Stage 6: confidence
        c["confidence"] = compute_confidence(c)

        # Build suggested seed entry
        if c["confidence"] in ("HIGH", "MEDIUM"):
            c["suggested_seed_entry"] = build_suggested_seed(c)

        status = c["confidence"]
        npm_info = c.get("npm_package", "no-npm")
        print(f"    -> {status} ({npm_info})")

    # Filter out SKIP
    output_candidates = [c for c in candidates if c.get("confidence") != "SKIP"]

    # Remove internal fields
    for c in output_candidates:
        c.pop("owner", None)
        c.pop("repo", None)
        c.pop("_pkg_json", None)

    # Stage 7: Write output
    Path(args.output).write_text(
        json.dumps(output_candidates, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\nOutput: {args.output} ({len(output_candidates)} candidates)")

    # Write review
    write_review_md(output_candidates, args.review)
    print(f"Review: {args.review}")

    # Summary
    counts = {}
    for c in output_candidates:
        conf = c.get("confidence", "?")
        counts[conf] = counts.get(conf, 0) + 1
    print(f"\nSummary: {counts}")


if __name__ == "__main__":
    main()
