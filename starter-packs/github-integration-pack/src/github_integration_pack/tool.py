"""GitHub integration tool using PyGithub."""

from __future__ import annotations

from github import Auth, Github, GithubException


def _list_repos(g: Github, **kwargs) -> dict:
    """List repositories for the authenticated user."""
    user = g.get_user()
    repos = []
    limit = kwargs.get("limit", 30)
    sort = kwargs.get("sort", "updated")
    for repo in user.get_repos(sort=sort)[:limit]:
        repos.append({
            "full_name": repo.full_name,
            "description": repo.description or "",
            "language": repo.language or "",
            "stars": repo.stargazers_count,
            "forks": repo.forks_count,
            "private": repo.private,
            "url": repo.html_url,
            "updated_at": repo.updated_at.isoformat() if repo.updated_at else "",
        })
    return {"success": True, "repos": repos, "total": len(repos)}


def _get_repo(g: Github, repo: str, **kwargs) -> dict:
    """Get detailed information about a repository."""
    r = g.get_repo(repo)
    return {
        "success": True,
        "repo": {
            "full_name": r.full_name,
            "description": r.description or "",
            "language": r.language or "",
            "stars": r.stargazers_count,
            "forks": r.forks_count,
            "open_issues": r.open_issues_count,
            "private": r.private,
            "default_branch": r.default_branch,
            "url": r.html_url,
            "created_at": r.created_at.isoformat() if r.created_at else "",
            "updated_at": r.updated_at.isoformat() if r.updated_at else "",
            "topics": r.get_topics(),
        },
    }


def _list_issues(g: Github, repo: str, **kwargs) -> dict:
    """List issues for a repository."""
    r = g.get_repo(repo)
    state = kwargs.get("state", "open")
    limit = kwargs.get("limit", 30)
    issues = []
    for issue in r.get_issues(state=state)[:limit]:
        if issue.pull_request is not None:
            continue
        issues.append({
            "number": issue.number,
            "title": issue.title,
            "state": issue.state,
            "author": issue.user.login if issue.user else "",
            "labels": [l.name for l in issue.labels],
            "created_at": issue.created_at.isoformat() if issue.created_at else "",
            "updated_at": issue.updated_at.isoformat() if issue.updated_at else "",
            "url": issue.html_url,
        })
    return {"success": True, "issues": issues, "total": len(issues)}


def _create_issue(g: Github, repo: str, **kwargs) -> dict:
    """Create a new issue in a repository."""
    title = kwargs.get("title", "")
    body = kwargs.get("body", "")
    labels = kwargs.get("labels", [])
    assignees = kwargs.get("assignees", [])

    if not title:
        return {"success": False, "error": "Missing required parameter: title"}

    r = g.get_repo(repo)
    issue = r.create_issue(
        title=title,
        body=body,
        labels=labels,
        assignees=assignees,
    )
    return {
        "success": True,
        "issue": {
            "number": issue.number,
            "title": issue.title,
            "url": issue.html_url,
        },
    }


def _list_prs(g: Github, repo: str, **kwargs) -> dict:
    """List pull requests for a repository."""
    r = g.get_repo(repo)
    state = kwargs.get("state", "open")
    limit = kwargs.get("limit", 30)
    prs = []
    for pr in r.get_pulls(state=state, sort="updated", direction="desc")[:limit]:
        prs.append({
            "number": pr.number,
            "title": pr.title,
            "state": pr.state,
            "author": pr.user.login if pr.user else "",
            "base": pr.base.ref,
            "head": pr.head.ref,
            "mergeable": pr.mergeable,
            "created_at": pr.created_at.isoformat() if pr.created_at else "",
            "updated_at": pr.updated_at.isoformat() if pr.updated_at else "",
            "url": pr.html_url,
        })
    return {"success": True, "pull_requests": prs, "total": len(prs)}


def _get_file(g: Github, repo: str, **kwargs) -> dict:
    """Get file contents from a repository."""
    path = kwargs.get("path", "")
    ref = kwargs.get("ref", None)

    if not path:
        return {"success": False, "error": "Missing required parameter: path"}

    r = g.get_repo(repo)
    get_kwargs = {"path": path}
    if ref:
        get_kwargs["ref"] = ref

    content = r.get_contents(**get_kwargs)

    if isinstance(content, list):
        # It's a directory
        items = []
        for item in content:
            items.append({
                "name": item.name,
                "path": item.path,
                "type": item.type,
                "size": item.size,
            })
        return {"success": True, "type": "directory", "items": items}

    return {
        "success": True,
        "type": "file",
        "file": {
            "name": content.name,
            "path": content.path,
            "size": content.size,
            "encoding": content.encoding,
            "content": content.decoded_content.decode("utf-8", errors="replace")
            if content.decoded_content
            else "",
            "sha": content.sha,
        },
    }


_OPERATIONS = {
    "list_repos": (_list_repos, False),
    "get_repo": (_get_repo, True),
    "list_issues": (_list_issues, True),
    "create_issue": (_create_issue, True),
    "list_prs": (_list_prs, True),
    "get_file": (_get_file, True),
}


def run(token: str, operation: str, repo: str = "", **kwargs) -> dict:
    """Interact with GitHub repositories, issues, and pull requests.

    Args:
        token: GitHub personal access token.
        operation: One of 'list_repos', 'get_repo', 'list_issues',
                   'create_issue', 'list_prs', 'get_file'.
        repo: Repository in 'owner/name' format (required for most operations).
        **kwargs: Additional arguments depending on operation:
            list_repos: limit (int), sort (str)
            list_issues: state ('open'/'closed'/'all'), limit (int)
            create_issue: title (str, required), body (str), labels (list), assignees (list)
            list_prs: state ('open'/'closed'/'all'), limit (int)
            get_file: path (str, required), ref (str, optional branch/tag/sha)

    Returns:
        dict with operation results.
    """
    if not token:
        return {"success": False, "error": "Missing required parameter: token"}

    operation = operation.lower().strip()

    if operation not in _OPERATIONS:
        ops = ", ".join(sorted(_OPERATIONS.keys()))
        return {"success": False, "error": f"Unknown operation: {operation}. Available: {ops}"}

    handler, requires_repo = _OPERATIONS[operation]

    if requires_repo and not repo:
        return {"success": False, "error": f"Operation '{operation}' requires the 'repo' parameter (owner/name)."}

    try:
        auth = Auth.Token(token)
        g = Github(auth=auth)
        result = handler(g, repo=repo, **kwargs) if requires_repo else handler(g, **kwargs)
        g.close()
        return result
    except GithubException as exc:
        return {"success": False, "error": f"GitHub API error: {exc.data.get('message', str(exc))}"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
