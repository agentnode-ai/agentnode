"""Slice 0.2A-1b — structure-digest core logic (no runtime/installer/CLI).

The structure digest is an UNKEYED hash-of-hashes over the set of
(slug -> per-entry _integrity). It is NOT a signature: it only detects entry
addition / removal / transplant and non-reseal drift that per-entry integrity
misses. These are pure unit tests of the core functions; integration lands in a
later slice.
"""

from __future__ import annotations

import copy
import json

import pytest

from agentnode_sdk.lock_integrity import (
    STRUCTURE_CANONICALIZATION_VERSION,
    LockIntegrityDenied,
    LockIntegrityReport,
    StructureIntegrityError,
    compute_structure_digest,
    enforce_lock_integrity,
    evaluate_lock_integrity,
    seal_entry,
    seal_structure,
    verify_structure,
)

# Locks the canonicalization: empty packages -> a fixed digest. Canonical bytes:
# {"canonicalization_version":1,"entries":[],"kind":"agentnode.lock.structure","lockfile_version":"0.1"}
EMPTY_DIGEST = "57eae92f23a97fd2292d317ea26c67a68464eee9b488f39652584804ca883d5d"


def _entry(**over) -> dict:
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
    return seal_entry(e)  # attaches a well-formed _integrity


def _lock(packages: dict) -> dict:
    return {"lockfile_version": "0.1", "updated_at": "", "packages": packages}


# --------------------------------------------------------------------------- #
# Determinism                                                                  #
# --------------------------------------------------------------------------- #

class TestDeterminism:
    def test_identical_lock_identical_digest(self):
        a = _lock({"a-pack": _entry()})
        b = _lock({"a-pack": _entry()})
        assert compute_structure_digest(a) == compute_structure_digest(b)

    def test_deep_copy_and_json_roundtrip_stable(self):
        a = _lock({"a-pack": _entry(), "b-pack": _entry(version="2.0.0")})
        d = compute_structure_digest(a)
        assert compute_structure_digest(copy.deepcopy(a)) == d
        assert compute_structure_digest(json.loads(json.dumps(a))) == d

    def test_package_key_order_irrelevant(self):
        e_a, e_b = _entry(), _entry(version="2.0.0")
        one = _lock({"a-pack": e_a, "b-pack": e_b})
        two = _lock({"b-pack": e_b, "a-pack": e_a})
        assert compute_structure_digest(one) == compute_structure_digest(two)

    def test_empty_lock_is_deterministic_fixed_vector(self):
        assert compute_structure_digest(_lock({})) == EMPTY_DIGEST
        assert compute_structure_digest(_lock({})) == compute_structure_digest(_lock({}))

    def test_first_entry_changes_digest(self):
        empty = compute_structure_digest(_lock({}))
        one = compute_structure_digest(_lock({"a-pack": _entry()}))
        assert empty != one

    def test_updated_at_does_not_affect(self):
        a = _lock({"a-pack": _entry()})
        b = _lock({"a-pack": copy.deepcopy(a["packages"]["a-pack"])})
        b["updated_at"] = "2099-01-01T00:00:00+00:00"
        assert compute_structure_digest(a) == compute_structure_digest(b)

    def test_existing_structure_digest_does_not_affect_recompute(self):
        a = _lock({"a-pack": _entry()})
        sealed = seal_structure(a)  # now has structure_digest
        assert compute_structure_digest(sealed) == compute_structure_digest(a)


# --------------------------------------------------------------------------- #
# Sensitivity                                                                  #
# --------------------------------------------------------------------------- #

