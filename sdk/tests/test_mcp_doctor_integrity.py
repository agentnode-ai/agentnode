"""cmd_mcp_doctor two-stage integrity gate — 0.2A-2c.

The doctor bypasses run_tool, so it carries its OWN gate (same core matrix): a
fail-closed read + entry-from-snapshot + evaluate/enforce BEFORE any Node/npx
probe or MCP server start. Normal mode allows a readable-but-not-verified state
(one warning + audit, then start); strict denies without starting; hard read /
base-model errors and an absent package deny in both modes. Read-only.
"""
import json
from unittest import mock

import pytest

from agentnode_sdk.cli.mcp_commands import cmd_mcp_doctor
from agentnode_sdk.lock_integrity import seal_entry, seal_structure


def _mcp_entry(**over) -> dict:
    e = {
        "version": "0.1.0",
        "runtime": "mcp",
        "mcp_command": ["npx", "-y", "test-server@1.0.0"],
        "mcp_env_keys": [],
        "tools": [{"name": "t"}],
        "permissions": {},
    }
    e.update(over)
    return e


def _sealed_mcp(**over) -> dict:
    return seal_entry(_mcp_entry(**over))


def _drifted_mcp(**over) -> dict:
    e = _sealed_mcp(**over)
    e["version"] = e["version"] + "-drifted"
    return e


def _write(lf, packages, *, structure):
    data = {"lockfile_version": "0.1", "updated_at": "2026-01-01T00:00:00+00:00", "packages": packages}
    if structure:
        data = seal_structure(data)
    lf.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture()
def doctor(tmp_path, monkeypatch):
    """Mock + COUNT every downstream side-effect: node/npx probe (shutil.which),
    version subprocess, MCPServerProcess construction + start, plus a write spy."""
    import agentnode_sdk._fileutil as fu
    import agentnode_sdk.cli.mcp_commands as mc
    import agentnode_sdk.runtimes.mcp_runner as mcpr

    state = {"starts": 0, "writes": 0, "which": 0, "subproc": 0, "ctor": 0,
             "lf": tmp_path / "agentnode.lock"}

    def _which(n):
        state["which"] += 1
        return f"/usr/bin/{n}"
    monkeypatch.setattr(mc.shutil, "which", _which)

    def _sub(*a, **k):
        state["subproc"] += 1
        return mock.Mock(stdout="v20.0.0\n")
    monkeypatch.setattr(mc.subprocess, "run", _sub)

    real_ctor = mcpr.MCPServerProcess.__init__

    def _ctor(self, *a, **k):
        state["ctor"] += 1
        real_ctor(self, *a, **k)
    monkeypatch.setattr(mcpr.MCPServerProcess, "__init__", _ctor)
    monkeypatch.setattr(mcpr.MCPServerProcess, "start",
                        lambda self, *a, **k: state.__setitem__("starts", state["starts"] + 1))
    monkeypatch.setattr(mcpr.MCPServerProcess, "stop", lambda self, *a, **k: None)
    real_write = fu.atomic_write_json
    monkeypatch.setattr(fu, "atomic_write_json",
                        lambda *a, **k: (state.__setitem__("writes", state["writes"] + 1),
                                         real_write(*a, **k))[1])
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(state["lf"]))
    return state


def _strict(monkeypatch):
    monkeypatch.setenv("AGENTNODE_GUARD_STRICT", "true")


# --------------------------------------------------------------------------- #

class TestDoctorVerified:
    @pytest.mark.parametrize("strict", [False, True], ids=["normal", "strict"])
    def test_verified_starts_once(self, doctor, monkeypatch, strict):
        if strict:
            _strict(monkeypatch)
        _write(doctor["lf"], {"m-pack": _sealed_mcp()}, structure=True)
        before = doctor["lf"].read_bytes()
        rc = cmd_mcp_doctor("m-pack", json_output=True, skip_start=False)
        assert rc == 0 and doctor["starts"] == 1
        assert doctor["writes"] == 0 and doctor["lf"].read_bytes() == before


