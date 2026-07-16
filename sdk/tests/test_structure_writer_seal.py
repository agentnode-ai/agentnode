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


def _drifted_entry(**over) -> dict:
    """A sealed entry whose CONTENT was mutated after sealing: verify_entry reports
    'mismatch', but its _integrity (and thus the structure digest over the
    _integrity set) is unchanged, so verify_structure stays 'verified'."""
    e = _sealed_entry(**over)
    e["version"] = e["version"] + "-drifted"   # content drift; _integrity untouched
    return e


def _write_spy(monkeypatch):
    """Count atomic_write_json calls (0 == no write reached disk)."""
    import agentnode_sdk._fileutil as fu
    calls = {"n": 0}
    real = fu.atomic_write_json

    def spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(fu, "atomic_write_json", spy)
    return calls


def _audit_spy(monkeypatch):
    """Record every (reason, slug) passed to cmd_lock_seal's audit hook."""
    import agentnode_sdk.cli.commands as cmds
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(cmds, "_lock_seal_audit", lambda reason, slug: events.append((reason, slug)))
    return events


def _raise_oserror(*a, **k):
    """Stand-in for a failing atomic_write_json; its message carries a path + OS
    detail so a test can prove the neutral CLI error does NOT leak them."""
    raise OSError("simulated write failure at /secret/path/agentnode.lock (ENOSPC)")


def _order_spies(monkeypatch):
    """Record the interleaved order of atomic_write_json, cmd_lock_seal's success
    prints, and its audit events as a single event stream:
    ``"write"`` / ``("out", line)`` / ``("audit", reason, slug)``."""
    import builtins

    import agentnode_sdk._fileutil as fu
    import agentnode_sdk.cli.commands as cmds

    events: list = []
    real_write = fu.atomic_write_json
    real_print = builtins.print

    def spy_write(*a, **k):
        events.append("write")
        return real_write(*a, **k)

    def spy_print(*a, **k):
        events.append(("out", " ".join(str(x) for x in a)))
        return real_print(*a, **k)

    monkeypatch.setattr(fu, "atomic_write_json", spy_write)
    monkeypatch.setattr(builtins, "print", spy_print)
    monkeypatch.setattr(cmds, "_lock_seal_audit", lambda reason, slug: events.append(("audit", reason, slug)))
    return events


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


# --------------------------------------------------------------------------- #
# Per-entry pre-mutation gate — normal mutations refuse existing entry drift    #
# --------------------------------------------------------------------------- #

class TestPerEntryPreMutationGate:
    """A hash-of-hashes structure digest stays 'verified' when an entry's CONTENT
    drifts but its _integrity is untouched. A normal install/upgrade/remove must
    still refuse — it may not conserve that drift into a rewritten lockfile."""

    def _drift_lock(self, lf):
        # a-pack content-drifted (verify_entry mismatch), b-pack clean; structure
        # sealed over the (unchanged) _integrity set → verify_structure verified.
        _write_lock(lf, {"a-pack": _drifted_entry(version="1.0.0"),
                         "b-pack": _sealed_entry(version="1.0.0")}, structure=True)

    def test_precondition_structure_verified_entry_mismatch(self, lf):
        self._drift_lock(lf)
        lock = read_lockfile(lf)
        assert verify_structure(lock) == "verified"                       # global digest intact
        assert verify_entry("a-pack", lock["packages"]["a-pack"]).status == "mismatch"
        assert verify_entry("b-pack", lock["packages"]["b-pack"]).status == "verified"

    def test_install_other_refused_no_write(self, lf, monkeypatch):
        self._drift_lock(lf)
        before, spy = lf.read_bytes(), _write_spy(monkeypatch)
        with pytest.raises(LockfileFormatError):
            update_lockfile("c-pack", _sealed_entry(), path=lf)
        assert spy["n"] == 0 and lf.read_bytes() == before

    def test_upgrade_other_refused_no_write(self, lf, monkeypatch):
        self._drift_lock(lf)
        before, spy = lf.read_bytes(), _write_spy(monkeypatch)
        with pytest.raises(LockfileFormatError):
            update_lockfile("b-pack", _sealed_entry(version="2.0.0"), path=lf)
        assert spy["n"] == 0 and lf.read_bytes() == before

    def test_remove_other_refused_no_write(self, lf, monkeypatch):
        self._drift_lock(lf)
        before, spy = lf.read_bytes(), _write_spy(monkeypatch)
        with pytest.raises(LockfileFormatError):
            remove_from_lockfile("b-pack", path=lf)
        assert spy["n"] == 0 and lf.read_bytes() == before

    def test_update_drifted_slug_itself_refused_no_write(self, lf, monkeypatch):
        self._drift_lock(lf)
        before, spy = lf.read_bytes(), _write_spy(monkeypatch)
        with pytest.raises(LockfileFormatError):
            update_lockfile("a-pack", _sealed_entry(version="2.0.0"), path=lf)   # no target-slug exception
        assert spy["n"] == 0 and lf.read_bytes() == before

    def test_clean_mutations_leave_all_entries_and_structure_verified(self, lf):
        update_lockfile("a-pack", _sealed_entry(version="1.0.0"), path=lf)       # install
        update_lockfile("b-pack", _sealed_entry(version="1.0.0"), path=lf)       # install
        update_lockfile("a-pack", _sealed_entry(version="2.0.0"), path=lf)       # upgrade
        remove_from_lockfile("b-pack", path=lf)                                  # remove
        lock = read_lockfile(lf)
        for slug, entry in lock["packages"].items():
            assert verify_entry(slug, entry).status == "verified"
        assert verify_structure(lock) == "verified"


