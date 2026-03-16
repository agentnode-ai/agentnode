"""CI/CD pipeline runner for GitHub Actions and GitLab CI using httpx."""

from __future__ import annotations

import httpx

_GITHUB_API = "https://api.github.com"


# ── GitHub Actions ───────────────────────────────────────────────────────

def _github(token: str, operation: str, **kwargs) -> dict:
    repo = kwargs.get("repo", "")  # "owner/repo"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    if operation == "list_runs":
        if not repo:
            return {"status": "error", "message": "repo is required (owner/repo)"}
        per_page = kwargs.get("per_page", 10)
        branch = kwargs.get("branch")
        params: dict = {"per_page": per_page}
        if branch:
            params["branch"] = branch
        resp = httpx.get(
            f"{_GITHUB_API}/repos/{repo}/actions/runs",
            headers=headers,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        runs = []
        for r in data.get("workflow_runs", []):
            runs.append({
                "id": r["id"],
                "name": r.get("name"),
                "status": r["status"],
                "conclusion": r.get("conclusion"),
                "branch": r["head_branch"],
                "event": r["event"],
                "created_at": r["created_at"],
                "url": r["html_url"],
            })
        return {"status": "ok", "runs": runs, "total_count": data.get("total_count", 0)}

    elif operation == "trigger_workflow":
        if not repo:
            return {"status": "error", "message": "repo is required (owner/repo)"}
        workflow_id = kwargs.get("workflow_id", kwargs.get("workflow_file", ""))
        if not workflow_id:
            return {"status": "error", "message": "workflow_id or workflow_file is required"}
        ref = kwargs.get("ref", "main")
        inputs = kwargs.get("inputs", {})
        resp = httpx.post(
            f"{_GITHUB_API}/repos/{repo}/actions/workflows/{workflow_id}/dispatches",
            headers=headers,
            json={"ref": ref, "inputs": inputs},
            timeout=30,
        )
        resp.raise_for_status()
        return {
            "status": "ok",
            "message": f"Workflow {workflow_id} dispatched on {ref}",
            "repo": repo,
        }

    elif operation == "get_run":
        if not repo:
            return {"status": "error", "message": "repo is required (owner/repo)"}
        run_id = kwargs.get("run_id")
        if not run_id:
            return {"status": "error", "message": "run_id is required"}
        resp = httpx.get(
            f"{_GITHUB_API}/repos/{repo}/actions/runs/{run_id}",
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        r = resp.json()
        return {
            "status": "ok",
            "run": {
                "id": r["id"],
                "name": r.get("name"),
                "status": r["status"],
                "conclusion": r.get("conclusion"),
                "branch": r["head_branch"],
                "event": r["event"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "url": r["html_url"],
                "run_attempt": r.get("run_attempt"),
            },
        }

    else:
        return {"status": "error", "message": f"Unknown GitHub operation: {operation}. Valid: list_runs, trigger_workflow, get_run"}


# ── GitLab CI ────────────────────────────────────────────────────────────

def _gitlab(token: str, operation: str, **kwargs) -> dict:
    base_url = kwargs.get("url", "https://gitlab.com").rstrip("/")
    api_url = f"{base_url}/api/v4"
    project_id = kwargs.get("project_id", "")
    headers = {"PRIVATE-TOKEN": token}

    if operation == "list_pipelines":
        if not project_id:
            return {"status": "error", "message": "project_id is required"}
        per_page = kwargs.get("per_page", 10)
        ref = kwargs.get("ref")
        params: dict = {"per_page": per_page}
        if ref:
            params["ref"] = ref
        resp = httpx.get(
            f"{api_url}/projects/{project_id}/pipelines",
            headers=headers,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        pipelines = []
        for p in resp.json():
            pipelines.append({
                "id": p["id"],
                "status": p["status"],
                "ref": p["ref"],
                "sha": p.get("sha", "")[:12],
                "created_at": p.get("created_at"),
                "updated_at": p.get("updated_at"),
                "web_url": p.get("web_url"),
            })
        return {"status": "ok", "pipelines": pipelines, "count": len(pipelines)}

    elif operation == "trigger_pipeline":
        if not project_id:
            return {"status": "error", "message": "project_id is required"}
        ref = kwargs.get("ref", "main")
        variables = kwargs.get("variables", [])
        body: dict = {"ref": ref}
        if variables:
            body["variables"] = variables
        # Use a pipeline trigger token if provided, otherwise use personal token
        trigger_token = kwargs.get("trigger_token")
        if trigger_token:
            body["token"] = trigger_token
            resp = httpx.post(
                f"{api_url}/projects/{project_id}/trigger/pipeline",
                json=body,
                timeout=30,
            )
        else:
            resp = httpx.post(
                f"{api_url}/projects/{project_id}/pipeline",
                headers=headers,
                json={"ref": ref},
                timeout=30,
            )
        resp.raise_for_status()
        p = resp.json()
        return {
            "status": "ok",
            "message": f"Pipeline triggered on ref {ref}",
            "pipeline": {
                "id": p.get("id"),
                "status": p.get("status"),
                "ref": p.get("ref"),
                "web_url": p.get("web_url"),
            },
        }

    elif operation == "get_pipeline":
        if not project_id:
            return {"status": "error", "message": "project_id is required"}
        pipeline_id = kwargs.get("pipeline_id")
        if not pipeline_id:
            return {"status": "error", "message": "pipeline_id is required"}
        resp = httpx.get(
            f"{api_url}/projects/{project_id}/pipelines/{pipeline_id}",
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        p = resp.json()
        return {
            "status": "ok",
            "pipeline": {
                "id": p["id"],
                "status": p["status"],
                "ref": p.get("ref"),
                "sha": p.get("sha", "")[:12],
                "created_at": p.get("created_at"),
                "updated_at": p.get("updated_at"),
                "finished_at": p.get("finished_at"),
                "duration": p.get("duration"),
                "web_url": p.get("web_url"),
            },
        }

    else:
        return {"status": "error", "message": f"Unknown GitLab operation: {operation}. Valid: list_pipelines, trigger_pipeline, get_pipeline"}


# ── Main entry point ────────────────────────────────────────────────────

_PLATFORMS = {
    "github": _github,
    "gitlab": _gitlab,
}


def run(platform: str, token: str, operation: str, **kwargs) -> dict:
    """Trigger and monitor CI/CD pipelines.

    Parameters
    ----------
    platform : str
        One of ``github`` or ``gitlab``.
    token : str
        Authentication token (GitHub PAT or GitLab private token).
    operation : str
        Platform-specific operation.

        GitHub: list_runs, trigger_workflow, get_run
            - repo : str – ``"owner/repo"`` (required for all)
            - workflow_id : str – workflow file name or ID (for trigger_workflow)
            - run_id : int – (for get_run)
            - ref : str – branch name (default ``"main"``, for trigger_workflow)

        GitLab: list_pipelines, trigger_pipeline, get_pipeline
            - project_id : str – GitLab project ID (required for all)
            - url : str – GitLab instance URL (default ``"https://gitlab.com"``)
            - pipeline_id : int – (for get_pipeline)
            - ref : str – branch name (default ``"main"``, for trigger_pipeline)

    Returns
    -------
    dict with ``status`` and operation-specific data.
    """
    handler = _PLATFORMS.get(platform)
    if handler is None:
        return {
            "status": "error",
            "message": f"Unknown platform: {platform}. Valid platforms: {', '.join(_PLATFORMS)}",
        }

    try:
        return handler(token, operation, **kwargs)
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:500]
        return {
            "status": "error",
            "message": f"HTTP {exc.response.status_code}: {body}",
        }
    except httpx.RequestError as exc:
        return {"status": "error", "message": f"Request error: {exc}"}
    except KeyError as exc:
        return {"status": "error", "message": f"Missing required parameter: {exc}"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