class TestSensitivity:
    def test_add_entry(self):
        a = _lock({"a-pack": _entry()})
        b = _lock({"a-pack": copy.deepcopy(a["packages"]["a-pack"]), "b-pack": _entry()})
        assert compute_structure_digest(a) != compute_structure_digest(b)

    def test_remove_entry(self):
        a = _lock({"a-pack": _entry(), "b-pack": _entry(version="2.0.0")})
        b = _lock({"a-pack": copy.deepcopy(a["packages"]["a-pack"])})
        assert compute_structure_digest(a) != compute_structure_digest(b)

    def test_change_slug(self):
        e = _entry()
        a = _lock({"a-pack": e})
        b = _lock({"z-pack": copy.deepcopy(e)})
        assert compute_structure_digest(a) != compute_structure_digest(b)

    def test_change_integrity_hash(self):
        a = _lock({"a-pack": _entry()})
        b = copy.deepcopy(a)
        b["packages"]["a-pack"]["_integrity"]["hash"] = "f" * 64
        assert compute_structure_digest(a) != compute_structure_digest(b)

    def test_change_integrity_canonical_version(self):
        a = _lock({"a-pack": _entry()})
        b = copy.deepcopy(a)
        b["packages"]["a-pack"]["_integrity"]["canonical_version"] = 3
        assert compute_structure_digest(a) != compute_structure_digest(b)

    def test_unknown_extra_fields_do_not_affect(self):
        a = _lock({"a-pack": _entry()})
        b = copy.deepcopy(a)
        b["packages"]["a-pack"]["_integrity"]["extra"] = "ignored"  # dropped from canon
        b["packages"]["a-pack"]["installed_at"] = "2099-01-01"      # not in canon input
        b["meta"] = {"anything": True}                             # extra top-level key
        assert compute_structure_digest(a) == compute_structure_digest(b)


# --------------------------------------------------------------------------- #
# Hash-of-hashes semantics                                                     #
# --------------------------------------------------------------------------- #

class TestHashOfHashes:
    def test_content_change_without_reseal_keeps_structure(self):
        sealed = seal_structure(_lock({"a-pack": _entry()}))
        sealed["packages"]["a-pack"]["version"] = "9.9.9"  # content, _integrity unchanged
        assert verify_structure(sealed) == "verified"

    def test_reseal_entry_without_reseal_structure_is_mismatch(self):
        sealed = seal_structure(_lock({"a-pack": _entry()}))
        # re-seal the entry (its _integrity hash changes) but NOT the structure
        e = sealed["packages"]["a-pack"]
        e["version"] = "9.9.9"
        sealed["packages"]["a-pack"] = seal_entry(e)
        assert verify_structure(sealed) == "mismatch"

    def test_reseal_both_levels_is_verified(self):
        sealed = seal_structure(_lock({"a-pack": _entry()}))
        e = sealed["packages"]["a-pack"]
        e["version"] = "9.9.9"
        sealed["packages"]["a-pack"] = seal_entry(e)
        sealed = seal_structure(sealed)  # re-seal the structure too
        assert verify_structure(sealed) == "verified"

    def test_transplant_different_slug_is_mismatch(self):
        sealed = seal_structure(_lock({"a-pack": _entry()}))
        # transplant a validly-sealed entry under a NEW slug without re-sealing structure
        sealed["packages"]["b-pack"] = _entry(version="2.0.0")
        assert verify_structure(sealed) == "mismatch"

    def test_identical_canonical_pair_is_indistinguishable(self):
        # Two entries that produce the same (slug, _integrity) pair are, by design,
        # not distinguishable by the structure digest.
        a = _lock({"a-pack": _entry()})
        b = _lock({"a-pack": _entry()})  # same canonical content -> same _integrity
        assert compute_structure_digest(a) == compute_structure_digest(b)


# --------------------------------------------------------------------------- #
# Error states                                                                 #
# --------------------------------------------------------------------------- #

