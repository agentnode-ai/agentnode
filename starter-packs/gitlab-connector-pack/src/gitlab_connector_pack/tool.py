"""GitLab connector tool using python-gitlab."""

from __future__ import annotations

import gitlab


def _list_projects(gl: gitlab.Gitlab, **kwargs) -> dict:
    """List projects accessible to the authenticated user."""
    limit = kwargs.get("limit", 30)
    owned = kwargs.get("owned", False)
    membership = kwargs.get("membership", True)
    search = kwargs.get("search", None)

    list_kwargs: dict = {
        "owned": owned,
        "membership": membership,
        "order_by": "updated_at",
        "sort": "desc",
        "per_page": limit,
    }
    if search:
        list_kwargs["search"] = search

    projects = gl.projects.list(**list_kwargs)
    result = []
    for p in projects:
        result.append({
            "id": p.id,
            "name": p.name,
            "path_with_namespace": p.path_with_namespace,
            "description": p.description or "",
            "visibility": p.visibility,
            "default_branch": getattr(p, "default_branch", ""),
            "web_url": p.web_url,
            "star_count": getattr(p, "star_count", 0),
            "forks_count": getattr(p, "forks_count", 0),
            "last_activity_at": getattr(p, "last_activity_at", ""),
        })
    return {"success": True, "projects": result, "total": len(result)}


def _get_project(gl: gitlab.Gitlab, project: str, **kwargs) -> dict:
    """Get detailed information about a project."""
    p = gl.projects.get(project)
    return {
        "success": True,
        "project": {
            "id": p.id,
            "name": p.name,
            "path_with_namespace": p.path_with_namespace,
            "description": p.description or "",
            "visibility": p.visibility,
            "default_branch": getattr(p, "default_branch", ""),
            "web_url": p.web_url,
            "star_count": getattr(p, "star_count", 0),
            "forks_count": getattr(p, "forks_count", 0),
            "open_issues_count": getattr(p, "open_issues_count", 0),
            "topics": getattr(p, "topics", []),
            "created_at": getattr(p, "created_at", ""),
            "last_activity_at": getattr(p, "last_activity_at", ""),
        },
    }


def _list_mrs(gl: gitlab.Gitlab, project: str, **kwargs) -> dict:
    """List merge requests for a project."""
    p = gl.projects.get(project)
    state = kwargs.get("state", "opened")
    limit = kwargs.get("limit", 30)

    mrs = p.mergerequests.list(
        state=state,
        order_by="updated_at",
        sort="desc",
        per_page=limit,
    )
    result = []
    for mr in mrs:
        result.append({
            "iid": mr.iid,
            "title": mr.title,
            "state": mr.state,
            "author": mr.author.get("username", "") if mr.author else "",
            "source_branch": mr.source_branch,
            "target_branch": mr.target_branch,
            "merge_status": getattr(mr, "merge_status", ""),
            "created_at": mr.created_at,
            "updated_at": mr.updated_at,
            "web_url": mr.web_url,
        })
    return {"success": True, "merge_requests": result, "total": len(result)}


def _list_pipelines(gl: gitlab.Gitlab, project: str, **kwargs) -> dict:
    """List pipelines for a project."""
    p = gl.projects.get(project)
    limit = kwargs.get("limit", 20)
    ref = kwargs.get("ref", None)
    status = kwargs.get("status", None)

    list_kwargs: dict = {
        "order_by": "updated_at",
        "sort": "desc",
        "per_page": limit,
    }
    if ref:
        list_kwargs["ref"] = ref
    if status:
        list_kwargs["status"] = status

    pipelines = p.pipelines.list(**list_kwargs)
    result = []
    for pl in pipelines:
        result.append({
            "id": pl.id,
            "status": pl.status,
            "ref": pl.ref,
            "sha": pl.sha[:12],
            "source": getattr(pl, "source", ""),
            "created_at": getattr(pl, "created_at", ""),
            "updated_at": getattr(pl, "updated_at", ""),
            "web_url": getattr(pl, "web_url", ""),
        })
    return {"success": True, "pipelines": result, "total": len(result)}


def _get_file(gl: gitlab.Gitlab, project: str, **kwargs) -> dict:
    """Get file contents from a project repository."""
    path = kwargs.get("path", "")
    ref = kwargs.get("ref", None)

    if not path:
        return {"success": False, "error": "Missing required parameter: path"}

    p = gl.projects.get(project)

    get_kwargs: dict = {"file_path": path}
    if ref:
        get_kwargs["ref"] = ref
    else:
        get_kwargs["ref"] = getattr(p, "default_branch", "main")

    try:
        f = p.files.get(**get_kwargs)
        content = f.decode().decode("utf-8", errors="replace")
        return {
            "success": True,
            "file": {
                "file_name": f.file_name,
                "file_path": f.file_path,
                "size": f.size,
                "encoding": f.encoding,
                "ref": get_kwargs["ref"],
                "content": content,
                "last_commit_id": getattr(f, "last_commit_id", ""),
            },
        }
    except gitlab.exceptions.GitlabGetError:
        # Might be a directory; try listing tree instead
        tree = p.repository_tree(path=path, ref=get_kwargs["ref"])
        items = []
        for item in tree:
            items.append({
                "name": item["name"],
                "path": item["path"],
                "type": item["type"],
            })
        return {"success": True, "type": "directory", "items": items}


_OPERATIONS = {
    "list_projects": (_list_projects, False),
    "get_project": (_get_project, True),
    "list_mrs": (_list_mrs, True),
    "list_pipelines": (_list_pipelines, True),
    "get_file": (_get_file, True),
}


def run(url: str, token: str, operation: str, project: str = "", **kwargs) -> dict:
    """Interact with GitLab projects, merge requests, and pipelines.

    Args:
        url: GitLab instance URL (e.g., 'https://gitlab.com').
        token: GitLab personal access token.
        operation: One of 'list_projects', 'get_project', 'list_mrs',
                   'list_pipelines', 'get_file'.
        project: Project ID or 'namespace/name' (required for most operations).
        **kwargs: Additional arguments depending on operation:
            list_projects: limit (int), owned (bool), membership (bool), search (str)
            list_mrs: state ('opened'/'closed'/'merged'/'all'), limit (int)
            list_pipelines: limit (int), ref (str), status (str)
            get_file: path (str, required), ref (str, optional)

    Returns:
        dict with operation results.
    """
    if not url:
        return {"success": False, "error": "Missing required parameter: url"}
    if not token:
        return {"success": False, "error": "Missing required parameter: token"}

    operation = operation.lower().strip()

    if operation not in _OPERATIONS:
        ops = ", ".join(sorted(_OPERATIONS.keys()))
        return {"success": False, "error": f"Unknown operation: {operation}. Available: {ops}"}

    handler, requires_project = _OPERATIONS[operation]

    if requires_project and not project:
        return {
            "success": False,
            "error": f"Operation '{operation}' requires the 'project' parameter (ID or namespace/name).",
        }

    try:
        gl = gitlab.Gitlab(url=url, private_token=token)
        gl.auth()
        if requires_project:
            result = handler(gl, project=project, **kwargs)
        else:
            result = handler(gl, **kwargs)
        return result
    except gitlab.exceptions.GitlabAuthenticationError as exc:
        return {"success": False, "error": f"Authentication failed: {exc}"}
    except gitlab.exceptions.GitlabGetError as exc:
        return {"success": False, "error": f"GitLab API error: {exc}"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
