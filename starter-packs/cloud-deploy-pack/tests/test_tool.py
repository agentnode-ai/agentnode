"""Tests for cloud-deploy-pack."""

from unittest.mock import MagicMock, patch

from cloud_deploy_pack.tool import run


# -- Input validation --

def test_unknown_platform():
    result = run(platform="heroku", token="tok", operation="list")
    assert result["status"] == "error"
    assert "Unknown platform" in result["message"]


def test_vercel_unknown_operation():
    result = run(platform="vercel", token="tok", operation="destroy")
    assert result["status"] == "error"
    assert "Unknown Vercel operation" in result["message"]


def test_railway_unknown_operation():
    result = run(platform="railway", token="tok", operation="launch")
    assert result["status"] == "error"
    assert "Unknown Railway operation" in result["message"]


def test_railway_missing_project_id():
    result = run(platform="railway", token="tok", operation="list_services")
    assert result["status"] == "error"
    assert "project_id" in result["message"]


# -- Mocked Vercel list_deployments --

@patch("cloud_deploy_pack.tool.httpx.get")
def test_vercel_list_deployments(mock_get):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "deployments": [{
            "uid": "d1", "name": "my-app", "url": "my-app.vercel.app",
            "state": "READY", "created": 1704067200, "ready": 1704067260,
            "target": "production", "source": "git",
        }],
    }
    mock_get.return_value = mock_resp

    result = run(platform="vercel", token="tok", operation="list_deployments")
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["deployments"][0]["uid"] == "d1"


# -- Mocked Railway list_services --

@patch("cloud_deploy_pack.tool.httpx.post")
def test_railway_list_services(mock_post):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "data": {
            "project": {
                "id": "proj1", "name": "My Project",
                "services": {"edges": [
                    {"node": {"id": "svc1", "name": "web", "createdAt": "2024-01-01", "updatedAt": "2024-01-02"}},
                ]},
            },
        },
    }
    mock_post.return_value = mock_resp

    result = run(platform="railway", token="tok", operation="list_services", project_id="proj1")
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["services"][0]["name"] == "web"


# -- Error handling --

@patch("cloud_deploy_pack.tool.httpx.get")
def test_vercel_http_error(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized"
    import httpx
    mock_get.side_effect = httpx.HTTPStatusError("err", request=MagicMock(), response=mock_resp)

    result = run(platform="vercel", token="bad", operation="list_deployments")
    assert result["status"] == "error"
    assert "401" in result["message"]