class TestErrorStates:
    @pytest.mark.parametrize("integ", [
        None,                                              # missing (as null)
        "sha256:abc",                                      # string
        ["sha256"],                                        # list
        {"canonical_version": 1, "hash": "a" * 64},        # missing algorithm
        {"algorithm": "sha256", "hash": "a" * 64},         # missing canonical_version
        {"algorithm": "sha256", "canonical_version": 1},   # missing hash
        {"algorithm": "md5", "canonical_version": 1, "hash": "a" * 64},   # wrong algo
        {"algorithm": "sha256", "canonical_version": "1", "hash": "a" * 64},  # bad version type
        {"algorithm": "sha256", "canonical_version": 0, "hash": "a" * 64},    # version < 1
        {"algorithm": "sha256", "canonical_version": 4, "hash": "a" * 64},    # > CANONICAL_VERSION
        {"algorithm": "sha256", "canonical_version": 999, "hash": "a" * 64},  # unsupported version
        {"algorithm": "sha256", "canonical_version": True, "hash": "a" * 64}, # bool is not a version
        {"algorithm": "sha256", "canonical_version": 1, "hash": "AB" * 32},   # uppercase hex
        {"algorithm": "sha256", "canonical_version": 1, "hash": "a" * 63},    # not 64 chars
        {"algorithm": "sha256", "canonical_version": 1, "hash": "z" * 64},    # non-hex
    ])
    def test_compute_raises_on_malformed_integrity(self, integ):
        entry = {"version": "1.0.0"}
        if integ is not None:
            entry["_integrity"] = integ
        with pytest.raises(StructureIntegrityError):
            compute_structure_digest(_lock({"a-pack": entry}))

    def test_missing_integrity_maps_not_to_null(self):
        # An entry with no _integrity is refused, never canonicalized as null.
        with pytest.raises(StructureIntegrityError):
            compute_structure_digest(_lock({"a-pack": {"version": "1.0.0"}}))

    def test_entry_not_object(self):
        with pytest.raises(StructureIntegrityError):
            compute_structure_digest(_lock({"a-pack": "not-an-object"}))

    def test_missing_or_invalid_packages(self):
        with pytest.raises(StructureIntegrityError):
            compute_structure_digest({"lockfile_version": "0.1"})           # no packages
        with pytest.raises(StructureIntegrityError):
            compute_structure_digest({"lockfile_version": "0.1", "packages": []})  # not a dict

    def test_missing_lockfile_version(self):
        with pytest.raises(StructureIntegrityError):
            compute_structure_digest({"packages": {}})

    def test_verify_malformed_structure_digest_is_invalid(self):
        sealed = seal_structure(_lock({"a-pack": _entry()}))
        for bad in ["nope", {"algorithm": "md5", "canonicalization_version": 1, "hash": "a" * 64},
                    {"algorithm": "sha256", "canonicalization_version": 1, "hash": "a" * 63},
                    {"algorithm": "sha256", "canonicalization_version": 1}]:
            broken = dict(sealed)
            broken["structure_digest"] = bad
            assert verify_structure(broken) == "invalid"

    def test_verify_invalid_on_bad_entry_integrity(self):
        sealed = seal_structure(_lock({"a-pack": _entry()}))
        sealed["packages"]["a-pack"]["_integrity"] = "corrupt"
        assert verify_structure(sealed) == "invalid"

    def test_verify_unknown_canonicalization_version_is_unsupported(self):
        sealed = seal_structure(_lock({"a-pack": _entry()}))
        sealed["structure_digest"] = dict(sealed["structure_digest"])
        sealed["structure_digest"]["canonicalization_version"] = 99
        assert verify_structure(sealed) == "unsupported"

    def test_verify_wrong_stored_hash_is_mismatch(self):
        sealed = seal_structure(_lock({"a-pack": _entry()}))
        sealed["structure_digest"] = dict(sealed["structure_digest"])
        sealed["structure_digest"]["hash"] = "0" * 64
        assert verify_structure(sealed) == "mismatch"


# --------------------------------------------------------------------------- #
# Seal / verify                                                                #
# --------------------------------------------------------------------------- #