# --------------------------------------------------------------------------- #
# lock seal audits — emitted only AFTER a successful write                      #
# --------------------------------------------------------------------------- #

class TestSealAuditAfterCommit:
    def test_non_force_seal_then_structure_drift_no_audit(self, env_lock, monkeypatch):
        # a-pack unsealed (missing _integrity) + a corrupt structure_digest that
        # still mismatches after the entry is sealed in memory → non-force seals
        # the entry in memory, then hits non-healable structure drift → refuse.
        e = _sealed_entry(version="1.0.0")
        del e["_integrity"]
        data = {"lockfile_version": "0.1", "updated_at": "2026-01-01T00:00:00+00:00",
                "packages": {"a-pack": e},
                "structure_digest": {"algorithm": "sha256", "canonicalization_version": 1, "hash": "0" * 64}}
        env_lock.write_text(json.dumps(data, indent=2), encoding="utf-8")
        before = env_lock.read_bytes()
        audits, spy = _audit_spy(monkeypatch), _write_spy(monkeypatch)
        assert main(["lock", "seal"]) == 1
        assert spy["n"] == 0                             # no write
        assert audits == []                              # no seal audit for an unwritten seal
        assert env_lock.read_bytes() == before

    def test_force_write_failure_no_audit(self, env_lock, monkeypatch):
        _write_lock(env_lock, {"a-pack": _sealed_entry()}, structure=True)
        audits = _audit_spy(monkeypatch)
        import agentnode_sdk._fileutil as fu
        monkeypatch.setattr(fu, "atomic_write_json", _raise_oserror)
        rc = main(["lock", "seal", "--force"])           # controlled, not raised
        assert rc == 1
        assert audits == []                              # write failed → no reseal audit

    def test_successful_seal_emits_once_strictly_after_write(self, env_lock, monkeypatch):
        e = _sealed_entry()
        del e["_integrity"]                              # unsealed → non-force will seal it
        _write_lock(env_lock, {"a-pack": e})            # no structure digest yet
        order: list = []
        import agentnode_sdk._fileutil as fu
        import agentnode_sdk.cli.commands as cmds
        real_write = fu.atomic_write_json
        monkeypatch.setattr(fu, "atomic_write_json",
                            lambda *a, **k: (order.append("write"), real_write(*a, **k))[1])
        monkeypatch.setattr(cmds, "_lock_seal_audit",
                            lambda reason, slug: order.append(("audit", reason, slug)))
        assert main(["lock", "seal"]) == 0
        assert order == ["write", ("audit", "entry sealed", "a-pack")]   # one write, then one audit

    def test_idempotent_seal_no_audit(self, env_lock, monkeypatch):
        _write_lock(env_lock, {"a-pack": _sealed_entry()}, structure=True)   # fully verified
        audits = _audit_spy(monkeypatch)
        assert main(["lock", "seal"]) == 0
        assert audits == []                              # no new seal/reseal audit


# --------------------------------------------------------------------------- #
# lock seal — success output buffered until commit; write errors controlled     #
# --------------------------------------------------------------------------- #

