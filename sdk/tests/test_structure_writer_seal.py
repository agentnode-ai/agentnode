"""Slice 0.2A-2a — lockfile writer + seal integration of the structure digest.

Every production mutation (install/upgrade/remove) goes through the central,
structure-safe writer; `lock seal [--force]` is the deliberate seal/reseal path.
These tests pin the pre-mutation gate, the migration rules, fail-closed reading,
atomic no-write on refusal, and the seal semantics. No runtime / lock-verify
integration here.
"""

from __future__ import annotations

import json

import pytest

from agentnode_sdk.cli.main import main
from agentnode_sdk.exceptions import LockfileFormatError
from agentnode_sdk.installer import (
    LOCKFILE_VERSION,
    read_lockfile,
    read_lockfile_strict,
    remove_from_lockfile,
    update_lockfile,
)
from agentnode_sdk.lock_integrity import (
    seal_entry,
    seal_structure,
    verify_entry,
    verify_structure,
)

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


def _write_lock(lf, packages, *, structure=False, updated_at="2026-01-01T00:00:00+00:00"):
    data = {"lockfile_version": LOCKFILE_VERSION, "updated_at": updated_at, "packages": packages}
    if structure:
        data = seal_structure(data)
    lf.write_text(json.dumps(data, indent=2), encoding="utf-8")


@pytest.fixture()
def lf(tmp_path):
    return tmp_path / "agentnode.lock"


@pytest.fixture()
def env_lock(tmp_path, monkeypatch):
    p = tmp_path / "agentnode.lock"
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(p))
    return p


def _seal_spies(monkeypatch):
    """Record the ordered (lock, read, write) events of ``cmd_lock_seal`` so a
    test can prove the whole transaction runs under one file_lock: the lock is
    acquired BEFORE the strict read, there is exactly one read, and at most one
    write. Returns the mutable event list."""
    import contextlib

    import agentnode_sdk._fileutil as fu
    import agentnode_sdk.installer as inst

    events: list[str] = []
    real_lock = fu.file_lock
    real_read = inst.read_lockfile_strict
    real_write = fu.atomic_write_json

    @contextlib.contextmanager
    def spy_lock(*a, **k):
        events.append("lock")
        with real_lock(*a, **k):
            yield

    def spy_read(*a, **k):
        events.append("read")
        return real_read(*a, **k)

    def spy_write(*a, **k):
        events.append("write")
        return real_write(*a, **k)

    monkeypatch.setattr(fu, "file_lock", spy_lock)
    monkeypatch.setattr(inst, "read_lockfile_strict", spy_read)
    monkeypatch.setattr(fu, "atomic_write_json", spy_write)
    return events


# --------------------------------------------------------------------------- #
# Writer + migration                                                           #
# --------------------------------------------------------------------------- #