class TestSealVerify:
    def test_seal_produces_field_format(self):
        sealed = seal_structure(_lock({"a-pack": _entry()}))
        sd = sealed["structure_digest"]
        assert sd["algorithm"] == "sha256"
        assert sd["canonicalization_version"] == STRUCTURE_CANONICALIZATION_VERSION
        assert isinstance(sd["hash"], str) and len(sd["hash"]) == 64
        assert sd["hash"] == sd["hash"].lower()

    def test_seal_is_idempotent(self):
        s1 = seal_structure(_lock({"a-pack": _entry()}))
        s2 = seal_structure(s1)
        assert s1["structure_digest"] == s2["structure_digest"]

    def test_seal_does_not_mutate_input_or_other_fields(self):
        original = _lock({"a-pack": _entry()})
        snapshot = copy.deepcopy(original)
        sealed = seal_structure(original)
        assert "structure_digest" not in original          # input untouched
        assert original == snapshot                          # nothing changed
        assert sealed["packages"] == original["packages"]    # entries/_integrity intact
        assert sealed["updated_at"] == original["updated_at"]

    def test_verify_fresh_sealed_is_verified(self):
        assert verify_structure(seal_structure(_lock({"a-pack": _entry()}))) == "verified"
        assert verify_structure(seal_structure(_lock({}))) == "verified"  # empty too

    def test_lock_without_digest_is_missing(self):
        assert verify_structure(_lock({"a-pack": _entry()})) == "missing"

    def test_seal_refuses_unsealed_or_invalid_entries(self):
        with pytest.raises(StructureIntegrityError):
            seal_structure(_lock({"x": {"version": "1.0.0"}}))  # no _integrity

    def test_deliberate_reseal_of_divergent_digest_is_allowed(self):
        sealed = seal_structure(_lock({"a-pack": _entry()}))
        sealed["packages"]["b-pack"] = _entry(version="2.0.0")  # now divergent
        assert verify_structure(sealed) == "mismatch"
        resealed = seal_structure(sealed)                        # core op allows reseal
        assert verify_structure(resealed) == "verified"


# --------------------------------------------------------------------------- #
# Runtime-neutral report + enforcement decision                                #
# --------------------------------------------------------------------------- #

class TestReport:
    def test_report_shape(self):
        r = evaluate_lock_integrity("a-pack", seal_structure(_lock({"a-pack": _entry()})), strict=False)
        assert isinstance(r, LockIntegrityReport)
        assert r.entry_status == "verified"
        assert r.structure_status == "verified"
        assert r.allowed is True
        assert r.reason == "verified"

    @pytest.mark.parametrize("structure_status", ["missing", "mismatch", "unsupported", "invalid"])
    def test_allow_matrix_normal_vs_strict(self, structure_status):
        sealed = seal_structure(_lock({"a-pack": _entry()}))
        lock = dict(sealed)
        if structure_status == "missing":
            lock = _lock({"a-pack": _entry()})
        elif structure_status == "mismatch":
            sd = dict(sealed["structure_digest"])
            sd["hash"] = "0" * 64
            lock["structure_digest"] = sd
        elif structure_status == "unsupported":
            sd = dict(sealed["structure_digest"])
            sd["canonicalization_version"] = 99
            lock["structure_digest"] = sd
        else:  # invalid
            lock["structure_digest"] = "nope"

        assert verify_structure(lock) == structure_status
        # normal: allowed (warn); strict: denied
        assert evaluate_lock_integrity("a-pack", lock, strict=False).allowed is True
        assert evaluate_lock_integrity("a-pack", lock, strict=True).allowed is False

    def test_verified_allowed_in_both_modes(self):
        lock = seal_structure(_lock({"a-pack": _entry()}))
        assert evaluate_lock_integrity("a-pack", lock, strict=False).allowed is True
        assert evaluate_lock_integrity("a-pack", lock, strict=True).allowed is True

    def test_entry_mismatch_denies_in_strict_even_if_structure_verified(self):
        lock = seal_structure(_lock({"a-pack": _entry()}))
        # tamper the entry AFTER structure seal, then re-seal structure so structure
        # is verified but the entry's own integrity is now a mismatch.
        lock["packages"]["a-pack"]["entrypoint"] = "evil.mod"
        lock = seal_structure(lock)
        assert verify_structure(lock) == "verified"
        r = evaluate_lock_integrity("a-pack", lock, strict=True)
        assert r.entry_status == "mismatch"
        assert r.allowed is False
        assert evaluate_lock_integrity("a-pack", lock, strict=False).allowed is True

    def test_enforce_raises_when_denied_returns_none_when_allowed(self):
        denied = _lock({"a-pack": _entry()})  # structure missing → strict denies
        with pytest.raises(LockIntegrityDenied) as ei:
            enforce_lock_integrity("a-pack", denied, strict=True)
        assert ei.value.report.structure_status == "missing"
        assert ei.value.report.allowed is False

        allowed = seal_structure(_lock({"a-pack": _entry()}))
        assert enforce_lock_integrity("a-pack", allowed, strict=True) is None

    def test_reason_is_content_free(self):
        lock = seal_structure(_lock({"secret-slug": _entry()}))
        lock["structure_digest"] = dict(lock["structure_digest"])
        lock["structure_digest"]["hash"] = "0" * 64
        r = evaluate_lock_integrity("secret-slug", lock, strict=True)
        assert "secret-slug" not in r.reason        # no slug/value leakage
        assert "structure_mismatch" in r.reason


