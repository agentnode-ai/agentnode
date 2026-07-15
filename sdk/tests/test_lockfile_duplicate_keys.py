"""Slice 0.2A-1a — global duplicate-key parser hardening for agentnode.lock.

JSON permits duplicate object keys; ``json.loads`` silently keeps last-wins,
which lets a tampered lockfile hide an entry or field. ``read_lockfile`` now
rejects duplicate keys at ANY nesting level with a specific, runtime-neutral
``LockfileFormatError`` that fails closed (it is NOT swallowed into an empty
default like a missing/corrupt file). These tests pin that contract and prove
the execution/mutation boundaries reach no side effect on a duplicate-key file.

Scope note: this slice is ONLY duplicate-key rejection — no structure digest.
"""

from __future__ import annotations

import json

import pytest

from agentnode_sdk.exceptions import LockfileFormatError
from agentnode_sdk.installer import LOCKFILE_VERSION, read_lockfile, update_lockfile

VALID = (
    '{"lockfile_version":"0.1","updated_at":"",'
    '"packages":{"a-pack":{"version":"1.0.0","_integrity":{"algorithm":"sha256",'
    '"canonical_version":1,"hash":"abc"}}}}'
)


def _write(path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Duplicate keys → LockfileFormatError, at every nesting level                 #
# --------------------------------------------------------------------------- #

def test_duplicate_top_level_key(tmp_path):
    lf = tmp_path / "agentnode.lock"
    _write(lf, '{"packages":{},"packages":{}}')
    with pytest.raises(LockfileFormatError):
        read_lockfile(lf)


def test_duplicate_package_slug(tmp_path):
    lf = tmp_path / "agentnode.lock"
    _write(lf, '{"lockfile_version":"0.1","packages":{"a-pack":{"version":"1"},"a-pack":{"version":"2"}}}')
    with pytest.raises(LockfileFormatError):
        read_lockfile(lf)


def test_duplicate_field_in_entry(tmp_path):
    lf = tmp_path / "agentnode.lock"
    _write(lf, '{"lockfile_version":"0.1","packages":{"a-pack":{"version":"1","version":"2"}}}')
    with pytest.raises(LockfileFormatError):
        read_lockfile(lf)


def test_duplicate_field_in_integrity(tmp_path):
    lf = tmp_path / "agentnode.lock"
    _write(lf, '{"lockfile_version":"0.1","packages":{"a-pack":{"_integrity":{"hash":"x","hash":"y"}}}}')
    with pytest.raises(LockfileFormatError):
        read_lockfile(lf)


def test_duplicate_key_in_unknown_nested_object(tmp_path):
    lf = tmp_path / "agentnode.lock"
    _write(lf, '{"lockfile_version":"0.1","packages":{},"meta":{"k":1,"k":2}}')
    with pytest.raises(LockfileFormatError):
        read_lockfile(lf)


def test_first_duplicate_aborts(tmp_path):
    """The FIRST duplicate raises — no last/first-wins collapse happens."""
    lf = tmp_path / "agentnode.lock"
    _write(lf, '{"packages":{},"x":1,"x":2,"y":3,"y":4}')
    with pytest.raises(LockfileFormatError):
        read_lockfile(lf)


# --------------------------------------------------------------------------- #
# Valid / existing behaviours preserved                                        #
# --------------------------------------------------------------------------- #

def test_valid_lockfile_unchanged(tmp_path):
    lf = tmp_path / "agentnode.lock"
    _write(lf, VALID)
    data = read_lockfile(lf)
    assert data == json.loads(VALID)
    assert data["packages"]["a-pack"]["version"] == "1.0.0"


def test_array_with_identical_values_allowed(tmp_path):
    """Duplicate *array* values are legal JSON — arrays are not objects."""
    lf = tmp_path / "agentnode.lock"
    _write(lf, '{"packages":{"a-pack":{"tags":["x","x","x"]}}}')
    data = read_lockfile(lf)
    assert data["packages"]["a-pack"]["tags"] == ["x", "x", "x"]


def test_missing_file_returns_default(tmp_path):
    lf = tmp_path / "does-not-exist.lock"
    data = read_lockfile(lf)
    assert data == {"lockfile_version": LOCKFILE_VERSION, "updated_at": "", "packages": {}}


def test_invalid_json_stays_failsoft(tmp_path):
    """Syntactically corrupt JSON keeps the existing fail-soft empty default."""
    lf = tmp_path / "agentnode.lock"
    _write(lf, '{not valid json')
    data = read_lockfile(lf)
    assert data == {"lockfile_version": LOCKFILE_VERSION, "updated_at": "", "packages": {}}


def test_duplicate_key_is_not_a_jsondecodeerror(tmp_path):
    """A duplicate-key file must raise LockfileFormatError, NOT be treated as a
    JSONDecodeError and collapsed into an empty default lockfile."""
    lf = tmp_path / "agentnode.lock"
    _write(lf, '{"packages":{},"packages":{}}')
    with pytest.raises(LockfileFormatError):
        read_lockfile(lf)
    assert not isinstance(LockfileFormatError("c", "m"), json.JSONDecodeError)


# --------------------------------------------------------------------------- #
# No write / no side effect at the boundaries                                  #
# --------------------------------------------------------------------------- #

def test_update_lockfile_does_not_write_on_duplicate(tmp_path):
    lf = tmp_path / "agentnode.lock"
    _write(lf, '{"lockfile_version":"0.1","packages":{"a-pack":{"v":1},"a-pack":{"v":2}}}')
    before = lf.read_bytes()
    with pytest.raises(LockfileFormatError):
        update_lockfile("b-pack", {"version": "1.0.0"}, path=lf)
    assert lf.read_bytes() == before  # nothing written or overwritten


def test_run_tool_reaches_no_runner_on_duplicate(tmp_path, monkeypatch):
    from agentnode_sdk.runner import run_tool
    import agentnode_sdk.runtimes.python_runner as pr

    lf = tmp_path / "agentnode.lock"
    _write(lf, '{"packages":{"a-pack":{"runtime":"python"},"a-pack":{"runtime":"python"}}}')

    called = {"python": False}
    monkeypatch.setattr(pr, "run_python", lambda *a, **k: called.__setitem__("python", True))

    result = run_tool("a-pack", lockfile_path=lf)
    assert result.success is False
    assert result.mode_used == "lockfile_error"
    assert called["python"] is False  # never dispatched


def test_mcp_doctor_starts_no_server_on_duplicate(tmp_path, monkeypatch):
    from agentnode_sdk.cli import mcp_commands
    from agentnode_sdk.runtimes import mcp_runner

    lf = tmp_path / "agentnode.lock"
    _write(lf, '{"packages":{"m-pack":{"runtime":"mcp","mcp_command":["x"]},"m-pack":{"runtime":"mcp"}}}')
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lf))

    started = {"n": 0}
    monkeypatch.setattr(
        mcp_runner.MCPServerProcess, "start",
        lambda self, *a, **k: started.__setitem__("n", started["n"] + 1),
    )

    rc = mcp_commands.cmd_mcp_doctor("m-pack", json_output=True)
    assert rc == 1
    assert started["n"] == 0  # no MCP server started