class TestWriter:
    def test_new_lockfile_gets_entry_and_structure(self, lf):
        update_lockfile("a-pack", _sealed_entry(), path=lf)
        lock = read_lockfile(lf)
        assert verify_entry("a-pack", lock["packages"]["a-pack"]).status == "verified"
        assert verify_structure(lock) == "verified"

    def test_verified_lockfile_install_upgrade_remove(self, lf):
        update_lockfile("a-pack", _sealed_entry(version="1.0.0"), path=lf)      # install
        assert verify_structure(read_lockfile(lf)) == "verified"
        update_lockfile("b-pack", _sealed_entry(version="1.0.0"), path=lf)      # install b
        assert verify_structure(read_lockfile(lf)) == "verified"
        update_lockfile("a-pack", _sealed_entry(version="2.0.0"), path=lf)      # upgrade a
        lock = read_lockfile(lf)
        assert lock["packages"]["a-pack"]["version"] == "2.0.0"
        assert verify_structure(lock) == "verified"
        removed = remove_from_lockfile("b-pack", path=lf)                       # remove b
        assert removed is not None
        lock = read_lockfile(lf)
        assert "b-pack" not in lock["packages"]
        assert verify_structure(lock) == "verified"

    def test_migration_all_sealed_first_seal(self, lf):
        _write_lock(lf, {"a-pack": _sealed_entry()})           # sealed entries, NO digest
        assert verify_structure(read_lockfile(lf)) == "missing"
        update_lockfile("b-pack", _sealed_entry(), path=lf)    # migration mutation
        lock = read_lockfile(lf)
        assert "b-pack" in lock["packages"] and "structure_digest" in lock
        assert verify_structure(lock) == "verified"

    def test_migration_refused_when_old_entry_unsealed(self, lf):
        _write_lock(lf, {"old-pack": {"version": "1.0.0"}})    # unsealed old entry, no digest
        before = lf.read_bytes()
        with pytest.raises(LockfileFormatError):
            update_lockfile("b-pack", _sealed_entry(), path=lf)
        assert lf.read_bytes() == before                        # byte-identical, no write

    @pytest.mark.parametrize("break_fn", [
        lambda d: d["structure_digest"].__setitem__("hash", "0" * 64),          # mismatch
        lambda d: d["structure_digest"].__setitem__("canonicalization_version", 99),  # unsupported
        lambda d: d.__setitem__("structure_digest", "nope"),                    # invalid
    ])
    def test_mutation_refused_on_structure_drift(self, lf, break_fn):
        _write_lock(lf, {"a-pack": _sealed_entry()}, structure=True)
        d = json.loads(lf.read_text(encoding="utf-8"))
        break_fn(d)
        lf.write_text(json.dumps(d, indent=2), encoding="utf-8")
        before = lf.read_bytes()
        with pytest.raises(LockfileFormatError):
            update_lockfile("b-pack", _sealed_entry(), path=lf)
        assert lf.read_bytes() == before

    def test_remove_last_entry_empty_set_digest(self, lf):
        update_lockfile("a-pack", _sealed_entry(), path=lf)
        remove_from_lockfile("a-pack", path=lf)
        lock = read_lockfile(lf)
        assert lock["packages"] == {}
        assert verify_structure(lock) == "verified"
        assert lock["structure_digest"]["hash"] == EMPTY_SET_DIGEST


# --------------------------------------------------------------------------- #
# Corrupted inputs: fail closed, never write                                   #
# --------------------------------------------------------------------------- #