# --------------------------------------------------------------------------- #
# Contract corrections (amendment): state order, absent slug, versions, slugs  #
# --------------------------------------------------------------------------- #

# One entry with an EXPLICIT fixed _integrity triple (independent of seal_entry).
# Canonical bytes:
# {"canonicalization_version":1,"entries":[["a-pack",{"algorithm":"sha256",
#  "canonical_version":1,"hash":"a...a"}]],"kind":"agentnode.lock.structure","lockfile_version":"0.1"}
ONE_ENTRY_DIGEST = "0feb9e3ff46a71ff70264518209a068f5634c2289c96b934e85f06899cd73750"


def _fixed_triple_lock():
    return {
        "lockfile_version": "0.1",
        "updated_at": "",
        "packages": {"a-pack": {"_integrity": {"algorithm": "sha256", "canonical_version": 1, "hash": "a" * 64}}},
    }


class TestContractCorrections:
    def test_one_entry_fixed_vector(self):
        assert compute_structure_digest(_fixed_triple_lock()) == ONE_ENTRY_DIGEST

    @pytest.mark.parametrize("bad", [None, [], "x", 42])
    def test_verify_non_dict_is_invalid(self, bad):
        assert verify_structure(bad) == "invalid"

    def test_no_digest_but_invalid_base_is_invalid_not_missing(self):
        # entry lacks _integrity, no structure_digest -> base is invalid, so the
        # result is 'invalid', never the migration 'missing'.
        assert verify_structure(_lock({"a-pack": {"version": "1.0.0"}})) == "invalid"

    def test_no_digest_valid_base_is_missing(self):
        assert verify_structure(_lock({"a-pack": _entry()})) == "missing"
        assert verify_structure(_lock({})) == "missing"

    def test_digest_key_absent_is_missing(self):
        sealed = seal_structure(_lock({"a-pack": _entry()}))
        del sealed["structure_digest"]
        assert verify_structure(sealed) == "missing"

    def test_explicit_null_digest_is_invalid(self):
        sealed = seal_structure(_lock({"a-pack": _entry()}))
        sealed["structure_digest"] = None
        assert verify_structure(sealed) == "invalid"

    def test_absent_slug_is_denied_in_both_modes(self):
        lock = seal_structure(_lock({"a-pack": _entry()}))  # structure verified
        for strict in (False, True):
            r = evaluate_lock_integrity("does-not-exist", lock, strict=strict)
            assert r.entry_status == "absent"
            assert r.allowed is False
            assert "entry_absent" in r.reason

    def test_present_missing_integrity_vs_absent_slug(self):
        # present entry without _integrity -> entry_status 'missing' (migration),
        # NOT 'absent'. (structure is missing here too, so normal allows.)
        lock = _lock({"a-pack": {"version": "1.0.0"}})
        r = evaluate_lock_integrity("a-pack", lock, strict=False)
        assert r.entry_status == "missing"

    @pytest.mark.parametrize("lock", [None, {"packages": []}, {"packages": "nope"}, "x", 42])
    def test_report_path_non_dict_is_controlled_no_attributeerror(self, lock):
        r = evaluate_lock_integrity("a-pack", lock, strict=False)
        assert isinstance(r, LockIntegrityReport)
        assert r.entry_status == "absent"
        assert r.allowed is False

    def test_unsupported_entry_canonical_version_is_invalid(self):
        sealed = seal_structure(_lock({"a-pack": _entry()}))
        sealed["packages"]["a-pack"]["_integrity"]["canonical_version"] = 999
        assert verify_structure(sealed) == "invalid"

    @pytest.mark.parametrize("slug", ["A-Pack", "-lead", "trail-", "has space", "café", "x", ""])
    def test_invalid_slug_is_invalid_on_verify(self, slug):
        lock = _lock({slug: {"_integrity": {"algorithm": "sha256", "canonical_version": 1, "hash": "a" * 64}}})
        assert verify_structure(lock) == "invalid"
        with pytest.raises(StructureIntegrityError):
            compute_structure_digest(lock)

    def test_non_string_slug_raises(self):
        with pytest.raises(StructureIntegrityError):
            compute_structure_digest(_lock({123: {"_integrity": {"algorithm": "sha256", "canonical_version": 1, "hash": "a" * 64}}}))

    def test_seal_refuses_invalid_slug(self):
        with pytest.raises(StructureIntegrityError):
            seal_structure(_lock({"Bad Slug": _entry()}))