# --------------------------------------------------------------------------- #
# Error surface: deterministic, no values / file content                       #
# --------------------------------------------------------------------------- #

def test_error_message_has_key_but_no_values(tmp_path):
    lf = tmp_path / "agentnode.lock"
    _write(lf, '{"packages":{"a-pack":{"token":"SUPERSECRET","token":"OTHERSECRET"}}}')
    with pytest.raises(LockfileFormatError) as ei:
        read_lockfile(lf)
    msg = str(ei.value)
    # ONLY the duplicate key is surfaced (a duplicate package key could itself be a
    # slug — that is intentional); never a value or full file content.
    assert "token" in msg
    assert "SUPERSECRET" not in msg
    assert "OTHERSECRET" not in msg


# --------------------------------------------------------------------------- #
# Central CLI boundary: one generic translation (no raw traceback)             #
# --------------------------------------------------------------------------- #

def test_readonly_cli_path_translates_error_without_traceback(tmp_path, monkeypatch, capsys):
    """A read-only lockfile command (NOT cmd_mcp_doctor) that lets the error
    propagate is translated by the central main() handler into a deterministic
    message + non-zero exit — never a raw traceback."""
    from agentnode_sdk.cli.main import main

    lf = tmp_path / "agentnode.lock"
    _write(lf, '{"packages":{},"packages":{}}')
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lf))

    rc = main(["lock", "verify"])          # read-only; cmd_lock_verify → read_lockfile
    assert rc == 1                          # non-zero, did NOT raise
    err = capsys.readouterr().err
    assert "duplicate key" in err           # deterministic message
    assert "Traceback" not in err           # not a raw traceback
