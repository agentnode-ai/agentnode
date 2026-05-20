"""Tests for agentnode lock seal / lock verify CLI — Phase 15.2."""
import json
import os
from pathlib import Path

import pytest

from agentnode_sdk.cli.main import main
from agentnode_sdk.installer import read_lockfile, LOCKFILE_VERSION
from agentnode_sdk.lock_integrity import seal_entry, verify_entry


@pytest.fixture()
def tmp_lockfile(tmp_path, monkeypatch):
    """Point AGENTNODE_LOCKFILE to a temp file and return its path."""
    lf = tmp_path / "agentnode.lock"
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lf))
    return lf


def _write_lockfile(path: Path, packages: dict) -> None:
    data = {
        "lockfile_version": LOCKFILE_VERSION,
        "updated_at": "2026-05-20T00:00:00+00:00",
        "packages": packages,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _make_entry(**overrides) -> dict:
    entry = {
        "version": "1.0.0",
        "package_type": "toolpack",
        "runtime": "python",
        "entrypoint": "my_pack.tool",
        "artifact_hash": "sha256:abc123",
        "tools": [],
        "permissions": {"network_level": "none"},
        "installed_at": "2026-05-20T00:00:00+00:00",
        "trust_level": "trusted",
        "source": "sdk",
        "capability_ids": [],
        "prompts": [],
        "resources": [],
        "connector": None,
        "agent": None,
    }
    entry.update(overrides)
    return entry


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

class TestRouting:
    def test_lock_seal_routes(self, tmp_lockfile, capsys):
        _write_lockfile(tmp_lockfile, {"test-pack": _make_entry()})
        rc = main(["lock", "seal"])
        assert rc == 0

    def test_lock_verify_routes(self, tmp_lockfile, capsys):
        _write_lockfile(tmp_lockfile, {"test-pack": seal_entry(_make_entry())})
        rc = main(["lock", "verify"])
        assert rc == 0

    def test_lock_no_subcommand_shows_help(self, capsys):
        rc = main(["lock"])
        assert rc == 1


# ---------------------------------------------------------------------------
# lock seal
# ---------------------------------------------------------------------------

class TestLockSeal:
    def test_seal_adds_integrity_to_missing(self, tmp_lockfile, capsys):
        _write_lockfile(tmp_lockfile, {
            "pack-a": _make_entry(),
            "pack-b": _make_entry(version="2.0.0"),
        })
        rc = main(["lock", "seal"])
        assert rc == 0

        lock = read_lockfile(tmp_lockfile)
        for slug in ("pack-a", "pack-b"):
            entry = lock["packages"][slug]
            assert "_integrity" in entry
            assert verify_entry(slug, entry).status == "verified"

    def test_seal_without_force_preserves_existing(self, tmp_lockfile, capsys):
        sealed = seal_entry(_make_entry())
        original_hash = sealed["_integrity"]["hash"]
        _write_lockfile(tmp_lockfile, {"test-pack": sealed})

        rc = main(["lock", "seal"])
        assert rc == 0

        lock = read_lockfile(tmp_lockfile)
        assert lock["packages"]["test-pack"]["_integrity"]["hash"] == original_hash
        out = capsys.readouterr().out
        assert "unchanged" in out

    def test_seal_force_recomputes(self, tmp_lockfile, capsys):
        sealed = seal_entry(_make_entry())
        sealed["_integrity"]["hash"] = "0" * 64  # corrupt
        _write_lockfile(tmp_lockfile, {"test-pack": sealed})

        rc = main(["lock", "seal", "--force"])
        assert rc == 0

        lock = read_lockfile(tmp_lockfile)
        result = verify_entry("test-pack", lock["packages"]["test-pack"])
        assert result.status == "verified"
        out = capsys.readouterr().out
        assert "resealed" in out

    def test_seal_empty_lockfile(self, tmp_lockfile, capsys):
        _write_lockfile(tmp_lockfile, {})
        rc = main(["lock", "seal"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "No packages" in out

    def test_seal_writes_valid_lockfile(self, tmp_lockfile):
        _write_lockfile(tmp_lockfile, {"pack-a": _make_entry()})
        main(["lock", "seal"])

        lock = read_lockfile(tmp_lockfile)
        assert "lockfile_version" in lock
        assert "updated_at" in lock
        assert "packages" in lock
        assert "_integrity" in lock["packages"]["pack-a"]

    def test_seal_mixed_entries(self, tmp_lockfile, capsys):
        """Some entries sealed, some not. Only unsealed get sealed."""
        sealed = seal_entry(_make_entry(version="1.0.0"))
        unsealed = _make_entry(version="2.0.0")
        _write_lockfile(tmp_lockfile, {
            "sealed-pack": sealed,
            "unsealed-pack": unsealed,
        })

        rc = main(["lock", "seal"])
        assert rc == 0

        lock = read_lockfile(tmp_lockfile)
        assert verify_entry("sealed-pack", lock["packages"]["sealed-pack"]).status == "verified"
        assert verify_entry("unsealed-pack", lock["packages"]["unsealed-pack"]).status == "verified"

        out = capsys.readouterr().out
        assert "unsealed-pack: sealed" in out
        assert "1 sealed" in out
        assert "1 unchanged" in out


# ---------------------------------------------------------------------------
# lock verify
# ---------------------------------------------------------------------------

class TestLockVerify:
    def test_verify_all_sealed_returns_0(self, tmp_lockfile, capsys):
        _write_lockfile(tmp_lockfile, {
            "pack-a": seal_entry(_make_entry()),
            "pack-b": seal_entry(_make_entry(version="2.0.0")),
        })
        rc = main(["lock", "verify"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "verified" in out

    def test_verify_mismatch_returns_1(self, tmp_lockfile, capsys):
        sealed = seal_entry(_make_entry())
        sealed["entrypoint"] = "tampered.module"
        _write_lockfile(tmp_lockfile, {"bad-pack": sealed})

        rc = main(["lock", "verify"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "MISMATCH" in out

    def test_verify_missing_returns_0_by_default(self, tmp_lockfile, capsys):
        _write_lockfile(tmp_lockfile, {"unsealed-pack": _make_entry()})
        rc = main(["lock", "verify"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "missing" in out

    def test_verify_missing_strict_returns_1(self, tmp_lockfile, capsys):
        _write_lockfile(tmp_lockfile, {"unsealed-pack": _make_entry()})
        rc = main(["lock", "verify", "--strict"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "MISSING" in out

    def test_verify_json_output(self, tmp_lockfile, capsys):
        sealed = seal_entry(_make_entry())
        tampered = seal_entry(_make_entry(version="2.0.0"))
        tampered["runtime"] = "remote"
        _write_lockfile(tmp_lockfile, {
            "good-pack": sealed,
            "bad-pack": tampered,
            "no-seal-pack": _make_entry(version="3.0.0"),
        })

        rc = main(["lock", "verify", "--json"])
        assert rc == 1

        out = capsys.readouterr().out
        report = json.loads(out)
        assert "good-pack" in report["verified"]
        assert "bad-pack" in report["mismatch"]
        assert "no-seal-pack" in report["missing"]
        assert report["total"] == 3
        assert report["ok"] is False

    def test_verify_json_strict_missing(self, tmp_lockfile, capsys):
        _write_lockfile(tmp_lockfile, {"unsealed": _make_entry()})
        rc = main(["lock", "verify", "--json", "--strict"])
        assert rc == 1

        report = json.loads(capsys.readouterr().out)
        assert report["ok"] is False
        assert "unsealed" in report["missing"]

    def test_verify_json_all_ok(self, tmp_lockfile, capsys):
        _write_lockfile(tmp_lockfile, {"good": seal_entry(_make_entry())})
        rc = main(["lock", "verify", "--json"])
        assert rc == 0

        report = json.loads(capsys.readouterr().out)
        assert report["ok"] is True

    def test_verify_empty_lockfile(self, tmp_lockfile, capsys):
        _write_lockfile(tmp_lockfile, {})
        rc = main(["lock", "verify"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "No packages" in out

    def test_verify_does_not_modify_lockfile(self, tmp_lockfile):
        _write_lockfile(tmp_lockfile, {"test-pack": _make_entry()})
        content_before = tmp_lockfile.read_text(encoding="utf-8")

        main(["lock", "verify"])

        content_after = tmp_lockfile.read_text(encoding="utf-8")
        assert content_before == content_after