# --------------------------------------------------------------------------- #
# Slug rule is centralised in a neutral core module (no core -> cli dependency) #
# --------------------------------------------------------------------------- #

class TestSlugRuleCentralization:
    def test_cli_init_reexports_the_central_rule(self):
        # cli.init.SLUG_RE must remain importable (API compat) and be the SAME
        # object as the central rule — one source of truth.
        from agentnode_sdk.references import PACKAGE_SLUG_RE
        from agentnode_sdk.cli import init
        assert init.SLUG_RE is PACKAGE_SLUG_RE

    @pytest.mark.parametrize("slug,valid", [
        ("word-counter-pack", True),   # valid normal slug
        ("ab", True),                  # two-char
        ("a1", True),
        ("a", False),                  # one-char — current regex requires >= 2
        ("", False),
        ("A-Pack", False),             # uppercase
        ("-lead", False),              # leading hyphen
        ("trail-", False),             # trailing hyphen
        ("has space", False),          # whitespace
        ("café", False),               # unicode
    ])
    def test_central_rule_cli_and_structure_agree(self, slug, valid):
        from agentnode_sdk.references import is_valid_package_slug
        from agentnode_sdk.cli.init import SLUG_RE

        assert is_valid_package_slug(slug) is valid
        assert bool(SLUG_RE.match(slug)) is valid          # cli init accepts/refuses the same
        lock = _lock({slug: {"_integrity": {"algorithm": "sha256", "canonical_version": 1, "hash": "a" * 64}}})
        if valid:
            compute_structure_digest(lock)                  # structure accepts it too
        else:
            with pytest.raises(StructureIntegrityError):    # ...and refuses the same
                compute_structure_digest(lock)

    def test_importing_lock_integrity_does_not_require_cli(self):
        # Layering guard: importing + exercising the core integrity logic must NOT
        # load any agentnode_sdk.cli module (not even lazily). Run in a fresh
        # interpreter so other tests' imports don't mask a real dependency.
        import subprocess
        import sys

        code = (
            "import sys\n"
            "import agentnode_sdk.lock_integrity as m\n"
            "m.compute_structure_digest({'lockfile_version':'0.1','packages':{'a-pack':"
            "{'_integrity':{'algorithm':'sha256','canonical_version':1,'hash':'a'*64}}}})\n"
            "cli = [k for k in sys.modules if k == 'agentnode_sdk.cli' or k.startswith('agentnode_sdk.cli.')]\n"
            "assert not cli, cli\n"
            "print('OK')\n"
        )
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert "OK" in r.stdout
