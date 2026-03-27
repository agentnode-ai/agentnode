"""
@tool function that internally uses a helper class.
Pattern seen when someone organises their logic into a class but exposes it via @tool.
"""

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests
from langchain.tools import tool


@dataclass
class GitHubRepo:
    """Lightweight wrapper around GitHub repo metadata."""
    owner: str
    name: str
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    language: Optional[str] = None
    description: str = ""
    topics: List[str] = field(default_factory=list)
    url: str = ""

    @classmethod
    def from_api_response(cls, data: dict) -> "GitHubRepo":
        return cls(
            owner=data.get("owner", {}).get("login", ""),
            name=data.get("name", ""),
            stars=data.get("stargazers_count", 0),
            forks=data.get("forks_count", 0),
            open_issues=data.get("open_issues_count", 0),
            language=data.get("language"),
            description=data.get("description", "") or "",
            topics=data.get("topics", []),
            url=data.get("html_url", ""),
        )

    def to_dict(self) -> dict:
        return {
            "owner": self.owner,
            "name": self.name,
            "full_name": f"{self.owner}/{self.name}",
            "stars": self.stars,
            "forks": self.forks,
            "open_issues": self.open_issues,
            "language": self.language,
            "description": self.description,
            "topics": self.topics,
            "url": self.url,
        }


@tool
def get_github_repo_info(repo: str) -> dict:
    """
    Fetch metadata about a GitHub repository.

    Args:
        repo: Repository in "owner/name" format, e.g. "langchain-ai/langchain"

    Returns:
        dict with repo metadata including stars, forks, language, topics
    """
    parts = repo.strip().split("/")
    if len(parts) != 2:
        return {"error": "repo must be in 'owner/name' format", "repo": repo}

    owner, name = parts
    url = f"https://api.github.com/repos/{owner}/{name}"
    headers = {"Accept": "application/vnd.github+json"}

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 404:
            return {"error": f"Repository not found: {repo}", "repo": repo}
        resp.raise_for_status()

        github_repo = GitHubRepo.from_api_response(resp.json())
        result = github_repo.to_dict()
        result["error"] = None
        return result

    except requests.RequestException as e:
        return {"error": str(e), "repo": repo}