class TestDoctorStructureMissing:
    def test_normal_allows_and_starts(self, doctor, caplog):
        _write(doctor["lf"], {"m-pack": _sealed_mcp()}, structure=False)
        rc = cmd_mcp_doctor("m-pack", json_output=True, skip_start=False)
        assert rc == 0 and doctor["starts"] == 1
        assert "structure=missing" in caplog.text

    def test_strict_denies_no_start(self, doctor, monkeypatch, capsys):
        _strict(monkeypatch)
        _write(doctor["lf"], {"m-pack": _sealed_mcp()}, structure=False)
        before = doctor["lf"].read_bytes()
        rc = cmd_mcp_doctor("m-pack", json_output=True, skip_start=False)
        assert rc == 1 and doctor["starts"] == 0
        assert doctor["writes"] == 0 and doctor["lf"].read_bytes() == before
        assert "Traceback" not in capsys.readouterr().out


class TestDoctorEntryMismatch:
    def test_normal_allows(self, doctor):
        _write(doctor["lf"], {"m-pack": _drifted_mcp()}, structure=True)   # entry mismatch, struct verified
        assert cmd_mcp_doctor("m-pack", json_output=True, skip_start=False) == 0
        assert doctor["starts"] == 1

    def test_strict_denies(self, doctor, monkeypatch):
        _strict(monkeypatch)
        _write(doctor["lf"], {"m-pack": _drifted_mcp()}, structure=True)
        assert cmd_mcp_doctor("m-pack", json_output=True, skip_start=False) == 1
        assert doctor["starts"] == 0


class TestDoctorStructureDrift:
    def _corrupt(self, doctor, how):
        _write(doctor["lf"], {"m-pack": _sealed_mcp()}, structure=True)
        d = json.loads(doctor["lf"].read_text(encoding="utf-8"))
        if how == "mismatch":
            d["structure_digest"]["hash"] = "0" * 64
        elif how == "unsupported":
            d["structure_digest"]["canonicalization_version"] = 99
        elif how == "invalid":
            d["structure_digest"] = "nope"
        doctor["lf"].write_text(json.dumps(d), encoding="utf-8")

    @pytest.mark.parametrize("how", ["mismatch", "invalid", "unsupported"])
    def test_normal_allows(self, doctor, how):
        self._corrupt(doctor, how)
        assert cmd_mcp_doctor("m-pack", json_output=True, skip_start=False) == 0
        assert doctor["starts"] == 1

    @pytest.mark.parametrize("how", ["mismatch", "invalid", "unsupported"])
    def test_strict_denies(self, doctor, monkeypatch, how):
        self._corrupt(doctor, how)
        _strict(monkeypatch)
        assert cmd_mcp_doctor("m-pack", json_output=True, skip_start=False) == 1
        assert doctor["starts"] == 0


class TestDoctorHardErrorsAndAbsent:
    @pytest.mark.parametrize("strict", [False, True], ids=["normal", "strict"])
    def test_missing_package_denies_no_start(self, doctor, monkeypatch, strict):
        if strict:
            _strict(monkeypatch)
        _write(doctor["lf"], {"other": _sealed_mcp()}, structure=True)
        rc = cmd_mcp_doctor("m-pack", json_output=True, skip_start=False)
        assert rc == 1 and doctor["starts"] == 0

    @pytest.mark.parametrize("body", [
        '{"lockfile_version":"0.1","packages":{},"packages":{}}',   # dup key
        "{not valid json",                                          # malformed JSON
        '{"packages":{"m-pack":{}}}',                               # missing lockfile_version
        '["x"]',                                                    # non-object lockfile
    ], ids=["dupkey", "badjson", "no-version", "top-list"])
    @pytest.mark.parametrize("strict", [False, True], ids=["normal", "strict"])
    def test_read_error_denies_no_start(self, doctor, monkeypatch, body, strict):
        if strict:
            _strict(monkeypatch)
        doctor["lf"].write_text(body, encoding="utf-8")
        before = doctor["lf"].read_bytes()
        rc = cmd_mcp_doctor("m-pack", json_output=True, skip_start=False)
        assert rc == 1 and doctor["starts"] == 0
        assert doctor["writes"] == 0 and doctor["lf"].read_bytes() == before


