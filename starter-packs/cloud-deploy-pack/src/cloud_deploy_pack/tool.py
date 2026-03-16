"""Cloud deployment tool for Vercel and Railway using httpx."""

from __future__ import annotations

import httpx

_VERCEL_API = "https://api.vercel.com"
_RAILWAY_API = "https://backboard.railway.app/graphql/v2"


# ── Vercel ───────────────────────────────────────────────────────────────

def _vercel(token: str, operation: str, **kwargs) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    team_id = kwargs.get("team_id")
    params: dict = {}
    if team_id:
        params["teamId"] = team_id

    if operation == "list_deployments":
        limit = kwargs.get("limit", 20)
        project_name = kwargs.get("project")
        query_params = {**params, "limit": limit}
        if project_name:
            query_params["projectId"] = project_name
        resp = httpx.get(
            f"{_VERCEL_API}/v6/deployments",
            headers=headers,
            params=query_params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        deployments = []
        for d in data.get("deployments", []):
            deployments.append({
                "uid": d.get("uid"),
                "name": d.get("name"),
                "url": d.get("url"),
                "state": d.get("state"),
                "created": d.get("created"),
                "ready": d.get("ready"),
                "target": d.get("target"),
                "source": d.get("source"),
            })
        return {"status": "ok", "deployments": deployments, "count": len(deployments)}

    elif operation == "create_deployment":
        project = kwargs.get("project")
        name = kwargs.get("name", project)
        git_source = kwargs.get("git_source")
        body: dict = {}
        if name:
            body["name"] = name
        if project:
            body["project"] = project
        if git_source:
            body["gitSource"] = git_source
        # Optional: specify target (production / preview)
        target = kwargs.get("target")
        if target:
            body["target"] = target
        resp = httpx.post(
            f"{_VERCEL_API}/v13/deployments",
            headers=headers,
            params=params,
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        d = resp.json()
        return {
            "status": "ok",
            "message": "Deployment created",
            "deployment": {
                "uid": d.get("id"),
                "url": d.get("url"),
                "name": d.get("name"),
                "state": d.get("readyState") or d.get("state"),
                "created": d.get("createdAt"),
            },
        }

    else:
        return {
            "status": "error",
            "message": f"Unknown Vercel operation: {operation}. Valid: list_deployments, create_deployment",
        }


# ── Railway ──────────────────────────────────────────────────────────────

def _railway(token: str, operation: str, **kwargs) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    if operation == "list_services":
        project_id = kwargs.get("project_id")
        if not project_id:
            return {"status": "error", "message": "project_id is required for Railway list_services"}
        query = """
        query($projectId: String!) {
            project(id: $projectId) {
                id
                name
                services {
                    edges {
                        node {
                            id
                            name
                            createdAt
                            updatedAt
                        }
                    }
                }
            }
        }
        """
        resp = httpx.post(
            _RAILWAY_API,
            headers=headers,
            json={"query": query, "variables": {"projectId": project_id}},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            return {"status": "error", "message": str(data["errors"])}
        project = data.get("data", {}).get("project", {})
        services = []
        for edge in project.get("services", {}).get("edges", []):
            node = edge.get("node", {})
            services.append({
                "id": node.get("id"),
                "name": node.get("name"),
                "created_at": node.get("createdAt"),
                "updated_at": node.get("updatedAt"),
            })
        return {
            "status": "ok",
            "project_id": project_id,
            "project_name": project.get("name"),
            "services": services,
            "count": len(services),
        }

    else:
        return {
            "status": "error",
            "message": f"Unknown Railway operation: {operation}. Valid: list_services",
        }


# ── Main entry point ────────────────────────────────────────────────────

_PLATFORMS = {
    "vercel": _vercel,
    "railway": _railway,
}


def run(platform: str, token: str, operation: str, **kwargs) -> dict:
    """Deploy and manage applications on cloud platforms.

    Parameters
    ----------
    platform : str
        One of ``vercel`` or ``railway``.
    token : str
        Authentication token for the platform API.
    operation : str
        Platform-specific operation.

        Vercel: list_deployments, create_deployment
            - project : str – project name or ID
            - name : str – deployment name
            - team_id : str – optional Vercel team ID
            - target : str – ``"production"`` or ``"preview"``
            - git_source : dict – optional git source config

        Railway: list_services
            - project_id : str – Railway project ID (required)

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
