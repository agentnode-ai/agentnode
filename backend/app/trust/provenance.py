"""Provenance verification — validate that source_repo and commit exist.

Called as a background task after publish to verify that the claimed
source repository and commit SHA actually exist on GitHub/GitLab.
"""
from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger(__name__)

GITHUB_REPO_PATTERN = re.compile(r"https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$")
GITLAB_REPO_PATTERN = re.compile(r"https?://gitlab\.com/([^/]+/[^/]+?)(?:\.git)?/?$")


async def verify_github_repo(owner_repo: str) -> bool:
    """Check if a GitHub repository exists (public)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner_repo}",
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            return resp.status_code == 200
    except Exception as e:
        logger.warning(f"GitHub repo check failed for {owner_repo}: {e}")
        return False


async def verify_github_commit(owner_repo: str, commit_sha: str) -> bool:
    """Check if a specific commit exists in a GitHub repository."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner_repo}/commits/{commit_sha}",
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            return resp.status_code == 200
    except Exception as e:
        logger.warning(f"GitHub commit check failed for {owner_repo}@{commit_sha}: {e}")
        return False


async def verify_provenance(source_repo_url: str | None, commit_sha: str | None) -> dict:
    """Verify that the claimed provenance is real.

    Returns:
        dict with verification results:
            repo_exists: bool | None
            commit_exists: bool | None
            provider: str | None
    """
    result = {
        "repo_exists": None,
        "commit_exists": None,
        "provider": None,
    }

    if not source_repo_url:
        return result

    # GitHub
    github_match = GITHUB_REPO_PATTERN.match(source_repo_url)
    if github_match:
        owner_repo = github_match.group(1)
        result["provider"] = "github"
        result["repo_exists"] = await verify_github_repo(owner_repo)

        if result["repo_exists"] and commit_sha:
            result["commit_exists"] = await verify_github_commit(owner_repo, commit_sha)
        return result

    # GitLab (basic check — just HEAD the URL)
    gitlab_match = GITLAB_REPO_PATTERN.match(source_repo_url)
    if gitlab_match:
        result["provider"] = "gitlab"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.head(source_repo_url)
                result["repo_exists"] = resp.status_code < 400
        except Exception:
            result["repo_exists"] = False
        return result

    # Unknown provider — try HEAD
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.head(source_repo_url)
            result["repo_exists"] = resp.status_code < 400
            result["provider"] = "other"
    except Exception:
        result["repo_exists"] = False

    return result
