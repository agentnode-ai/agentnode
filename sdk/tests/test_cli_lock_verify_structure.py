"""Slice 0.2A-2b — `agentnode lock verify` extended with the global structure check.

Read-only, deterministic, two-stage integrity: every per-entry `_integrity` AND
the global `structure_digest`. The check must NEVER seal, write, restamp, or
audit the lockfile, and a corrupt/invalid lockfile must fail closed (exit 1),
never be silently treated as empty.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentnode_sdk.cli.main import main
from agentnode_sdk.installer import LOCKFILE_VERSION
from agentnode_sdk.lock_integrity import seal_entry, seal_structure

EMPTY_SET_DIGEST = "57eae92f23a97fd2292d317ea26c67a68464eee9b488f39652584804ca883d5d"


def _sealed_entry(**over) -> dict:
    e = {
        "version": "1.0.0",
        "package_type": "toolpack",
        "runtime": "python",
        "entrypoint": "m.tool",
        "artifact_hash": "sha256:x",
        "tools": [],
        "permissions": {"network_level": "none"},
    }
    e.update(over)
    return seal_entry(e)


def _drifted_entry(**over) -> dict:
    """Sealed entry whose CONTENT was mutated after sealing → verify_entry
    'mismatch', but _integrity (and the structure digest over it) unchanged."""
    e = _sealed_entry(**over)
    e["version"] = e["version"] + "-drifted"
    return e


def _write_lock(lf, packages, *, structure=False, updated_at="2026-01-01T00:00:00+00:00"):
    data = {"lockfile_version": LOCKFILE_VERSION, "updated_at": updated_at, "packages": packages}
    if structure:
        data = seal_structure(data)
    lf.write_text(json.dumps(data, indent=2), encoding="utf-8")


@pytest.fixture()
def env_lock(tmp_path, monkeypatch):
    p = tmp_path / "agentnode.lock"
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(p))
    return p


def _write_spy(monkeypatch):
    import agentnode_sdk._fileutil as fu
    calls = {"n": 0}
    monkeypatch.setattr(fu, "atomic_write_json", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    return calls


def _no_write_guard(monkeypatch):
    """Make every mutation / seal / audit primitive explode if lock verify calls it.
    Install AFTER the lockfile fixture is written (setup uses seal_structure)."""
    import agentnode_sdk._fileutil as fu
    import agentnode_sdk.cli.commands as cmds
    import agentnode_sdk.installer as inst
    import agentnode_sdk.lock_integrity as li

    def boom(name):
        def _b(*a, **k):
            raise AssertionError(f"lock verify must not call {name}")
        return _b

    monkeypatch.setattr(fu, "atomic_write_json", boom("atomic_write_json"))
    monkeypatch.setattr(li, "seal_entry", boom("seal_entry"))
    monkeypatch.setattr(li, "seal_structure", boom("seal_structure"))
    monkeypatch.setattr(inst, "mutate_lockfile", boom("mutate_lockfile"))
    monkeypatch.setattr(inst, "update_lockfile", boom("update_lockfile"))
    monkeypatch.setattr(inst, "remove_from_lockfile", boom("remove_from_lockfile"))
    monkeypatch.setattr(inst, "_audit_structure_event", boom("_audit_structure_event"))
    monkeypatch.setattr(cmds, "_lock_seal_audit", boom("_lock_seal_audit"))


# --------------------------------------------------------------------------- #
# Success                                                                       #
# --------------------------------------------------------------------------- #

class TestVerifySuccess:
    @pytest.mark.parametrize("flags", [[], ["--strict"]], ids=["normal", "strict"])
    def test_fully_verified_exit_0(self, env_lock, capsys, flags):
        _write_lock(env_lock, {"b-pack": _sealed_entry(version="2.0.0"),
                               "a-pack": _sealed_entry(version="1.0.0")}, structure=True)
        rc = main(["lock", "verify", *flags])
        out = capsys.readouterr().out
        assert rc == 0
        assert "a-pack" in out and "b-pack" in out
        assert out.index("a-pack") < out.index("b-pack")     # deterministic raw-slug order
        assert "structure: verified" in out

    @pytest.mark.parametrize("flags", [[], ["--strict"]], ids=["normal", "strict"])
    def test_empty_verified_empty_set_digest_exit_0(self, env_lock, capsys, flags):
        _write_lock(env_lock, {}, structure=True)            # empty + empty-set digest
        rc = main(["lock", "verify", *flags])
        out = capsys.readouterr().out
        assert rc == 0
        assert "structure: verified" in out                  # structure shown even when empty


# --------------------------------------------------------------------------- #
# Migration states (missing structure / unsealed entries)                       #
# --------------------------------------------------------------------------- #

class TestVerifyMigration:
    def test_structure_missing_all_verified_normal_warns_exit_0(self, env_lock, capsys):
        _write_lock(env_lock, {"a-pack": _sealed_entry()})   # sealed entry, NO structure digest
        rc = main(["lock", "verify"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "structure: missing (not sealed)" in out

    def test_structure_missing_all_verified_strict_exit_1(self, env_lock, capsys):
        _write_lock(env_lock, {"a-pack": _sealed_entry()})
        rc = main(["lock", "verify", "--strict"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "structure: MISSING (strict)" in out

    def test_entry_and_structure_missing_normal_warns(self, env_lock, capsys):
        e = _sealed_entry()
        del e["_integrity"]                                  # unsealed entry
        _write_lock(env_lock, {"a-pack": e})                # + no structure digest
        rc = main(["lock", "verify"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "a-pack" in out and "missing (not sealed)" in out   # entry warning
        assert "structure: missing (not sealed)" in out            # structure warning

    def test_entry_and_structure_missing_strict_exit_1(self, env_lock, capsys):
        e = _sealed_entry()
        del e["_integrity"]
        _write_lock(env_lock, {"a-pack": e})
        rc = main(["lock", "verify", "--strict"])
        assert rc == 1

    def test_combined_warnings_all_shown(self, env_lock, capsys):
        sealed = _sealed_entry(version="1.0.0")
        unsealed = _sealed_entry(version="2.0.0")
        del unsealed["_integrity"]
        _write_lock(env_lock, {"sealed-pack": sealed, "unsealed-pack": unsealed})  # no digest
        rc = main(["lock", "verify"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "sealed-pack" in out and "unsealed-pack" in out     # both entries shown
        assert "structure: missing (not sealed)" in out


# --------------------------------------------------------------------------- #
# Drift + invalid states → exit 1                                               #
# --------------------------------------------------------------------------- #

class TestVerifyDriftInvalid:
    def test_entry_content_drift_structure_verified_exit_1(self, env_lock, capsys):
        _write_lock(env_lock, {"a-pack": _drifted_entry(version="1.0.0"),
                               "b-pack": _sealed_entry(version="1.0.0")}, structure=True)
        rc = main(["lock", "verify"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "a-pack" in out and "MISMATCH" in out          # entry drift visible
        assert "structure: verified" in out                   # global digest still verified

    def test_structure_mismatch_verified_entries_exit_1(self, env_lock, capsys):
        _write_lock(env_lock, {"a-pack": _sealed_entry()}, structure=True)
        d = json.loads(env_lock.read_text(encoding="utf-8"))
        d["structure_digest"]["hash"] = "0" * 64
        env_lock.write_text(json.dumps(d, indent=2), encoding="utf-8")
        rc = main(["lock", "verify"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "structure: MISMATCH" in out

    def test_structure_unsupported_exit_1(self, env_lock, capsys):
        _write_lock(env_lock, {"a-pack": _sealed_entry()}, structure=True)
        d = json.loads(env_lock.read_text(encoding="utf-8"))
        d["structure_digest"]["canonicalization_version"] = 99
        env_lock.write_text(json.dumps(d, indent=2), encoding="utf-8")
        rc = main(["lock", "verify"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "structure: UNSUPPORTED" in out

    def test_malformed_structure_digest_invalid_exit_1(self, env_lock, capsys):
        _write_lock(env_lock, {"a-pack": _sealed_entry()}, structure=True)
        d = json.loads(env_lock.read_text(encoding="utf-8"))
        d["structure_digest"] = "nope"
        env_lock.write_text(json.dumps(d, indent=2), encoding="utf-8")
        rc = main(["lock", "verify"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "structure: INVALID" in out

    def test_non_object_entry_controlled_exit_1(self, env_lock, capsys):
        env_lock.write_text(
            '{"lockfile_version":"0.1","updated_at":"","packages":{"a-pack":42}}',
            encoding="utf-8",
        )
        rc = main(["lock", "verify"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "a-pack" in out and "INVALID" in out
        assert "Traceback" not in out

    def test_invalid_slug_controlled_exit_1(self, env_lock, capsys):
        env_lock.write_text(
            '{"lockfile_version":"0.1","updated_at":"","packages":{"Bad_Slug":{"version":"1.0.0"}}}',
            encoding="utf-8",
        )
        rc = main(["lock", "verify"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "INVALID" in out
        assert "Traceback" not in out


# --------------------------------------------------------------------------- #
# File / parser errors — fail closed, no leak, no write                         #
# --------------------------------------------------------------------------- #

class TestVerifyFileParserErrors:
    _SECRET = "SECRETTOKENVALUE"

    @pytest.mark.parametrize("body", [
        "{not valid json",                                                  # invalid JSON
        '{"lockfile_version":"0.1","packages":{},"packages":{}}',           # dup key top-level
        '{"lockfile_version":"0.1","packages":{"a-pack":{"t":"SECRETTOKENVALUE","t":"x"}}}',  # dup in entry
        '["not","an","object"]',                                            # top-level list
        '{"lockfile_version":"0.1","packages":[]}',                         # packages as list
        '{"lockfile_version":"0.1","packages":"nope"}',                     # packages as string
        '{"lockfile_version":"0.1","packages":null}',                       # packages null
        '{"packages":{}}',                                                  # missing lockfile_version
        '{"lockfile_version":"9.9","packages":{}}',                         # unsupported version
    ])
    @pytest.mark.parametrize("flags", [[], ["--strict"]], ids=["normal", "strict"])
    def test_fail_closed_exit_1_no_write(self, env_lock, capsys, monkeypatch, body, flags):
        # Parser / base-model errors propagate as LockfileFormatError and are
        # translated by the central main() handler (stderr) — check BOTH streams.
        env_lock.write_text(body, encoding="utf-8")
        before = env_lock.read_bytes()
        spy = _write_spy(monkeypatch)
        rc = main(["lock", "verify", *flags])
        cap = capsys.readouterr()
        combined = cap.out + cap.err
        assert rc == 1
        assert "Traceback" not in combined
        assert self._SECRET not in combined                  # no entry-content leak
        assert str(env_lock) not in combined                 # no path leak
        assert spy["n"] == 0                                 # no write
        assert env_lock.read_bytes() == before               # byte-identical

    def test_oserror_fail_closed(self, env_lock, capsys, monkeypatch):
        _write_lock(env_lock, {"a-pack": _sealed_entry()}, structure=True)
        before = env_lock.read_bytes()
        monkeypatch.setattr(Path, "read_text",
                            lambda self, *a, **k: (_ for _ in ()).throw(OSError("boom")))
        rc = main(["lock", "verify"])
        cap = capsys.readouterr()
        combined = cap.out + cap.err
        assert rc == 1
        assert "Traceback" not in combined and "boom" not in combined
        assert env_lock.read_bytes() == before


# --------------------------------------------------------------------------- #
# Missing file — contract: normal exit 0, strict exit 1                         #
# --------------------------------------------------------------------------- #

class TestVerifyMissingFile:
    def test_missing_file_normal_exit_0(self, env_lock, capsys):
        assert not env_lock.exists()
        rc = main(["lock", "verify"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "No lockfile found" in out

    def test_missing_file_strict_exit_1(self, env_lock, capsys):
        assert not env_lock.exists()
        rc = main(["lock", "verify", "--strict"])
        assert rc == 1


# --------------------------------------------------------------------------- #
# Read-only proofs across states                                                #
# --------------------------------------------------------------------------- #

class TestVerifyReadOnly:
    def _lock_for(self, env_lock, state):
        if state == "verified":
            _write_lock(env_lock, {"a-pack": _sealed_entry()}, structure=True)
        elif state == "missing":
            _write_lock(env_lock, {"a-pack": _sealed_entry()})                    # no digest
        elif state == "mismatch":
            _write_lock(env_lock, {"a-pack": _drifted_entry()}, structure=True)
        elif state == "invalid":
            env_lock.write_text(
                '{"lockfile_version":"0.1","updated_at":"z","packages":{"a-pack":42}}',
                encoding="utf-8")
        elif state == "unsupported":
            _write_lock(env_lock, {"a-pack": _sealed_entry()}, structure=True)
            d = json.loads(env_lock.read_text(encoding="utf-8"))
            d["structure_digest"]["canonicalization_version"] = 99
            env_lock.write_text(json.dumps(d, indent=2), encoding="utf-8")

    @pytest.mark.parametrize("state", ["verified", "missing", "mismatch", "invalid", "unsupported"])
    def test_verify_never_writes_or_audits(self, env_lock, monkeypatch, state):
        self._lock_for(env_lock, state)
        before = env_lock.read_bytes()
        before_updated_at = json.loads(before)["updated_at"]
        _no_write_guard(monkeypatch)                         # any seal/write/audit → AssertionError
        main(["lock", "verify"])                             # rc irrelevant here
        after = env_lock.read_bytes()
        assert after == before                               # byte-identical
        assert json.loads(after)["updated_at"] == before_updated_at
