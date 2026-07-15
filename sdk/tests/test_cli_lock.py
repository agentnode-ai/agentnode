"""Tests for agentnode lock seal / lock verify CLI — Phase 15.2 + 16.5 + TG-2."""
import base64
import hashlib as _hashlib
import json
from pathlib import Path
from unittest.mock import patch

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
        # existing per-entry integrity is untouched; the missing structure digest
        # is now added (0.2A-2a migration).
        assert lock["packages"]["test-pack"]["_integrity"]["hash"] == original_hash
        from agentnode_sdk.lock_integrity import verify_structure
        assert verify_structure(lock) == "verified"

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
        # An empty but valid lockfile is sealed with the empty-set structure
        # digest (0.2A-2a), not treated as "nothing to do".
        _write_lockfile(tmp_lockfile, {})
        rc = main(["lock", "seal"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "structure_digest written" in out
        from agentnode_sdk.lock_integrity import verify_structure
        lock = read_lockfile(tmp_lockfile)
        assert lock["packages"] == {}
        assert verify_structure(lock) == "verified"

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
        # sealed-pack keeps its integrity (not re-sealed); structure digest is
        # written over the full set (0.2A-2a).
        assert lock["packages"]["sealed-pack"]["_integrity"]["hash"] == sealed["_integrity"]["hash"]
        from agentnode_sdk.lock_integrity import verify_structure
        assert verify_structure(lock) == "verified"

        out = capsys.readouterr().out
        assert "unsealed-pack: sealed" in out
        assert "1 sealed" in out


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


# ---------------------------------------------------------------------------
# Signature helpers
# ---------------------------------------------------------------------------

def _sign_entry(slug, entry):
    """Sign an entry with a fresh Ed25519 key (test helper)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from agentnode_sdk.signature import build_sign_payload

    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    payload = build_sign_payload(slug, entry)
    sig_bytes = private_key.sign(payload)
    fingerprint = _hashlib.sha256(pub_bytes).hexdigest()[:16]

    return {
        "publisher": [{
            "key_id": f"ed25519:{fingerprint}",
            "algorithm": "ed25519",
            "signature": base64.b64encode(sig_bytes).decode(),
            "public_key": base64.b64encode(pub_bytes).decode(),
            "canonical_version": 1,
            "signed_at": "2026-05-21T12:00:00+00:00",
        }],
    }


def _make_signed_entry(**overrides):
    """Create a sealed entry with a valid publisher signature."""
    slug = overrides.pop("slug", "test-pack")
    entry = _make_entry(**overrides)
    entry["_signatures"] = _sign_entry(slug, entry)
    return slug, seal_entry(entry)


def _make_invalid_signed_entry(**overrides):
    """Create a sealed entry with an invalid publisher signature."""
    slug = overrides.pop("slug", "test-pack")
    entry = _make_entry(**overrides)
    entry["_signatures"] = _sign_entry(slug, entry)
    entry["_signatures"]["publisher"][0]["signature"] = base64.b64encode(b"X" * 64).decode()
    return slug, seal_entry(entry)


# ---------------------------------------------------------------------------
# lock verify — signature tests (Phase 16.5)
# ---------------------------------------------------------------------------

class TestLockVerifySignature:
    def test_verify_valid_signature(self, tmp_lockfile, capsys):
        slug, entry = _make_signed_entry()
        _write_lockfile(tmp_lockfile, {slug: entry})
        rc = main(["lock", "verify"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "publisher:" in out
        assert "verified" in out

    def test_verify_missing_signature_returns_0(self, tmp_lockfile, capsys):
        _write_lockfile(tmp_lockfile, {"test-pack": seal_entry(_make_entry())})
        rc = main(["lock", "verify"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "publisher: unverified" in out

    def test_verify_invalid_signature_returns_1(self, tmp_lockfile, capsys):
        slug, entry = _make_invalid_signed_entry()
        _write_lockfile(tmp_lockfile, {slug: entry})
        rc = main(["lock", "verify"])
        assert rc == 1

    def test_verify_json_includes_signatures(self, tmp_lockfile, capsys):
        slug, entry = _make_signed_entry()
        _write_lockfile(tmp_lockfile, {slug: entry})
        rc = main(["lock", "verify", "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        assert "signatures" in report
        assert report["signatures"][slug]["status"] == "valid"
        assert "key_id" in report["signatures"][slug]

    def test_verify_json_missing_signature(self, tmp_lockfile, capsys):
        _write_lockfile(tmp_lockfile, {"test-pack": seal_entry(_make_entry())})
        rc = main(["lock", "verify", "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        assert report["signatures"]["test-pack"]["status"] == "missing"

    def test_verify_json_invalid_signature(self, tmp_lockfile, capsys):
        slug, entry = _make_invalid_signed_entry()
        _write_lockfile(tmp_lockfile, {slug: entry})
        rc = main(["lock", "verify", "--json"])
        assert rc == 1
        report = json.loads(capsys.readouterr().out)
        assert report["signatures"][slug]["status"] == "invalid"
        assert slug in report["signature_invalid"]
        assert report["ok"] is False

    def test_verify_revoked_returns_0(self, tmp_lockfile, capsys):
        """Revoked key on installed package → exit 0 but visible."""
        slug, entry = _make_signed_entry()
        from unittest.mock import patch
        from agentnode_sdk.signature import SignatureResult, SignatureStatus
        revoked = SignatureResult(
            status=SignatureStatus.REVOKED, slug=slug, key_id="ed25519:revoked",
        )
        _write_lockfile(tmp_lockfile, {slug: entry})
        with patch(
            "agentnode_sdk.signature.verify_entry_signature",
            return_value=revoked,
        ):
            rc = main(["lock", "verify"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "revoked" in out

    def test_verify_mixed_valid_and_unsigned(self, tmp_lockfile, capsys):
        """Valid + missing signatures: still exit 0."""
        slug_signed, signed_entry = _make_signed_entry(slug="signed-pack")
        unsigned_entry = seal_entry(_make_entry(version="2.0.0"))
        _write_lockfile(tmp_lockfile, {
            slug_signed: signed_entry,
            "unsigned-pack": unsigned_entry,
        })
        rc = main(["lock", "verify"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "publisher:" in out and "verified" in out
        assert "publisher: unverified" in out

    def test_no_local_signing_key_loaded(self, tmp_lockfile, monkeypatch):
        """lock verify must not load or reference the local signing key."""
        slug, entry = _make_signed_entry()
        _write_lockfile(tmp_lockfile, {slug: entry})
        import agentnode_sdk.signing_key as sk_mod
        monkeypatch.setattr(
            sk_mod, "load_signing_key",
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("signing key loaded")),
        )
        rc = main(["lock", "verify"])
        assert rc == 0


# ---------------------------------------------------------------------------
# lock verify — publisher_slug display (Phase 16.6)
# ---------------------------------------------------------------------------

class TestLockVerifyPublisherSlug:
    def test_human_output_shows_publisher_tag(self, tmp_lockfile, capsys):
        entry = _make_entry(publisher_slug="acme-ai")
        _write_lockfile(tmp_lockfile, {"test-pack": seal_entry(entry)})
        rc = main(["lock", "verify"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "[acme-ai]" in out

    def test_human_output_no_tag_when_no_publisher(self, tmp_lockfile, capsys):
        entry = _make_entry()
        _write_lockfile(tmp_lockfile, {"test-pack": seal_entry(entry)})
        rc = main(["lock", "verify"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "[" not in out

    def test_json_includes_publisher_slug(self, tmp_lockfile, capsys):
        entry = _make_entry(publisher_slug="acme-ai")
        _write_lockfile(tmp_lockfile, {"test-pack": seal_entry(entry)})
        rc = main(["lock", "verify", "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        assert report["signatures"]["test-pack"]["publisher_slug"] == "acme-ai"

    def test_json_no_publisher_slug_when_absent(self, tmp_lockfile, capsys):
        entry = _make_entry()
        _write_lockfile(tmp_lockfile, {"test-pack": seal_entry(entry)})
        rc = main(["lock", "verify", "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        assert "publisher_slug" not in report["signatures"]["test-pack"]


# ---------------------------------------------------------------------------
# lock verify --online (TG-2)
# ---------------------------------------------------------------------------

def _mock_key_status(status, key_id="ed25519:abc", error=None):
    """Build a mock KeyStatusResult."""
    from agentnode_sdk.key_status import KeyStatusResult, OnlineKeyStatus
    return KeyStatusResult(
        status=OnlineKeyStatus(status),
        key_id=key_id,
        publisher_slug="acme-ai",
        error=error,
    )


class TestLockVerifyOnline:
    def test_online_active_key_exit_0(self, tmp_lockfile, capsys):
        slug, entry = _make_signed_entry(slug="test-pack")
        entry["publisher_slug"] = "acme-ai"
        entry = seal_entry(entry)
        _write_lockfile(tmp_lockfile, {slug: entry})
        with patch(
            "agentnode_sdk.key_status.check_key_status",
            return_value=_mock_key_status("active"),
        ):
            rc = main(["lock", "verify", "--online"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "verified online" in out

    def test_online_revoked_key_exit_1(self, tmp_lockfile, capsys):
        slug, entry = _make_signed_entry(slug="test-pack")
        entry["publisher_slug"] = "acme-ai"
        entry = seal_entry(entry)
        _write_lockfile(tmp_lockfile, {slug: entry})
        with patch(
            "agentnode_sdk.key_status.check_key_status",
            return_value=_mock_key_status("revoked"),
        ):
            rc = main(["lock", "verify", "--online"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "REVOKED" in out

    def test_online_unknown_key_exit_1(self, tmp_lockfile, capsys):
        slug, entry = _make_signed_entry(slug="test-pack")
        entry["publisher_slug"] = "acme-ai"
        entry = seal_entry(entry)
        _write_lockfile(tmp_lockfile, {slug: entry})
        with patch(
            "agentnode_sdk.key_status.check_key_status",
            return_value=_mock_key_status("unknown", error="Key not found in registry"),
        ):
            rc = main(["lock", "verify", "--online"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "not found" in out

    def test_online_network_error_exit_1(self, tmp_lockfile, capsys):
        slug, entry = _make_signed_entry(slug="test-pack")
        entry["publisher_slug"] = "acme-ai"
        entry = seal_entry(entry)
        _write_lockfile(tmp_lockfile, {slug: entry})
        with patch(
            "agentnode_sdk.key_status.check_key_status",
            return_value=_mock_key_status("error", error="Registry unreachable"),
        ):
            rc = main(["lock", "verify", "--online"])
        assert rc == 1

    def test_online_mismatch_exit_1(self, tmp_lockfile, capsys):
        slug, entry = _make_signed_entry(slug="test-pack")
        entry["publisher_slug"] = "acme-ai"
        entry = seal_entry(entry)
        _write_lockfile(tmp_lockfile, {slug: entry})
        with patch(
            "agentnode_sdk.key_status.check_key_status",
            return_value=_mock_key_status("mismatch", error="Cached public key does not match registry"),
        ):
            rc = main(["lock", "verify", "--online"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "MISMATCH" in out

    def test_online_json_includes_key_status(self, tmp_lockfile, capsys):
        slug, entry = _make_signed_entry(slug="test-pack")
        entry["publisher_slug"] = "acme-ai"
        entry = seal_entry(entry)
        _write_lockfile(tmp_lockfile, {slug: entry})
        with patch(
            "agentnode_sdk.key_status.check_key_status",
            return_value=_mock_key_status("active"),
        ):
            rc = main(["lock", "verify", "--online", "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        assert "key_status" in report
        assert report["key_status"][slug]["status"] == "active"
        assert "key_status_failures" not in report

    def test_online_json_includes_failures(self, tmp_lockfile, capsys):
        slug, entry = _make_signed_entry(slug="test-pack")
        entry["publisher_slug"] = "acme-ai"
        entry = seal_entry(entry)
        _write_lockfile(tmp_lockfile, {slug: entry})
        with patch(
            "agentnode_sdk.key_status.check_key_status",
            return_value=_mock_key_status("revoked"),
        ):
            rc = main(["lock", "verify", "--online", "--json"])
        assert rc == 1
        report = json.loads(capsys.readouterr().out)
        assert report["ok"] is False
        assert "key_status_failures" in report
        assert slug in report["key_status_failures"]

    def test_online_unsigned_package_skipped(self, tmp_lockfile, capsys):
        entry = seal_entry(_make_entry(publisher_slug="acme-ai"))
        _write_lockfile(tmp_lockfile, {"test-pack": entry})
        with patch(
            "agentnode_sdk.key_status.check_key_status",
        ) as mock_ks:
            rc = main(["lock", "verify", "--online"])
        assert rc == 0
        mock_ks.assert_not_called()

    def test_online_no_publisher_slug_skipped(self, tmp_lockfile, capsys):
        slug, entry = _make_signed_entry(slug="test-pack")
        entry = seal_entry(entry)
        _write_lockfile(tmp_lockfile, {slug: entry})
        with patch(
            "agentnode_sdk.key_status.check_key_status",
        ) as mock_ks:
            rc = main(["lock", "verify", "--online"])
        assert rc == 0
        mock_ks.assert_not_called()

    def test_online_without_signed_packages_exit_0(self, tmp_lockfile, capsys):
        entry = seal_entry(_make_entry())
        _write_lockfile(tmp_lockfile, {"test-pack": entry})
        rc = main(["lock", "verify", "--online"])
        assert rc == 0

    def test_online_empty_lockfile_exit_0(self, tmp_lockfile, capsys):
        _write_lockfile(tmp_lockfile, {})
        rc = main(["lock", "verify", "--online"])
        assert rc == 0
