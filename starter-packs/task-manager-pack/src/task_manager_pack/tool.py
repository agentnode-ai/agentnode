"""Task management via the Linear GraphQL API."""

from __future__ import annotations


def run(provider: str, api_key: str, operation: str, **kwargs) -> dict:
    """Manage tasks and issues on Linear.

    Args:
        provider: Task management provider (currently "linear").
        api_key: Linear API key.
        operation: One of "list_issues", "create_issue", "update_issue".
        **kwargs:
            title (str): Issue title (for "create_issue").
            description (str): Issue description (for "create_issue").
            team_id (str): Team ID (for "create_issue").
            issue_id (str): Issue ID (for "update_issue").
            state (str): New state ID (for "update_issue").
            limit (int): Max issues to fetch (for "list_issues", default 50).

    Returns:
        dict varying by operation.
    """
    if provider != "linear":
        raise ValueError(f"Unsupported provider: {provider}. Currently only 'linear' is supported.")

    import httpx

    url = "https://api.linear.app/graphql"
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }

    ops = {
        "list_issues": _list_issues,
        "create_issue": _create_issue,
        "update_issue": _update_issue,
    }

    if operation not in ops:
        raise ValueError(f"Unknown operation: {operation}. Choose from {list(ops)}")

    with httpx.Client(timeout=30.0) as client:
        return ops[operation](client, url, headers, **kwargs)


def _graphql(client, url: str, headers: dict, query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL query against Linear."""
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables
    resp = client.post(url, headers=headers, json=payload)
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"Linear API errors: {data['errors']}")
    return data.get("data", {})


def _list_issues(client, url: str, headers: dict, **kwargs) -> dict:
    limit = int(kwargs.get("limit", 50))
    query = """
    query ListIssues($first: Int) {
      issues(first: $first, orderBy: updatedAt) {
        nodes {
          id
          identifier
          title
          description
          priority
          state {
            id
            name
          }
          assignee {
            name
            email
          }
          createdAt
          updatedAt
        }
      }
    }
    """
    data = _graphql(client, url, headers, query, {"first": min(limit, 250)})
    nodes = data.get("issues", {}).get("nodes", [])

    issues = []
    for node in nodes:
        state = node.get("state") or {}
        assignee = node.get("assignee") or {}
        issues.append({
            "id": node.get("id", ""),
            "identifier": node.get("identifier", ""),
            "title": node.get("title", ""),
            "description": (node.get("description") or "")[:500],
            "priority": node.get("priority", 0),
            "state": state.get("name", ""),
            "state_id": state.get("id", ""),
            "assignee": assignee.get("name", ""),
            "created_at": node.get("createdAt", ""),
            "updated_at": node.get("updatedAt", ""),
        })

    return {"issues": issues, "total": len(issues)}


def _create_issue(client, url: str, headers: dict, **kwargs) -> dict:
    title = kwargs.get("title", "")
    description = kwargs.get("description", "")
    team_id = kwargs.get("team_id", "")

    if not title:
        raise ValueError("title is required for create_issue")
    if not team_id:
        raise ValueError("team_id is required for create_issue")

    query = """
    mutation CreateIssue($input: IssueCreateInput!) {
      issueCreate(input: $input) {
        success
        issue {
          id
          identifier
          title
          url
          state {
            name
          }
        }
      }
    }
    """
    variables = {
        "input": {
            "title": title,
            "description": description,
            "teamId": team_id,
        }
    }

    data = _graphql(client, url, headers, query, variables)
    result = data.get("issueCreate", {})
    issue = result.get("issue", {})

    return {
        "status": "created" if result.get("success") else "failed",
        "id": issue.get("id", ""),
        "identifier": issue.get("identifier", ""),
        "title": issue.get("title", ""),
        "url": issue.get("url", ""),
        "state": (issue.get("state") or {}).get("name", ""),
    }


def _update_issue(client, url: str, headers: dict, **kwargs) -> dict:
    issue_id = kwargs.get("issue_id", "")
    state = kwargs.get("state", "")

    if not issue_id:
        raise ValueError("issue_id is required for update_issue")

    input_fields: dict = {}
    if state:
        input_fields["stateId"] = state

    if not input_fields:
        raise ValueError("At least one field to update is required (e.g. state)")

    query = """
    mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
      issueUpdate(id: $id, input: $input) {
        success
        issue {
          id
          identifier
          title
          state {
            id
            name
          }
          updatedAt
        }
      }
    }
    """
    variables = {"id": issue_id, "input": input_fields}

    data = _graphql(client, url, headers, query, variables)
    result = data.get("issueUpdate", {})
    issue = result.get("issue", {})

    return {
        "status": "updated" if result.get("success") else "failed",
        "id": issue.get("id", ""),
        "identifier": issue.get("identifier", ""),
        "title": issue.get("title", ""),
        "state": (issue.get("state") or {}).get("name", ""),
        "updated_at": issue.get("updatedAt", ""),
    }
