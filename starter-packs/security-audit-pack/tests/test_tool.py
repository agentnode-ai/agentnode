"""Tests for security-audit-pack."""

import json
from unittest.mock import MagicMock, patch

from security_audit_pack.tool import _parse_bandit_output, run


# -- Pure helper: _parse_bandit_output --

def test_parse_empty_output():
    assert _parse_bandit_output("") == []


def test_parse_invalid_json():
    result = _parse_bandit_output("not json")
    assert len(result) == 1
    assert result[0]["severity"] == "ERROR"


def test_parse_valid_output():
    output = json.dumps({
        "results": [{
            "issue_severity": "HIGH",
            "issue_confidence": "HIGH",
            "issue_text": "Use of os.system detected",
            "line_number": 2,
            "test_id": "B605",
            "test_name": "start_process_with_a_shell",
        }],
    })
    issues = _parse_bandit_output(output)
    assert len(issues) == 1
    assert issues[0]["severity"] == "HIGH"
    assert issues[0]["test_id"] == "B605"
    assert issues[0]["line"] == 2


def test_parse_no_results():
    output = json.dumps({"results": []})
    assert _parse_bandit_output(output) == []


# -- Mocked run --

@patch("security_audit_pack.tool.shutil.which", return_value="/usr/bin/bandit")
@patch("security_audit_pack.tool.subprocess.run")
def test_run_detects_issues(mock_subproc, mock_which):
    mock_subproc.return_value = MagicMock(
        stdout=json.dumps({
            "results": [{
                "issue_severity": "MEDIUM",
                "issue_confidence": "HIGH",
                "issue_text": "Possible SQL injection",
                "line_number": 5,
                "test_id": "B608",
                "test_name": "hardcoded_sql_expressions",
            }],
        }),
        stderr="",
    )

    result = run(code="query = 'SELECT * FROM users WHERE id=' + user_id")
    assert result["total"] == 1
    assert result["issues"][0]["severity"] == "MEDIUM"


@patch("security_audit_pack.tool.shutil.which", return_value="/usr/bin/bandit")
@patch("security_audit_pack.tool.subprocess.run")
def test_run_clean_code(mock_subproc, mock_which):
    mock_subproc.return_value = MagicMock(stdout=json.dumps({"results": []}), stderr="")

    result = run(code="x = 1 + 2\nprint(x)")
    assert result["total"] == 0
    assert result["issues"] == []


# -- Bandit not installed --

@patch("security_audit_pack.tool.shutil.which", return_value=None)
def test_bandit_not_installed(mock_which):
    result = run(code="import os\nos.system('ls')")
    assert result["total"] == 1
    assert "not installed" in result["issues"][0]["description"]


# -- Timeout --

@patch("security_audit_pack.tool.shutil.which", return_value="/usr/bin/bandit")
@patch("security_audit_pack.tool.subprocess.run")
def test_run_timeout(mock_subproc, mock_which):
    import subprocess
    mock_subproc.side_effect = subprocess.TimeoutExpired(cmd="bandit", timeout=60)

    result = run(code="x = 1")
    assert result["total"] == 1
    assert "timed out" in result["issues"][0]["description"].lower()


# -- Severity validation --

@patch("security_audit_pack.tool.shutil.which", return_value="/usr/bin/bandit")
@patch("security_audit_pack.tool.subprocess.run")
def test_invalid_severity_defaults_to_low(mock_subproc, mock_which):
    mock_subproc.return_value = MagicMock(stdout=json.dumps({"results": []}), stderr="")
    result = run(code="x = 1", severity="INVALID")
    assert result["total"] == 0