class TestDoctorSnapshot:
    def test_exactly_one_read(self, doctor, monkeypatch):
        import agentnode_sdk.runtime_integrity as ri
        reads = {"n": 0}
        real = ri.read_lockfile_strict
        monkeypatch.setattr(ri, "read_lockfile_strict",
                            lambda *a, **k: (reads.__setitem__("n", reads["n"] + 1), real(*a, **k))[1])
        _write(doctor["lf"], {"m-pack": _sealed_mcp()}, structure=True)
        cmd_mcp_doctor("m-pack", json_output=True, skip_start=False)
        assert reads["n"] == 1

    def test_no_fallsoft_reader(self, doctor, monkeypatch):
        # The OLD fail-soft installer.read_lockfile must never be called.
        import agentnode_sdk.installer as inst
        monkeypatch.setattr(inst, "read_lockfile",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("fail-soft reader called")))
        _write(doctor["lf"], {"m-pack": _sealed_mcp()}, structure=True)
        assert cmd_mcp_doctor("m-pack", json_output=True, skip_start=False) == 0
        assert doctor["starts"] == 1


class TestDoctorNoProbeOnDeny:
    def test_strict_deny_zero_probes_and_start(self, doctor, monkeypatch):
        _strict(monkeypatch)
        _write(doctor["lf"], {"m-pack": _sealed_mcp()}, structure=False)   # strict deny
        rc = cmd_mcp_doctor("m-pack", json_output=True, skip_start=False)
        assert rc == 1
        assert doctor["which"] == 0 and doctor["subproc"] == 0
        assert doctor["ctor"] == 0 and doctor["starts"] == 0               # no probe / no server

    def test_read_error_zero_probes_and_start(self, doctor):
        doctor["lf"].write_text('{"lockfile_version":"0.1","packages":{},"packages":{}}', encoding="utf-8")
        rc = cmd_mcp_doctor("m-pack", json_output=True, skip_start=False)
        assert rc == 1
        assert doctor["which"] == 0 and doctor["subproc"] == 0
        assert doctor["ctor"] == 0 and doctor["starts"] == 0

    def test_absent_zero_probes_and_start(self, doctor):
        _write(doctor["lf"], {"other": _sealed_mcp()}, structure=True)
        rc = cmd_mcp_doctor("m-pack", json_output=True, skip_start=False)
        assert rc == 1
        assert doctor["which"] == 0 and doctor["subproc"] == 0
        assert doctor["ctor"] == 0 and doctor["starts"] == 0


class TestDoctorAuditFailure:
    @staticmethod
    def _raise_on_integrity(monkeypatch):
        import agentnode_sdk.runner as runner

        def _audit(decision, event, slug, **k):
            if event == "lock_integrity_check":
                raise RuntimeError("audit backend down")
        monkeypatch.setattr(runner, "audit_decision", _audit)

    def test_normal_allow_survives_audit_failure(self, doctor, monkeypatch):
        self._raise_on_integrity(monkeypatch)
        _write(doctor["lf"], {"m-pack": _sealed_mcp()}, structure=False)   # not verified
        assert cmd_mcp_doctor("m-pack", json_output=True, skip_start=False) == 0
        assert doctor["starts"] == 1                                       # still starts once

    def test_strict_deny_survives_audit_failure(self, doctor, monkeypatch):
        self._raise_on_integrity(monkeypatch)
        _strict(monkeypatch)
        _write(doctor["lf"], {"m-pack": _sealed_mcp()}, structure=False)
        assert cmd_mcp_doctor("m-pack", json_output=True, skip_start=False) == 1
        assert doctor["starts"] == 0                                       # decision unchanged