_SUCCESS_WORDS = ("sealed", "resealed", "complete", "written")


class TestSealOutputAndWriteErrorContract:
    def test_force_write_error_is_controlled(self, env_lock, monkeypatch, capsys):
        _write_lock(env_lock, {"a-pack": _sealed_entry()}, structure=True)
        before = env_lock.read_bytes()
        audits = _audit_spy(monkeypatch)
        import agentnode_sdk._fileutil as fu
        monkeypatch.setattr(fu, "atomic_write_json", _raise_oserror)
        rc = main(["lock", "seal", "--force"])           # no pytest.raises — must be handled
        out = capsys.readouterr().out
        assert rc == 1
        for w in _SUCCESS_WORDS:
            assert w not in out                          # no success claim before a failed write
        assert "could not save the lockfile" in out      # neutral, path-free error
        assert "/secret/path" not in out and "ENOSPC" not in out
        assert audits == []                              # no audit
        assert env_lock.read_bytes() == before           # byte-identical

    def test_non_force_write_error_is_controlled(self, env_lock, monkeypatch, capsys):
        _write_lock(env_lock, {"a-pack": _sealed_entry()})   # sealed entry, missing digest → would write
        before = env_lock.read_bytes()
        audits = _audit_spy(monkeypatch)
        import agentnode_sdk._fileutil as fu
        monkeypatch.setattr(fu, "atomic_write_json", _raise_oserror)
        rc = main(["lock", "seal"])
        out = capsys.readouterr().out
        assert rc == 1
        for w in _SUCCESS_WORDS:
            assert w not in out
        assert "could not save the lockfile" in out
        assert "/secret/path" not in out and "ENOSPC" not in out
        assert audits == []
        assert env_lock.read_bytes() == before

    def test_non_force_success_order_write_output_audit(self, env_lock, monkeypatch):
        e = _sealed_entry()
        del e["_integrity"]                              # unsealed → non-force will seal it
        _write_lock(env_lock, {"a-pack": e})            # no structure digest yet
        events = _order_spies(monkeypatch)
        assert main(["lock", "seal"]) == 0
        assert events.count("write") == 1
        wi = events.index("write")
        outs = [i for i, ev in enumerate(events) if isinstance(ev, tuple) and ev[0] == "out"]
        auds = [i for i, ev in enumerate(events) if isinstance(ev, tuple) and ev[0] == "audit"]
        assert outs and auds
        assert wi < min(outs)                            # write BEFORE any success output
        assert max(outs) < min(auds)                     # all output BEFORE any audit
        assert len(auds) == 1                            # audit exactly once
        assert events.count(("audit", "entry sealed", "a-pack")) == 1

    def test_force_success_outputs_before_audits_no_duplicates(self, env_lock, monkeypatch):
        _write_lock(env_lock, {"a-pack": _sealed_entry(),
                               "b-pack": _sealed_entry(version="2.0.0")}, structure=True)
        events = _order_spies(monkeypatch)
        assert main(["lock", "seal", "--force"]) == 0
        assert events.count("write") == 1
        wi = events.index("write")
        outs = [i for i, ev in enumerate(events) if isinstance(ev, tuple) and ev[0] == "out"]
        auds = [i for i, ev in enumerate(events) if isinstance(ev, tuple) and ev[0] == "audit"]
        assert wi < min(outs)                            # every entry/summary output AFTER write
        assert max(outs) < min(auds)                     # then audits
        assert len(events) == len(set(events))           # no duplicate events
        assert ("out", "  a-pack: resealed") in events
        assert ("out", "  b-pack: resealed") in events

    def test_idempotent_no_write_no_success_output_no_audit(self, env_lock, monkeypatch, capsys):
        _write_lock(env_lock, {"a-pack": _sealed_entry()}, structure=True)   # fully verified
        before = env_lock.read_bytes()
        audits, spy = _audit_spy(monkeypatch), _write_spy(monkeypatch)
        rc = main(["lock", "seal"])
        out = capsys.readouterr().out
        assert rc == 0
        assert spy["n"] == 0                             # no write
        assert audits == []                              # no audit
        assert out.strip() == "Already sealed (no changes)."   # only the neutral message
        for w in ("resealed", "written", "complete"):
            assert w not in out                          # no seal/reseal success output
        assert env_lock.read_bytes() == before