class TestCorruptedInputsNoWrite:
    @pytest.mark.parametrize("text", [
        "{not valid json",                                        # invalid JSON
        '{"packages":{},"packages":{}}',                          # duplicate key
        '["not","an","object"]',                                  # non-object lockfile
        '{"lockfile_version":"0.1","packages":[]}',               # invalid packages (not a dict)
    ])
    def test_update_refuses_and_does_not_write(self, lf, text):
        lf.write_text(text, encoding="utf-8")
        before = lf.read_bytes()
        with pytest.raises(LockfileFormatError):
            update_lockfile("b-pack", _sealed_entry(), path=lf)
        assert lf.read_bytes() == before

    def test_no_atomic_write_on_refusal(self, lf, monkeypatch):
        import agentnode_sdk._fileutil as fu
        called = {"n": 0}
        monkeypatch.setattr(fu, "atomic_write_json", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
        lf.write_text('{"packages":{},"packages":{}}', encoding="utf-8")
        with pytest.raises(LockfileFormatError):
            update_lockfile("b-pack", _sealed_entry(), path=lf)
        assert called["n"] == 0

    def test_strict_reader_oserror_refuses(self, lf, monkeypatch):
        from pathlib import Path
        lf.write_text('{"lockfile_version":"0.1","packages":{}}', encoding="utf-8")
        monkeypatch.setattr(Path, "read_text",
                            lambda self, *a, **k: (_ for _ in ()).throw(OSError("unreadable")))
        with pytest.raises(LockfileFormatError):
            read_lockfile_strict(lf)

    def test_error_message_has_no_entry_values(self, lf):
        lf.write_text('{"lockfile_version":"0.1","packages":{"a-pack":{"token":"SECRETVAL","token":"OTHER"}}}',
                      encoding="utf-8")
        before = lf.read_bytes()
        with pytest.raises(LockfileFormatError) as ei:
            update_lockfile("b-pack", _sealed_entry(), path=lf)
        msg = str(ei.value)
        assert "SECRETVAL" not in msg and "OTHER" not in msg
        assert lf.read_bytes() == before


# --------------------------------------------------------------------------- #
# lock seal (no --force)                                                       #
# --------------------------------------------------------------------------- #

class TestSealNoForce:
    def test_adds_missing_integrity_then_structure(self, env_lock):
        _write_lock(env_lock, {"a-pack": {"version": "1.0.0", "package_type": "toolpack",
                                          "runtime": "python", "entrypoint": "m", "artifact_hash": "sha256:x",
                                          "tools": [], "permissions": {"network_level": "none"}}})
        rc = main(["lock", "seal"])
        assert rc == 0
        lock = read_lockfile(env_lock)
        assert verify_entry("a-pack", lock["packages"]["a-pack"]).status == "verified"
        assert verify_structure(lock) == "verified"

    def test_verified_is_idempotent_no_rewrite(self, env_lock):
        _write_lock(env_lock, {"a-pack": _sealed_entry()}, structure=True)
        before = env_lock.read_bytes()
        rc = main(["lock", "seal"])
        assert rc == 0
        assert env_lock.read_bytes() == before          # no unnecessary rewrite

    def test_entry_mismatch_not_healed(self, env_lock, capsys):
        e = _sealed_entry()
        e["_integrity"]["hash"] = "0" * 64               # per-entry drift
        _write_lock(env_lock, {"a-pack": e}, structure=True)
        before = env_lock.read_bytes()
        rc = main(["lock", "seal"])
        assert rc == 1
        assert env_lock.read_bytes() == before           # not healed, no write
        assert "--force" in capsys.readouterr().out

    def test_structure_mismatch_not_healed(self, env_lock, capsys):
        _write_lock(env_lock, {"a-pack": _sealed_entry()}, structure=True)
        d = json.loads(env_lock.read_text(encoding="utf-8"))
        d["structure_digest"]["hash"] = "0" * 64
        env_lock.write_text(json.dumps(d, indent=2), encoding="utf-8")
        before = env_lock.read_bytes()
        rc = main(["lock", "seal"])
        assert rc == 1
        assert env_lock.read_bytes() == before
        assert "--force" in capsys.readouterr().out

    def test_corrupt_refused_without_force(self, env_lock):
        env_lock.write_text('{"packages":{},"packages":{}}', encoding="utf-8")
        before = env_lock.read_bytes()
        assert main(["lock", "seal"]) == 1
        assert env_lock.read_bytes() == before


# --------------------------------------------------------------------------- #
# lock seal --force                                                            #
# --------------------------------------------------------------------------- #

class TestSealForce:
    def test_force_heals_entry_mismatch(self, env_lock):
        e = _sealed_entry()
        e["_integrity"]["hash"] = "0" * 64
        _write_lock(env_lock, {"a-pack": e}, structure=True)
        assert main(["lock", "seal", "--force"]) == 0
        lock = read_lockfile(env_lock)
        assert verify_entry("a-pack", lock["packages"]["a-pack"]).status == "verified"
        assert verify_structure(lock) == "verified"

    def test_force_heals_structure_mismatch(self, env_lock):
        _write_lock(env_lock, {"a-pack": _sealed_entry(), "b-pack": _sealed_entry(version="2.0.0")}, structure=True)
        d = json.loads(env_lock.read_text(encoding="utf-8"))
        d["structure_digest"]["hash"] = "0" * 64
        env_lock.write_text(json.dumps(d, indent=2), encoding="utf-8")
        assert main(["lock", "seal", "--force"]) == 0
        lock = read_lockfile(env_lock)
        assert verify_structure(lock) == "verified"
        assert verify_entry("a-pack", lock["packages"]["a-pack"]).status == "verified"

    def test_force_audits_reseal(self, env_lock, monkeypatch):
        import agentnode_sdk.policy as pol
        calls = []
        monkeypatch.setattr(pol, "audit_decision",
                            lambda decision, event, slug, **k: calls.append((event, decision.reason)))
        _write_lock(env_lock, {"a-pack": _sealed_entry()}, structure=True)
        assert main(["lock", "seal", "--force"]) == 0
        assert any("force" in reason for _, reason in calls)

    @pytest.mark.parametrize("text", ['{"packages":{},"packages":{}}', "{not json", '["x"]'])
    def test_force_still_refuses_parser_and_model_errors(self, env_lock, text):
        env_lock.write_text(text, encoding="utf-8")
        before = env_lock.read_bytes()
        assert main(["lock", "seal", "--force"]) == 1
        assert env_lock.read_bytes() == before


# --------------------------------------------------------------------------- #
# Missing / unsupported lockfile_version — refused for EVERY mutation, no write #
# --------------------------------------------------------------------------- #

class TestMissingLockfileVersionRefused:
    """A lockfile whose lockfile_version is absent or unsupported is NOT
    auto-repaired: every mutation fails closed and writes nothing. 0.2A-2a has
    no historical migration path for a missing lockfile_version."""

    @pytest.mark.parametrize("body", [
        '{"packages":{}}',                              # missing entirely
        '{"lockfile_version":"9.9","packages":{}}',     # unsupported string
        '{"lockfile_version":0.1,"packages":{}}',       # wrong type (number)
        '{"lockfile_version":null,"packages":{}}',      # null
    ])
    def test_strict_reader_refuses(self, lf, body):
        lf.write_text(body, encoding="utf-8")
        with pytest.raises(LockfileFormatError):
            read_lockfile_strict(lf)

    def test_install_upgrade_refused_no_write(self, lf):
        lf.write_text('{"packages":{}}', encoding="utf-8")
        before = lf.read_bytes()
        with pytest.raises(LockfileFormatError):
            update_lockfile("a-pack", _sealed_entry(), path=lf)
        assert lf.read_bytes() == before

    def test_remove_refused_no_write(self, lf):
        lf.write_text('{"packages":{"a-pack":{"version":"1.0.0"}}}', encoding="utf-8")
        before = lf.read_bytes()
        with pytest.raises(LockfileFormatError):
            remove_from_lockfile("a-pack", path=lf)
        assert lf.read_bytes() == before

    def test_lock_seal_refused_no_write(self, env_lock, monkeypatch):
        events = _seal_spies(monkeypatch)
        env_lock.write_text('{"packages":{}}', encoding="utf-8")
        before = env_lock.read_bytes()
        assert main(["lock", "seal"]) == 1
        assert "write" not in events                     # atomic_write_json never called
        assert env_lock.read_bytes() == before

    def test_lock_seal_force_refused_no_write(self, env_lock, monkeypatch):
        events = _seal_spies(monkeypatch)
        env_lock.write_text('{"packages":{}}', encoding="utf-8")
        before = env_lock.read_bytes()
        assert main(["lock", "seal", "--force"]) == 1
        assert "write" not in events
        assert env_lock.read_bytes() == before


# --------------------------------------------------------------------------- #
# lock seal on an EMPTY but valid lockfile (empty-set digest)                   #
# --------------------------------------------------------------------------- #

class TestSealEmptyLockfile:
    def test_missing_digest_writes_empty_set_vector(self, env_lock):
        _write_lock(env_lock, {})                        # valid model, packages == {}, no digest
        assert verify_structure(read_lockfile(env_lock)) == "missing"
        assert main(["lock", "seal"]) == 0
        lock = read_lockfile(env_lock)
        assert lock["packages"] == {}
        assert verify_structure(lock) == "verified"
        assert lock["structure_digest"]["hash"] == EMPTY_SET_DIGEST

    def test_verified_empty_is_idempotent(self, env_lock):
        _write_lock(env_lock, {}, structure=True)        # empty + empty-set digest already
        before = env_lock.read_bytes()
        assert main(["lock", "seal"]) == 0
        assert env_lock.read_bytes() == before           # no rewrite, updated_at unchanged

    def test_absent_file_is_noop(self, env_lock):
        assert not env_lock.exists()
        assert main(["lock", "seal"]) == 0
        assert not env_lock.exists()                     # nothing created


# --------------------------------------------------------------------------- #
# Transactional remove no-op (absent slug) — returns None, no write            #
# --------------------------------------------------------------------------- #

class TestRemoveNoOp:
    def test_absent_slug_returns_none_no_write(self, lf, monkeypatch):
        update_lockfile("a-pack", _sealed_entry(), path=lf)   # verified, one entry
        before = lf.read_bytes()
        import agentnode_sdk._fileutil as fu
        calls = {"n": 0}
        monkeypatch.setattr(fu, "atomic_write_json",
                            lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
        removed = remove_from_lockfile("ghost-pack", path=lf)
        assert removed is None                            # transactional no-op
        assert calls["n"] == 0                            # no seal/updated_at/write
        assert lf.read_bytes() == before                  # byte-identical incl. updated_at


# --------------------------------------------------------------------------- #
# lock seal transaction order — lock acquired before read; one read, one write  #
# --------------------------------------------------------------------------- #

class TestSealTransactionOrder:
    def test_change_locks_before_read_then_single_write(self, env_lock, monkeypatch):
        events = _seal_spies(monkeypatch)
        _write_lock(env_lock, {"a-pack": _sealed_entry()})   # sealed entry, missing structure digest
        assert main(["lock", "seal"]) == 0
        assert events == ["lock", "read", "write"]           # lock BEFORE read; one read + one write

    def test_idempotent_locks_and_reads_but_never_writes(self, env_lock, monkeypatch):
        events = _seal_spies(monkeypatch)
        _write_lock(env_lock, {"a-pack": _sealed_entry()}, structure=True)   # already verified
        assert main(["lock", "seal"]) == 0
        assert events == ["lock", "read"]                    # no write on idempotency


# --------------------------------------------------------------------------- #
# lock seal — malformed slug/entry refused before any entry op (both modes)     #
# --------------------------------------------------------------------------- #

class TestSealMalformedEntry:
    @pytest.mark.parametrize("bad", ["null", '"a string"', "[1,2]", "42"])
    @pytest.mark.parametrize("force", [[], ["--force"]], ids=["noforce", "force"])
    def test_non_object_entry_refused_no_write(self, env_lock, bad, force):
        env_lock.write_text(
            '{"lockfile_version":"0.1","updated_at":"","packages":{"a-pack":%s}}' % bad,
            encoding="utf-8",
        )
        before = env_lock.read_bytes()
        rc = main(["lock", "seal", *force])
        assert rc == 1                                    # controlled exit, no traceback
        assert env_lock.read_bytes() == before            # byte-identical, no write

    @pytest.mark.parametrize("force", [[], ["--force"]], ids=["noforce", "force"])
    def test_invalid_slug_refused_no_write(self, env_lock, force):
        env_lock.write_text(
            '{"lockfile_version":"0.1","updated_at":"","packages":{"Bad_Slug":{"version":"1.0.0"}}}',
            encoding="utf-8",
        )
        before = env_lock.read_bytes()
        assert main(["lock", "seal", *force]) == 1
        assert env_lock.read_bytes() == before
