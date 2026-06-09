"""Tests for lockfile entry integrity — Phase 15.1.

Tests verify:
- Canonical hash determinism and stability
- Hash sensitivity to each canonical field
- Hash insensitivity to each mutable field
- Seal/verify round-trip
- Sensitive change detection
- Field classification completeness
"""
import copy
import json

import pytest

from agentnode_sdk.lock_integrity import (
    CANONICAL_FIELDS,
    CANONICAL_FIELDS_V2,
    CANONICAL_FIELDS_V3,
    CANONICAL_VERSION,
    MUTABLE_FIELDS,
    PERMISSION_ESCALATIONS,
    SENSITIVE_FIELDS,
    IntegrityResult,
    SensitiveChange,
    _build_canonical,
    _detect_canonical_version,
    compute_integrity,
    detect_sensitive_changes,
    seal_entry,
    verify_entry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_entry(**overrides) -> dict:
    """Build a realistic lockfile entry with all known fields."""
    entry = {
        # Canonical fields
        "version": "1.0.0",
        "package_type": "toolpack",
        "runtime": "python",
        "entrypoint": "my_pack.tool",
        "artifact_hash": "sha256:abc123def456",
        "tools": [
            {"name": "do_thing", "entrypoint": "my_pack.tool:do_thing", "action_type": "read"}
        ],
        "permissions": {
            "network_level": "none",
            "filesystem_level": "none",
            "code_execution_level": "none",
            "data_access_level": "input_only",
            "user_approval_level": "never",
        },
        "mcp_command": None,
        "remote_endpoint": None,
        "connector": None,
        "agent": None,
        "prompts": [],
        "resources": [],
        "assets": None,
        # Mutable fields
        "installed_at": "2026-05-20T10:00:00+00:00",
        "last_trust_check": "2026-05-20T10:00:00+00:00",
        "trust_level": "trusted",
        "source": "sdk",
        "install_path": None,
        "install_mode": None,
        "capability_ids": ["data_cleaning"],
    }
    entry.update(overrides)
    return entry


def _make_remote_entry(**overrides) -> dict:
    """Build a remote/connector lockfile entry."""
    entry = _make_entry(
        runtime="remote",
        remote_endpoint="https://api.slack.com/v1",
        connector={
            "provider": "slack",
            "auth_type": "oauth2",
            "scopes": ["channels:read"],
            "health_check": {"endpoint": "https://api.slack.com/health"},
        },
        permissions={
            "network_level": "full",
            "filesystem_level": "none",
            "code_execution_level": "none",
            "data_access_level": "input_only",
            "user_approval_level": "never",
        },
    )
    entry.update(overrides)
    return entry


def _make_mcp_entry(**overrides) -> dict:
    """Build an MCP lockfile entry."""
    entry = _make_entry(
        runtime="mcp",
        mcp_command=["python", "-m", "my_server"],
    )
    entry.update(overrides)
    return entry


def _make_skill_entry(**overrides) -> dict:
    """Build a skill lockfile entry."""
    entry = _make_entry(
        package_type="skill",
        runtime="none",
        install_mode="prompt_only",
        entrypoint="",
        tools=[],
        prompts=[{"name": "greet", "template": "greet.md"}],
        resources=[],
        assets=[{"path": "greet.md", "type": "template"}],
        install_path="/home/user/.agentnode/skills/my-skill",
    )
    entry.update(overrides)
    return entry


# ---------------------------------------------------------------------------
# Hash determinism
# ---------------------------------------------------------------------------

class TestHashDeterminism:
    def test_same_entry_same_hash(self):
        e = _make_entry()
        h1 = compute_integrity(e)
        h2 = compute_integrity(e)
        assert h1["hash"] == h2["hash"]

    def test_deep_copy_same_hash(self):
        e = _make_entry()
        h1 = compute_integrity(e)
        h2 = compute_integrity(copy.deepcopy(e))
        assert h1["hash"] == h2["hash"]

    def test_field_ordering_irrelevant(self):
        e1 = _make_entry()
        e2 = dict(reversed(list(e1.items())))
        assert compute_integrity(e1)["hash"] == compute_integrity(e2)["hash"]

    def test_integrity_shape(self):
        result = compute_integrity(_make_entry())
        assert result["algorithm"] == "sha256"
        assert result["canonical_version"] == 1
        assert isinstance(result["hash"], str)
        assert len(result["hash"]) == 64  # SHA256 hex

    def test_remote_entry_deterministic(self):
        e = _make_remote_entry()
        h1 = compute_integrity(e)
        h2 = compute_integrity(copy.deepcopy(e))
        assert h1["hash"] == h2["hash"]

    def test_mcp_entry_deterministic(self):
        e = _make_mcp_entry()
        h1 = compute_integrity(e)
        h2 = compute_integrity(copy.deepcopy(e))
        assert h1["hash"] == h2["hash"]

    def test_skill_entry_deterministic(self):
        e = _make_skill_entry()
        h1 = compute_integrity(e)
        h2 = compute_integrity(copy.deepcopy(e))
        assert h1["hash"] == h2["hash"]


# ---------------------------------------------------------------------------
# Hash changes when canonical fields mutate
# ---------------------------------------------------------------------------

class TestCanonicalFieldSensitivity:
    """Hash must change when ANY canonical field is mutated."""

    def test_version_change(self):
        e = _make_entry()
        h1 = compute_integrity(e)["hash"]
        e["version"] = "2.0.0"
        h2 = compute_integrity(e)["hash"]
        assert h1 != h2

    def test_package_type_change(self):
        e = _make_entry()
        h1 = compute_integrity(e)["hash"]
        e["package_type"] = "agent"
        h2 = compute_integrity(e)["hash"]
        assert h1 != h2

    def test_runtime_change(self):
        e = _make_entry()
        h1 = compute_integrity(e)["hash"]
        e["runtime"] = "remote"
        h2 = compute_integrity(e)["hash"]
        assert h1 != h2

    def test_entrypoint_change(self):
        e = _make_entry()
        h1 = compute_integrity(e)["hash"]
        e["entrypoint"] = "evil_pack.backdoor"
        h2 = compute_integrity(e)["hash"]
        assert h1 != h2

    def test_artifact_hash_change(self):
        e = _make_entry()
        h1 = compute_integrity(e)["hash"]
        e["artifact_hash"] = "sha256:000000000000"
        h2 = compute_integrity(e)["hash"]
        assert h1 != h2

    def test_tools_change(self):
        e = _make_entry()
        h1 = compute_integrity(e)["hash"]
        e["tools"] = [{"name": "evil", "entrypoint": "evil.mod:run"}]
        h2 = compute_integrity(e)["hash"]
        assert h1 != h2

    def test_tools_action_type_change(self):
        e = _make_entry()
        h1 = compute_integrity(e)["hash"]
        e["tools"][0]["action_type"] = "delete"
        h2 = compute_integrity(e)["hash"]
        assert h1 != h2

    def test_permissions_change(self):
        e = _make_entry()
        h1 = compute_integrity(e)["hash"]
        e["permissions"]["network_level"] = "full"
        h2 = compute_integrity(e)["hash"]
        assert h1 != h2

    def test_mcp_command_change(self):
        e = _make_mcp_entry()
        h1 = compute_integrity(e)["hash"]
        e["mcp_command"] = ["malicious-binary", "--pwn"]
        h2 = compute_integrity(e)["hash"]
        assert h1 != h2

    def test_remote_endpoint_change(self):
        e = _make_remote_entry()
        h1 = compute_integrity(e)["hash"]
        e["remote_endpoint"] = "https://evil.com/steal"
        h2 = compute_integrity(e)["hash"]
        assert h1 != h2

    def test_connector_change(self):
        e = _make_remote_entry()
        h1 = compute_integrity(e)["hash"]
        e["connector"]["auth_type"] = "api_key"
        h2 = compute_integrity(e)["hash"]
        assert h1 != h2

    def test_agent_change(self):
        e = _make_entry(agent={"tool_allowlist": ["safe-tool"], "limits": {}})
        h1 = compute_integrity(e)["hash"]
        e["agent"]["tool_allowlist"] = ["safe-tool", "dangerous-tool"]
        h2 = compute_integrity(e)["hash"]
        assert h1 != h2

    def test_prompts_change(self):
        e = _make_skill_entry()
        h1 = compute_integrity(e)["hash"]
        e["prompts"] = [{"name": "inject", "template": "inject.md"}]
        h2 = compute_integrity(e)["hash"]
        assert h1 != h2

    def test_resources_change(self):
        e = _make_entry(resources=[{"uri": "file:///data.csv"}])
        h1 = compute_integrity(e)["hash"]
        e["resources"] = [{"uri": "file:///evil.csv"}]
        h2 = compute_integrity(e)["hash"]
        assert h1 != h2

    def test_assets_change(self):
        e = _make_skill_entry()
        h1 = compute_integrity(e)["hash"]
        e["assets"] = [{"path": "evil.md", "type": "template"}]
        h2 = compute_integrity(e)["hash"]
        assert h1 != h2


# ---------------------------------------------------------------------------
# Hash unchanged when mutable fields mutate
# ---------------------------------------------------------------------------

class TestMutableFieldInsensitivity:
    """Hash must NOT change when mutable fields are mutated."""

    def test_installed_at_change(self):
        e = _make_entry()
        h1 = compute_integrity(e)["hash"]
        e["installed_at"] = "2099-12-31T23:59:59+00:00"
        assert compute_integrity(e)["hash"] == h1

    def test_last_trust_check_change(self):
        e = _make_entry()
        h1 = compute_integrity(e)["hash"]
        e["last_trust_check"] = "2099-12-31T23:59:59+00:00"
        assert compute_integrity(e)["hash"] == h1

    def test_trust_level_change(self):
        e = _make_entry()
        h1 = compute_integrity(e)["hash"]
        e["trust_level"] = "unverified"
        assert compute_integrity(e)["hash"] == h1

    def test_source_change(self):
        e = _make_entry()
        h1 = compute_integrity(e)["hash"]
        e["source"] = "cli"
        assert compute_integrity(e)["hash"] == h1

    def test_install_path_change(self):
        e = _make_skill_entry()
        h1 = compute_integrity(e)["hash"]
        e["install_path"] = "/other/path/skills/my-skill"
        assert compute_integrity(e)["hash"] == h1

    def test_install_mode_change(self):
        e = _make_skill_entry()
        h1 = compute_integrity(e)["hash"]
        e["install_mode"] = "auto"
        assert compute_integrity(e)["hash"] == h1

    def test_capability_ids_change(self):
        e = _make_entry()
        h1 = compute_integrity(e)["hash"]
        e["capability_ids"] = ["new_category", "other"]
        assert compute_integrity(e)["hash"] == h1


# ---------------------------------------------------------------------------
# _integrity excluded from hash
# ---------------------------------------------------------------------------

class TestIntegrityExclusion:
    def test_integrity_field_excluded(self):
        e = _make_entry()
        h1 = compute_integrity(e)["hash"]
        e["_integrity"] = {"algorithm": "sha256", "canonical_version": 1, "hash": "fake"}
        h2 = compute_integrity(e)["hash"]
        assert h1 == h2

    def test_build_canonical_strips_integrity(self):
        e = _make_entry()
        e["_integrity"] = {"algorithm": "sha256", "canonical_version": 1, "hash": "x"}
        canonical = _build_canonical(e)
        assert "_integrity" not in canonical


# ---------------------------------------------------------------------------
# Missing optional canonical fields are stable
# ---------------------------------------------------------------------------

class TestMissingFieldStability:
    def test_none_fields_omitted(self):
        e = _make_entry()
        assert e["mcp_command"] is None
        assert e["remote_endpoint"] is None
        canonical = _build_canonical(e)
        assert "mcp_command" not in canonical
        assert "remote_endpoint" not in canonical

    def test_absent_field_same_as_none(self):
        e1 = _make_entry()
        e2 = _make_entry()
        del e2["mcp_command"]
        del e2["remote_endpoint"]
        assert compute_integrity(e1)["hash"] == compute_integrity(e2)["hash"]

    def test_empty_list_is_canonical(self):
        """Empty list [] IS included (it's not None). Ensures adding
        a tool to an empty tools list changes the hash."""
        e1 = _make_entry(tools=[])
        e2 = _make_entry(tools=[{"name": "x", "entrypoint": "x:y"}])
        assert compute_integrity(e1)["hash"] != compute_integrity(e2)["hash"]

    def test_none_connector_omitted(self):
        e = _make_entry(connector=None)
        canonical = _build_canonical(e)
        assert "connector" not in canonical


# ---------------------------------------------------------------------------
# seal_entry
# ---------------------------------------------------------------------------

class TestSealEntry:
    def test_adds_integrity(self):
        e = _make_entry()
        sealed = seal_entry(e)
        assert "_integrity" in sealed
        assert sealed["_integrity"]["algorithm"] == "sha256"
        assert sealed["_integrity"]["canonical_version"] == 1
        assert len(sealed["_integrity"]["hash"]) == 64

    def test_idempotent(self):
        e = _make_entry()
        sealed1 = seal_entry(e)
        sealed2 = seal_entry(sealed1)
        assert sealed1["_integrity"]["hash"] == sealed2["_integrity"]["hash"]

    def test_does_not_mutate_original(self):
        e = _make_entry()
        sealed = seal_entry(e)
        assert "_integrity" not in e
        assert "_integrity" in sealed

    def test_all_original_fields_preserved(self):
        e = _make_entry()
        sealed = seal_entry(e)
        for key in e:
            assert key in sealed
            assert sealed[key] == e[key]

    def test_remote_entry_sealed(self):
        e = _make_remote_entry()
        sealed = seal_entry(e)
        assert verify_entry("test", sealed).status == "verified"

    def test_mcp_entry_sealed(self):
        e = _make_mcp_entry()
        sealed = seal_entry(e)
        assert verify_entry("test", sealed).status == "verified"

    def test_skill_entry_sealed(self):
        e = _make_skill_entry()
        sealed = seal_entry(e)
        assert verify_entry("test", sealed).status == "verified"


# ---------------------------------------------------------------------------
# verify_entry
# ---------------------------------------------------------------------------

class TestVerifyEntry:
    def test_verified(self):
        sealed = seal_entry(_make_entry())
        result = verify_entry("test-pack", sealed)
        assert result.status == "verified"
        assert result.slug == "test-pack"

    def test_missing(self):
        e = _make_entry()
        result = verify_entry("test-pack", e)
        assert result.status == "missing"
        assert result.slug == "test-pack"

    def test_mismatch_on_tampered_field(self):
        sealed = seal_entry(_make_entry())
        sealed["entrypoint"] = "evil.backdoor"
        result = verify_entry("test-pack", sealed)
        assert result.status == "mismatch"

    def test_mismatch_on_tampered_hash(self):
        sealed = seal_entry(_make_entry())
        sealed["_integrity"]["hash"] = "0" * 64
        result = verify_entry("test-pack", sealed)
        assert result.status == "mismatch"

    def test_mutable_change_still_verified(self):
        sealed = seal_entry(_make_entry())
        sealed["trust_level"] = "unverified"
        sealed["installed_at"] = "2099-01-01T00:00:00+00:00"
        sealed["capability_ids"] = ["something_else"]
        result = verify_entry("test-pack", sealed)
        assert result.status == "verified"

    def test_verify_after_json_roundtrip(self):
        """Integrity survives JSON serialization/deserialization."""
        sealed = seal_entry(_make_entry())
        roundtripped = json.loads(json.dumps(sealed))
        result = verify_entry("test-pack", roundtripped)
        assert result.status == "verified"


# ---------------------------------------------------------------------------
# detect_sensitive_changes
# ---------------------------------------------------------------------------

class TestDetectSensitiveChanges:
    def test_runtime_swap(self):
        old = _make_entry(runtime="python")
        new = _make_entry(runtime="remote")
        changes = detect_sensitive_changes(old, new)
        fields = [c.field for c in changes]
        assert "runtime" in fields
        rc = next(c for c in changes if c.field == "runtime")
        assert rc.old == "python"
        assert rc.new == "remote"

    def test_entrypoint_change(self):
        old = _make_entry(entrypoint="safe.tool")
        new = _make_entry(entrypoint="evil.backdoor")
        changes = detect_sensitive_changes(old, new)
        fields = [c.field for c in changes]
        assert "entrypoint" in fields

    def test_remote_endpoint_change(self):
        old = _make_remote_entry(remote_endpoint="https://api.legit.com")
        new = _make_remote_entry(remote_endpoint="https://evil.com")
        changes = detect_sensitive_changes(old, new)
        fields = [c.field for c in changes]
        assert "remote_endpoint" in fields

    def test_mcp_command_change(self):
        old = _make_mcp_entry(mcp_command=["python", "-m", "safe"])
        new = _make_mcp_entry(mcp_command=["evil-binary"])
        changes = detect_sensitive_changes(old, new)
        fields = [c.field for c in changes]
        assert "mcp_command" in fields

    def test_package_type_change(self):
        old = _make_entry(package_type="toolpack")
        new = _make_entry(package_type="agent")
        changes = detect_sensitive_changes(old, new)
        fields = [c.field for c in changes]
        assert "package_type" in fields

    def test_permission_escalation_network(self):
        old = _make_entry()
        new = _make_entry()
        new["permissions"]["network_level"] = "full"
        changes = detect_sensitive_changes(old, new)
        fields = [c.field for c in changes]
        assert "permissions.network_level" in fields

    def test_permission_escalation_filesystem(self):
        old = _make_entry()
        new = _make_entry()
        new["permissions"]["filesystem_level"] = "full"
        changes = detect_sensitive_changes(old, new)
        fields = [c.field for c in changes]
        assert "permissions.filesystem_level" in fields

    def test_permission_escalation_code_execution(self):
        old = _make_entry()
        new = _make_entry()
        new["permissions"]["code_execution_level"] = "sandboxed"
        changes = detect_sensitive_changes(old, new)
        fields = [c.field for c in changes]
        assert "permissions.code_execution_level" in fields

    def test_no_escalation_when_already_elevated(self):
        """Escalation only triggers from safe baseline, not from elevated→elevated."""
        old = _make_entry()
        old["permissions"]["network_level"] = "restricted"
        new = _make_entry()
        new["permissions"]["network_level"] = "full"
        changes = detect_sensitive_changes(old, new)
        fields = [c.field for c in changes]
        assert "permissions.network_level" not in fields

    def test_no_changes_for_identical_entries(self):
        e = _make_entry()
        changes = detect_sensitive_changes(e, copy.deepcopy(e))
        assert changes == []

    def test_non_sensitive_changes_ignored(self):
        """Changes to non-sensitive canonical fields don't appear."""
        old = _make_entry(version="1.0.0")
        new = _make_entry(version="2.0.0")
        changes = detect_sensitive_changes(old, new)
        assert changes == []

    def test_mutable_changes_ignored(self):
        old = _make_entry()
        new = _make_entry()
        new["trust_level"] = "unverified"
        new["installed_at"] = "2099-01-01"
        changes = detect_sensitive_changes(old, new)
        assert changes == []

    def test_multiple_sensitive_changes(self):
        old = _make_entry(runtime="python", entrypoint="safe.tool")
        new = _make_entry(runtime="remote", entrypoint="evil.tool")
        changes = detect_sensitive_changes(old, new)
        fields = {c.field for c in changes}
        assert "runtime" in fields
        assert "entrypoint" in fields


# ---------------------------------------------------------------------------
# Field classification completeness
# ---------------------------------------------------------------------------

class TestFieldClassification:
    """Every field in a real lockfile entry must be either canonical,
    mutable, or the _integrity seal itself."""

    # V3 = canonical V1 + _signatures + publisher_slug (all integrity-sealed).
    KNOWN_FIELDS = set(CANONICAL_FIELDS_V3) | set(MUTABLE_FIELDS) | {"_integrity"}

    def test_known_fields_cover_v3(self):
        """Regression: V3 canonical fields (incl. _signatures, publisher_slug)
        must be classified, else a real lockfile carrying them trips the check."""
        assert "publisher_slug" in self.KNOWN_FIELDS
        assert "_signatures" in self.KNOWN_FIELDS

    def test_toolpack_entry_fields_classified(self):
        e = seal_entry(_make_entry())
        unclassified = set(e.keys()) - self.KNOWN_FIELDS
        assert unclassified == set(), f"Unclassified fields: {unclassified}"

    def test_remote_entry_fields_classified(self):
        e = seal_entry(_make_remote_entry())
        unclassified = set(e.keys()) - self.KNOWN_FIELDS
        assert unclassified == set(), f"Unclassified fields: {unclassified}"

    def test_mcp_entry_fields_classified(self):
        e = seal_entry(_make_mcp_entry())
        unclassified = set(e.keys()) - self.KNOWN_FIELDS
        assert unclassified == set(), f"Unclassified fields: {unclassified}"

    def test_skill_entry_fields_classified(self):
        e = seal_entry(_make_skill_entry())
        unclassified = set(e.keys()) - self.KNOWN_FIELDS
        assert unclassified == set(), f"Unclassified fields: {unclassified}"

    def test_real_lockfile_fields_classified(self):
        """Check against the actual agentnode.lock in the repo."""
        import os
        from pathlib import Path
        lock_path = Path(os.getcwd()) / "agentnode.lock"
        if not lock_path.is_file():
            pytest.skip("No agentnode.lock in cwd")
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        for slug, entry in data.get("packages", {}).items():
            unclassified = set(entry.keys()) - self.KNOWN_FIELDS
            assert unclassified == set(), (
                f"Unclassified fields in '{slug}': {unclassified}"
            )


# ---------------------------------------------------------------------------
# Installer integration — Phase 15.3
# ---------------------------------------------------------------------------

class TestInstallerIntegrity:
    """Verify install_package() and _install_skill() seal lockfile entries."""

    @pytest.fixture(autouse=True)
    def _tmp_lockfile(self, tmp_path, monkeypatch):
        self.lf = tmp_path / "agentnode.lock"
        monkeypatch.setenv("AGENTNODE_LOCKFILE", str(self.lf))
        self.tmp_path = tmp_path

    def _read(self):
        from agentnode_sdk.installer import read_lockfile
        return read_lockfile(self.lf)

    def test_install_creates_integrity(self, monkeypatch):
        """install_package() writes _integrity to lockfile entry."""
        from agentnode_sdk import installer

        tar = self.tmp_path / "pkg.tar.gz"
        pkg_dir = self.tmp_path / "extracted" / "my-pack"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "my_pack").mkdir()
        (pkg_dir / "my_pack" / "tool.py").write_text("def run(): pass")

        monkeypatch.setattr(installer, "download_artifact", lambda *a, **kw: None)
        monkeypatch.setattr(installer, "verify_hash", lambda *a, **kw: "abc123")
        monkeypatch.setattr(installer, "extract_archive", lambda *a, **kw: pkg_dir)
        monkeypatch.setattr(installer, "resolve_python", lambda: "python")
        monkeypatch.setattr(installer, "pip_install", lambda *a, **kw: None)

        result = installer.install_package(
            slug="test-pack",
            version="1.0.0",
            artifact_url="https://example.com/pkg.tar.gz",
            artifact_hash="sha256:abc123",
            entrypoint="my_pack.tool",
            trust_level="trusted",
            permissions={"network_level": "none"},
        )
        assert result["installed"] is True

        lock = self._read()
        entry = lock["packages"]["test-pack"]
        assert "_integrity" in entry
        assert verify_entry("test-pack", entry).status == "verified"

    def test_install_with_mcp_command_creates_integrity(self, monkeypatch):
        """MCP entries get sealed too."""
        from agentnode_sdk import installer

        pkg_dir = self.tmp_path / "extracted" / "mcp-pack"
        pkg_dir.mkdir(parents=True)

        monkeypatch.setattr(installer, "download_artifact", lambda *a, **kw: None)
        monkeypatch.setattr(installer, "verify_hash", lambda *a, **kw: "def456")
        monkeypatch.setattr(installer, "extract_archive", lambda *a, **kw: pkg_dir)
        monkeypatch.setattr(installer, "resolve_python", lambda: "python")
        monkeypatch.setattr(installer, "pip_install", lambda *a, **kw: None)

        installer.install_package(
            slug="mcp-pack",
            version="1.0.0",
            artifact_url="https://example.com/mcp.tar.gz",
            runtime="mcp",
            mcp_command=["python", "-m", "mcp_server"],
            trust_level="trusted",
        )

        lock = self._read()
        entry = lock["packages"]["mcp-pack"]
        assert "_integrity" in entry
        assert entry.get("mcp_command") == ["python", "-m", "mcp_server"]
        assert verify_entry("mcp-pack", entry).status == "verified"

    def test_install_mcp_stores_env_keys(self, monkeypatch):
        """MCP install stores mcp_env_keys and protects them with integrity."""
        from agentnode_sdk import installer

        installer.install_package(
            slug="mcp-brave",
            version="0.1.0",
            artifact_url=None,
            runtime="mcp",
            mcp_command=["npx", "-y", "@modelcontextprotocol/server-brave-search@2025.3.28"],
            mcp_env_keys=["BRAVE_API_KEY"],
            trust_level="unverified",
        )

        lock = self._read()
        entry = lock["packages"]["mcp-brave"]
        assert entry["mcp_env_keys"] == ["BRAVE_API_KEY"]
        assert entry["runtime"] == "mcp"
        assert verify_entry("mcp-brave", entry).status == "verified"

        # Tampering with mcp_env_keys must break integrity
        entry["mcp_env_keys"] = ["BRAVE_API_KEY", "AWS_SECRET_KEY"]
        assert verify_entry("mcp-brave", entry).status == "mismatch"

    def test_install_with_remote_endpoint_creates_integrity(self, monkeypatch):
        """Remote entries get sealed too."""
        from agentnode_sdk import installer

        pkg_dir = self.tmp_path / "extracted" / "remote-pack"
        pkg_dir.mkdir(parents=True)

        monkeypatch.setattr(installer, "download_artifact", lambda *a, **kw: None)
        monkeypatch.setattr(installer, "verify_hash", lambda *a, **kw: "ghi789")
        monkeypatch.setattr(installer, "extract_archive", lambda *a, **kw: pkg_dir)
        monkeypatch.setattr(installer, "resolve_python", lambda: "python")
        monkeypatch.setattr(installer, "pip_install", lambda *a, **kw: None)

        installer.install_package(
            slug="remote-pack",
            version="1.0.0",
            artifact_url="https://example.com/remote.tar.gz",
            artifact_hash="sha256:ghi789",
            runtime="remote",
            remote_endpoint="https://api.example.com/v1",
            connector={"provider": "example", "auth_type": "oauth2", "scopes": []},
            trust_level="trusted",
        )

        lock = self._read()
        entry = lock["packages"]["remote-pack"]
        assert "_integrity" in entry
        assert entry["remote_endpoint"] == "https://api.example.com/v1"
        assert verify_entry("remote-pack", entry).status == "verified"

    def test_skill_install_creates_integrity(self, monkeypatch):
        """_install_skill() writes _integrity."""
        from agentnode_sdk import installer
        import tarfile, io

        skill_dir = self.tmp_path / "skill-src"
        skill_dir.mkdir()
        (skill_dir / "agentnode.yaml").write_text("name: test-skill\n")
        (skill_dir / "SKILL.md").write_text("# Test Skill\n")

        tar_path = self.tmp_path / "skill.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(skill_dir, arcname="test-skill")

        monkeypatch.setattr(
            installer, "download_artifact",
            lambda url, dest, **kw: __import__("shutil").copy2(tar_path, dest),
        )

        skills_dir = self.tmp_path / "skills"
        monkeypatch.setattr(installer, "_skills_dir", lambda: skills_dir)

        result = installer.install_package(
            slug="test-skill",
            version="1.0.0",
            artifact_url="https://example.com/skill.tar.gz",
            artifact_hash=None,
            package_type="skill",
            trust_level="trusted",
        )
        assert result["installed"] is True

        lock = self._read()
        entry = lock["packages"]["test-skill"]
        assert "_integrity" in entry
        assert verify_entry("test-skill", entry).status == "verified"

    def test_reinstall_recomputes_integrity(self, monkeypatch):
        """Upgrading a package recomputes _integrity with new canonical values."""
        from agentnode_sdk import installer
        from agentnode_sdk.installer import update_lockfile

        pkg_dir = self.tmp_path / "extracted" / "my-pack"
        pkg_dir.mkdir(parents=True)

        monkeypatch.setattr(installer, "download_artifact", lambda *a, **kw: None)
        monkeypatch.setattr(installer, "verify_hash", lambda *a, **kw: "v1hash")
        monkeypatch.setattr(installer, "extract_archive", lambda *a, **kw: pkg_dir)
        monkeypatch.setattr(installer, "resolve_python", lambda: "python")
        monkeypatch.setattr(installer, "pip_install", lambda *a, **kw: None)

        installer.install_package(
            slug="upgrade-pack", version="1.0.0",
            artifact_url="https://example.com/pkg.tar.gz",
            artifact_hash="sha256:v1hash",
            entrypoint="my_pack.tool",
            trust_level="trusted",
        )

        lock = self._read()
        hash_v1 = lock["packages"]["upgrade-pack"]["_integrity"]["hash"]

        monkeypatch.setattr(installer, "verify_hash", lambda *a, **kw: "v2hash")
        installer.install_package(
            slug="upgrade-pack", version="2.0.0",
            artifact_url="https://example.com/pkg2.tar.gz",
            artifact_hash="sha256:v2hash",
            entrypoint="my_pack.tool_v2",
            trust_level="trusted",
        )

        lock = self._read()
        hash_v2 = lock["packages"]["upgrade-pack"]["_integrity"]["hash"]
        assert hash_v1 != hash_v2
        assert verify_entry("upgrade-pack", lock["packages"]["upgrade-pack"]).status == "verified"

    def test_update_lockfile_preserves_unrelated_integrity(self, monkeypatch):
        """Installing package B does not strip _integrity from package A."""
        from agentnode_sdk.installer import update_lockfile

        sealed_a = seal_entry(_make_entry(version="1.0.0"))
        update_lockfile("pack-a", sealed_a, path=self.lf)

        entry_b = _make_entry(version="2.0.0")
        entry_b = seal_entry(entry_b)
        update_lockfile("pack-b", entry_b, path=self.lf)

        lock = self._read()
        assert "_integrity" in lock["packages"]["pack-a"]
        assert verify_entry("pack-a", lock["packages"]["pack-a"]).status == "verified"
        assert "_integrity" in lock["packages"]["pack-b"]
        assert verify_entry("pack-b", lock["packages"]["pack-b"]).status == "verified"

    def test_trust_level_mutation_preserves_integrity(self):
        """Simulating trust refresh: changing trust_level does not break verify."""
        from agentnode_sdk.installer import update_lockfile

        sealed = seal_entry(_make_entry(trust_level="trusted"))
        update_lockfile("trust-test", sealed, path=self.lf)

        lock = self._read()
        entry = lock["packages"]["trust-test"]
        entry["trust_level"] = "unverified"
        entry["last_trust_check"] = "2099-01-01T00:00:00+00:00"
        update_lockfile("trust-test", entry, path=self.lf)

        lock = self._read()
        result = verify_entry("trust-test", lock["packages"]["trust-test"])
        assert result.status == "verified"

    def test_read_lockfile_does_not_auto_seal(self):
        """read_lockfile() must never add _integrity. That's an install-only action."""
        from agentnode_sdk.installer import read_lockfile
        from agentnode_sdk._fileutil import atomic_write_json

        data = {
            "lockfile_version": "0.1",
            "updated_at": "2026-05-20T00:00:00+00:00",
            "packages": {
                "unsealed-pack": _make_entry(),
            },
        }
        atomic_write_json(self.lf, data)

        lock = read_lockfile(self.lf)
        entry = lock["packages"]["unsealed-pack"]
        assert "_integrity" not in entry


# ---------------------------------------------------------------------------
# Canonical version v2 — Phase 16.1
# ---------------------------------------------------------------------------

def _make_signatures_block():
    """Return a realistic _signatures dict for testing."""
    return {
        "publisher": [{
            "key_id": "ed25519:a1b2c3d4",
            "algorithm": "ed25519",
            "signature": "dGVzdHNpZw==",
            "public_key": "dGVzdGtleQ==",
            "canonical_version": 1,
            "signed_at": "2026-05-21T12:00:00+00:00",
        }],
    }


class TestCanonicalVersionV2:
    """canonical_version v2 includes _signatures in the integrity hash."""

    def test_v2_constant(self):
        assert CANONICAL_VERSION == 3

    def test_v2_fields_include_signatures(self):
        assert "_signatures" in CANONICAL_FIELDS_V2
        assert "_signatures" not in CANONICAL_FIELDS

    def test_detect_version_without_signatures(self):
        e = _make_entry()
        assert _detect_canonical_version(e) == 1

    def test_detect_version_with_signatures(self):
        e = _make_entry()
        e["_signatures"] = _make_signatures_block()
        assert _detect_canonical_version(e) == 2

    def test_detect_version_empty_signatures(self):
        e = _make_entry()
        e["_signatures"] = {}
        assert _detect_canonical_version(e) == 1

    def test_detect_version_none_signatures(self):
        e = _make_entry()
        e["_signatures"] = None
        assert _detect_canonical_version(e) == 1

    def test_v1_hash_excludes_signatures(self):
        e = _make_entry()
        h1 = compute_integrity(e, canonical_version=1)["hash"]
        e["_signatures"] = _make_signatures_block()
        h2 = compute_integrity(e, canonical_version=1)["hash"]
        assert h1 == h2

    def test_v2_hash_includes_signatures(self):
        e = _make_entry()
        h_no_sig = compute_integrity(e, canonical_version=2)["hash"]
        e["_signatures"] = _make_signatures_block()
        h_with_sig = compute_integrity(e, canonical_version=2)["hash"]
        assert h_no_sig != h_with_sig

    def test_seal_without_signatures_produces_v1(self):
        e = _make_entry()
        sealed = seal_entry(e)
        assert sealed["_integrity"]["canonical_version"] == 1

    def test_seal_with_signatures_produces_v2(self):
        e = _make_entry()
        e["_signatures"] = _make_signatures_block()
        sealed = seal_entry(e)
        assert sealed["_integrity"]["canonical_version"] == 2

    def test_v1_entry_verifies_against_v1_fields(self):
        e = _make_entry()
        sealed = seal_entry(e)
        assert sealed["_integrity"]["canonical_version"] == 1
        assert verify_entry("test", sealed).status == "verified"

    def test_v2_entry_verifies_against_v2_fields(self):
        e = _make_entry()
        e["_signatures"] = _make_signatures_block()
        sealed = seal_entry(e)
        assert sealed["_integrity"]["canonical_version"] == 2
        assert verify_entry("test", sealed).status == "verified"

    def test_signature_swap_causes_v2_mismatch(self):
        """Swapping _signatures on a v2 entry must break integrity."""
        e = _make_entry()
        e["_signatures"] = _make_signatures_block()
        sealed = seal_entry(e)
        sealed["_signatures"]["publisher"][0]["public_key"] = "YXR0YWNrZXI="
        assert verify_entry("test", sealed).status == "mismatch"

    def test_adding_signatures_to_v1_entry_causes_mismatch(self):
        """If an attacker adds _signatures to a v1 entry, the v1 hash
        is still verified against v1 fields (which exclude _signatures).
        The entry itself is not broken — but the _integrity is v1 so
        the signature is simply not integrity-protected. This is expected:
        the entry was sealed before signatures existed."""
        e = _make_entry()
        sealed = seal_entry(e)
        assert sealed["_integrity"]["canonical_version"] == 1
        sealed["_signatures"] = _make_signatures_block()
        assert verify_entry("test", sealed).status == "verified"

    def test_v2_idempotent_seal(self):
        e = _make_entry()
        e["_signatures"] = _make_signatures_block()
        sealed1 = seal_entry(e)
        sealed2 = seal_entry(sealed1)
        assert sealed1["_integrity"]["hash"] == sealed2["_integrity"]["hash"]
        assert sealed1["_integrity"]["canonical_version"] == 2

    def test_v2_survives_json_roundtrip(self):
        e = _make_entry()
        e["_signatures"] = _make_signatures_block()
        sealed = seal_entry(e)
        roundtripped = json.loads(json.dumps(sealed))
        assert verify_entry("test", roundtripped).status == "verified"

    def test_build_canonical_v1_excludes_signatures(self):
        e = _make_entry()
        e["_signatures"] = _make_signatures_block()
        canonical = _build_canonical(e, canonical_version=1)
        assert "_signatures" not in canonical

    def test_build_canonical_v2_includes_signatures(self):
        e = _make_entry()
        e["_signatures"] = _make_signatures_block()
        canonical = _build_canonical(e, canonical_version=2)
        assert "_signatures" in canonical


# ---------------------------------------------------------------------------
# Install signature verification — Phase 16.4
# ---------------------------------------------------------------------------

def _real_sign_entry(slug, entry):
    """Sign an entry with a real Ed25519 key (test helper)."""
    import base64
    import hashlib as _hashlib

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


class TestInstallerSignatureVerification:
    """Verify install_package() enforces publisher signatures."""

    @pytest.fixture(autouse=True)
    def _tmp_lockfile(self, tmp_path, monkeypatch):
        self.lf = tmp_path / "agentnode.lock"
        monkeypatch.setenv("AGENTNODE_LOCKFILE", str(self.lf))
        self.tmp_path = tmp_path

    def _read(self):
        from agentnode_sdk.installer import read_lockfile
        return read_lockfile(self.lf)

    def _mock_install(self, monkeypatch):
        from agentnode_sdk import installer
        pkg_dir = self.tmp_path / "extracted" / "pkg"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "my_pack").mkdir(exist_ok=True)
        (pkg_dir / "my_pack" / "tool.py").write_text("def run(): pass")
        monkeypatch.setattr(installer, "download_artifact", lambda *a, **kw: None)
        monkeypatch.setattr(installer, "verify_hash", lambda *a, **kw: "abc123def456")
        monkeypatch.setattr(installer, "extract_archive", lambda *a, **kw: pkg_dir)
        monkeypatch.setattr(installer, "resolve_python", lambda: "python")
        monkeypatch.setattr(installer, "pip_install", lambda *a, **kw: None)

    def _make_valid_signatures(self, slug, **overrides):
        """Build signatures that verify against the lock_entry install_package creates."""
        entry = {
            "version": overrides.get("version", "1.0.0"),
            "package_type": overrides.get("package_type", "toolpack"),
            "runtime": overrides.get("runtime", "python"),
            "entrypoint": overrides.get("entrypoint", "my_pack.tool"),
            "artifact_hash": "sha256:abc123def456",
            "tools": overrides.get("tools", []),
            "permissions": overrides.get("permissions", {"network_level": "none"}),
            "prompts": overrides.get("prompts", []),
            "resources": overrides.get("resources", []),
            "connector": overrides.get("connector", None),
            "agent": overrides.get("agent", None),
        }
        return _real_sign_entry(slug, entry)

    def test_valid_signature_installs(self, monkeypatch):
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package

        sigs = self._make_valid_signatures("test-pack")
        result = install_package(
            slug="test-pack",
            version="1.0.0",
            artifact_url="https://example.com/pkg.tar.gz",
            artifact_hash="sha256:abc123def456",
            entrypoint="my_pack.tool",
            trust_level="trusted",
            permissions={"network_level": "none"},
            signatures=sigs,
        )
        assert result["installed"] is True

    def test_valid_signature_stored_in_lockfile(self, monkeypatch):
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package

        sigs = self._make_valid_signatures("test-pack")
        install_package(
            slug="test-pack",
            version="1.0.0",
            artifact_url="https://example.com/pkg.tar.gz",
            artifact_hash="sha256:abc123def456",
            entrypoint="my_pack.tool",
            trust_level="trusted",
            permissions={"network_level": "none"},
            signatures=sigs,
        )
        lock = self._read()
        entry = lock["packages"]["test-pack"]
        assert "_signatures" in entry
        assert entry["_signatures"]["publisher"][0]["algorithm"] == "ed25519"

    def test_valid_signature_verifies_from_lockfile(self, monkeypatch):
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package
        from agentnode_sdk.signature import verify_entry_signature, SignatureStatus

        sigs = self._make_valid_signatures("test-pack")
        install_package(
            slug="test-pack",
            version="1.0.0",
            artifact_url="https://example.com/pkg.tar.gz",
            artifact_hash="sha256:abc123def456",
            entrypoint="my_pack.tool",
            trust_level="trusted",
            permissions={"network_level": "none"},
            signatures=sigs,
        )
        lock = self._read()
        entry = lock["packages"]["test-pack"]
        result = verify_entry_signature("test-pack", entry)
        assert result.status == SignatureStatus.VALID

    def test_signed_entry_gets_integrity_v2(self, monkeypatch):
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package

        sigs = self._make_valid_signatures("test-pack")
        install_package(
            slug="test-pack",
            version="1.0.0",
            artifact_url="https://example.com/pkg.tar.gz",
            artifact_hash="sha256:abc123def456",
            entrypoint="my_pack.tool",
            trust_level="trusted",
            permissions={"network_level": "none"},
            signatures=sigs,
        )
        lock = self._read()
        entry = lock["packages"]["test-pack"]
        assert entry["_integrity"]["canonical_version"] == 2

    def test_missing_signature_warns_but_installs(self, monkeypatch):
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package
        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = install_package(
                slug="test-pack",
                version="1.0.0",
                artifact_url="https://example.com/pkg.tar.gz",
                artifact_hash="sha256:abc123def456",
                entrypoint="my_pack.tool",
                trust_level="trusted",
                permissions={"network_level": "none"},
            )
        assert result["installed"] is True
        sig_warnings = [x for x in w if "no publisher signature" in str(x.message)]
        assert len(sig_warnings) >= 1

    def test_unsigned_entry_gets_integrity_v1(self, monkeypatch):
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package

        install_package(
            slug="test-pack",
            version="1.0.0",
            artifact_url="https://example.com/pkg.tar.gz",
            artifact_hash="sha256:abc123def456",
            entrypoint="my_pack.tool",
            trust_level="trusted",
            permissions={"network_level": "none"},
        )
        lock = self._read()
        entry = lock["packages"]["test-pack"]
        assert entry["_integrity"]["canonical_version"] == 1
        assert "_signatures" not in entry

    def test_invalid_signature_blocks_install(self, monkeypatch):
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package

        sigs = self._make_valid_signatures("test-pack")
        sigs["publisher"][0]["signature"] = "AAAA" * 22

        with pytest.raises(RuntimeError, match="signature verification failed"):
            install_package(
                slug="test-pack",
                version="1.0.0",
                artifact_url="https://example.com/pkg.tar.gz",
                artifact_hash="sha256:abc123def456",
                entrypoint="my_pack.tool",
                trust_level="trusted",
                permissions={"network_level": "none"},
                signatures=sigs,
            )
        assert not self.lf.exists()

    def test_malformed_signature_blocks_install(self, monkeypatch):
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package

        sigs = {
            "publisher": [{
                "key_id": "ed25519:bad",
                "algorithm": "ed25519",
                "signature": "not-valid-base64!!!",
                "public_key": "also-bad!!!",
            }],
        }
        with pytest.raises(RuntimeError, match="signature verification failed"):
            install_package(
                slug="test-pack",
                version="1.0.0",
                artifact_url="https://example.com/pkg.tar.gz",
                artifact_hash="sha256:abc123def456",
                entrypoint="my_pack.tool",
                trust_level="trusted",
                permissions={"network_level": "none"},
                signatures=sigs,
            )
        assert not self.lf.exists()

    def test_wrong_public_key_blocks_install(self, monkeypatch):
        import base64
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package
        from agentnode_sdk.signing_key import generate_ed25519_keypair
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        sigs = self._make_valid_signatures("test-pack")
        _, other_pub = generate_ed25519_keypair()
        sigs["publisher"][0]["public_key"] = base64.b64encode(other_pub).decode()

        with pytest.raises(RuntimeError, match="signature verification failed"):
            install_package(
                slug="test-pack",
                version="1.0.0",
                artifact_url="https://example.com/pkg.tar.gz",
                artifact_hash="sha256:abc123def456",
                entrypoint="my_pack.tool",
                trust_level="trusted",
                permissions={"network_level": "none"},
                signatures=sigs,
            )

    def test_install_does_not_load_local_signing_key(self, monkeypatch):
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package

        sigs = self._make_valid_signatures("test-pack")

        import agentnode_sdk.signing_key as sk_mod
        original_load = sk_mod.load_signing_key
        load_called = []
        def spy_load(*a, **kw):
            load_called.append(True)
            return original_load(*a, **kw)
        monkeypatch.setattr(sk_mod, "load_signing_key", spy_load)
        monkeypatch.setattr(sk_mod, "get_or_create_signing_key", spy_load)

        install_package(
            slug="test-pack",
            version="1.0.0",
            artifact_url="https://example.com/pkg.tar.gz",
            artifact_hash="sha256:abc123def456",
            entrypoint="my_pack.tool",
            trust_level="trusted",
            permissions={"network_level": "none"},
            signatures=sigs,
        )
        assert len(load_called) == 0

    def test_signature_uses_downloaded_artifact_hash(self, monkeypatch):
        """The signature must verify against the hash from the downloaded
        artifact, not a registry-provided value that could be spoofed."""
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package

        spoofed_entry = {
            "version": "1.0.0",
            "package_type": "toolpack",
            "runtime": "python",
            "entrypoint": "my_pack.tool",
            "artifact_hash": "sha256:spoofed_different_hash",
            "tools": [],
            "permissions": {"network_level": "none"},
            "prompts": [],
            "resources": [],
            "connector": None,
            "agent": None,
        }
        sigs = _real_sign_entry("test-pack", spoofed_entry)

        with pytest.raises(RuntimeError, match="signature verification failed"):
            install_package(
                slug="test-pack",
                version="1.0.0",
                artifact_url="https://example.com/pkg.tar.gz",
                artifact_hash="sha256:spoofed_different_hash",
                entrypoint="my_pack.tool",
                trust_level="trusted",
                permissions={"network_level": "none"},
                signatures=sigs,
            )


# ---------------------------------------------------------------------------
# Canonical version v3 — Phase 16.6
# ---------------------------------------------------------------------------

class TestCanonicalVersionV3:
    """canonical_version v3 includes publisher_slug in the integrity hash."""

    def test_v3_constant(self):
        assert CANONICAL_VERSION == 3

    def test_v3_fields_include_publisher_slug(self):
        assert "publisher_slug" in CANONICAL_FIELDS_V3
        assert "publisher_slug" not in CANONICAL_FIELDS_V2
        assert "publisher_slug" not in CANONICAL_FIELDS

    def test_v3_includes_all_v2_fields(self):
        for f in CANONICAL_FIELDS_V2:
            assert f in CANONICAL_FIELDS_V3

    def test_detect_version_with_publisher_slug(self):
        e = _make_entry(publisher_slug="acme-ai")
        assert _detect_canonical_version(e) == 3

    def test_detect_version_publisher_slug_with_signatures(self):
        e = _make_entry(publisher_slug="acme-ai")
        e["_signatures"] = _make_signatures_block()
        assert _detect_canonical_version(e) == 3

    def test_detect_version_signatures_only_stays_v2(self):
        e = _make_entry()
        e["_signatures"] = _make_signatures_block()
        assert _detect_canonical_version(e) == 2

    def test_detect_version_no_publisher_no_signatures_stays_v1(self):
        e = _make_entry()
        assert _detect_canonical_version(e) == 1

    def test_detect_version_empty_string_publisher_slug(self):
        e = _make_entry(publisher_slug="")
        assert _detect_canonical_version(e) == 1

    def test_detect_version_whitespace_only_publisher_slug(self):
        e = _make_entry(publisher_slug="   ")
        assert _detect_canonical_version(e) == 1

    def test_detect_version_none_publisher_slug(self):
        e = _make_entry(publisher_slug=None)
        assert _detect_canonical_version(e) == 1

    def test_v3_hash_includes_publisher_slug(self):
        e = _make_entry()
        h_no_pub = compute_integrity(e, canonical_version=3)["hash"]
        e["publisher_slug"] = "acme-ai"
        h_with_pub = compute_integrity(e, canonical_version=3)["hash"]
        assert h_no_pub != h_with_pub

    def test_v2_hash_does_not_include_publisher_slug(self):
        e = _make_entry()
        h1 = compute_integrity(e, canonical_version=2)["hash"]
        e["publisher_slug"] = "acme-ai"
        h2 = compute_integrity(e, canonical_version=2)["hash"]
        assert h1 == h2

    def test_v1_hash_unaffected_by_publisher_slug(self):
        e = _make_entry()
        h1 = compute_integrity(e, canonical_version=1)["hash"]
        e["publisher_slug"] = "acme-ai"
        h2 = compute_integrity(e, canonical_version=1)["hash"]
        assert h1 == h2

    def test_seal_with_publisher_slug_no_signatures_produces_v3(self):
        e = _make_entry(publisher_slug="acme-ai")
        sealed = seal_entry(e)
        assert sealed["_integrity"]["canonical_version"] == 3

    def test_seal_with_publisher_slug_and_signatures_produces_v3(self):
        e = _make_entry(publisher_slug="acme-ai")
        e["_signatures"] = _make_signatures_block()
        sealed = seal_entry(e)
        assert sealed["_integrity"]["canonical_version"] == 3

    def test_seal_with_signatures_only_produces_v2(self):
        e = _make_entry()
        e["_signatures"] = _make_signatures_block()
        sealed = seal_entry(e)
        assert sealed["_integrity"]["canonical_version"] == 2

    def test_seal_without_either_produces_v1(self):
        e = _make_entry()
        sealed = seal_entry(e)
        assert sealed["_integrity"]["canonical_version"] == 1

    def test_v1_entry_still_verifies(self):
        e = _make_entry()
        sealed = seal_entry(e)
        assert sealed["_integrity"]["canonical_version"] == 1
        assert verify_entry("test", sealed).status == "verified"

    def test_v2_entry_still_verifies(self):
        e = _make_entry()
        e["_signatures"] = _make_signatures_block()
        sealed = seal_entry(e)
        assert sealed["_integrity"]["canonical_version"] == 2
        assert verify_entry("test", sealed).status == "verified"

    def test_v3_entry_verifies(self):
        e = _make_entry(publisher_slug="acme-ai")
        sealed = seal_entry(e)
        assert sealed["_integrity"]["canonical_version"] == 3
        assert verify_entry("test", sealed).status == "verified"

    def test_changing_publisher_slug_causes_v3_mismatch(self):
        e = _make_entry(publisher_slug="acme-ai")
        sealed = seal_entry(e)
        sealed["publisher_slug"] = "evil-corp"
        assert verify_entry("test", sealed).status == "mismatch"

    def test_v3_idempotent_seal(self):
        e = _make_entry(publisher_slug="acme-ai")
        sealed1 = seal_entry(e)
        sealed2 = seal_entry(sealed1)
        assert sealed1["_integrity"]["hash"] == sealed2["_integrity"]["hash"]
        assert sealed1["_integrity"]["canonical_version"] == 3

    def test_v3_survives_json_roundtrip(self):
        e = _make_entry(publisher_slug="acme-ai")
        sealed = seal_entry(e)
        roundtripped = json.loads(json.dumps(sealed))
        assert verify_entry("test", roundtripped).status == "verified"

    def test_build_canonical_v3_includes_publisher_slug(self):
        e = _make_entry(publisher_slug="acme-ai")
        canonical = _build_canonical(e, canonical_version=3)
        assert "publisher_slug" in canonical

    def test_build_canonical_v2_excludes_publisher_slug(self):
        e = _make_entry(publisher_slug="acme-ai")
        canonical = _build_canonical(e, canonical_version=2)
        assert "publisher_slug" not in canonical

    def test_build_canonical_v1_excludes_publisher_slug(self):
        e = _make_entry(publisher_slug="acme-ai")
        canonical = _build_canonical(e, canonical_version=1)
        assert "publisher_slug" not in canonical


# ---------------------------------------------------------------------------
# Installer publisher_slug storage — Phase 16.6
# ---------------------------------------------------------------------------

class TestInstallerPublisherSlug:
    """Verify install_package() stores publisher_slug correctly."""

    @pytest.fixture(autouse=True)
    def _tmp_lockfile(self, tmp_path, monkeypatch):
        self.lf = tmp_path / "agentnode.lock"
        monkeypatch.setenv("AGENTNODE_LOCKFILE", str(self.lf))
        self.tmp_path = tmp_path

    def _read(self):
        from agentnode_sdk.installer import read_lockfile
        return read_lockfile(self.lf)

    def _mock_install(self, monkeypatch):
        from agentnode_sdk import installer
        pkg_dir = self.tmp_path / "extracted" / "pkg"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "my_pack").mkdir(exist_ok=True)
        (pkg_dir / "my_pack" / "tool.py").write_text("def run(): pass")
        monkeypatch.setattr(installer, "download_artifact", lambda *a, **kw: None)
        monkeypatch.setattr(installer, "verify_hash", lambda *a, **kw: "abc123def456")
        monkeypatch.setattr(installer, "extract_archive", lambda *a, **kw: pkg_dir)
        monkeypatch.setattr(installer, "resolve_python", lambda: "python")
        monkeypatch.setattr(installer, "pip_install", lambda *a, **kw: None)

    def test_publisher_slug_stored_at_entry_level(self, monkeypatch):
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package

        install_package(
            slug="test-pack",
            version="1.0.0",
            artifact_url="https://example.com/pkg.tar.gz",
            artifact_hash="sha256:abc123def456",
            entrypoint="my_pack.tool",
            trust_level="trusted",
            permissions={"network_level": "none"},
            publisher_slug="acme-ai",
        )
        lock = self._read()
        entry = lock["packages"]["test-pack"]
        assert entry["publisher_slug"] == "acme-ai"

    def test_publisher_slug_stored_in_signatures_publisher(self, monkeypatch):
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package

        sigs = _real_sign_entry("test-pack", {
            "version": "1.0.0",
            "package_type": "toolpack",
            "runtime": "python",
            "entrypoint": "my_pack.tool",
            "artifact_hash": "sha256:abc123def456",
            "tools": [],
            "permissions": {"network_level": "none"},
            "prompts": [],
            "resources": [],
            "connector": None,
            "agent": None,
        })
        install_package(
            slug="test-pack",
            version="1.0.0",
            artifact_url="https://example.com/pkg.tar.gz",
            artifact_hash="sha256:abc123def456",
            entrypoint="my_pack.tool",
            trust_level="trusted",
            permissions={"network_level": "none"},
            signatures=sigs,
            publisher_slug="acme-ai",
        )
        lock = self._read()
        entry = lock["packages"]["test-pack"]
        assert entry["publisher_slug"] == "acme-ai"
        assert entry["_signatures"]["publisher"][0]["publisher_slug"] == "acme-ai"

    def test_unsigned_install_with_publisher_slug_is_v3(self, monkeypatch):
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package

        install_package(
            slug="test-pack",
            version="1.0.0",
            artifact_url="https://example.com/pkg.tar.gz",
            artifact_hash="sha256:abc123def456",
            entrypoint="my_pack.tool",
            trust_level="trusted",
            permissions={"network_level": "none"},
            publisher_slug="acme-ai",
        )
        lock = self._read()
        entry = lock["packages"]["test-pack"]
        assert entry["_integrity"]["canonical_version"] == 3

    def test_canonicalization_strips_and_lowercases(self, monkeypatch):
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package

        install_package(
            slug="test-pack",
            version="1.0.0",
            artifact_url="https://example.com/pkg.tar.gz",
            artifact_hash="sha256:abc123def456",
            entrypoint="my_pack.tool",
            trust_level="trusted",
            permissions={"network_level": "none"},
            publisher_slug="  Acme-AI  ",
        )
        lock = self._read()
        entry = lock["packages"]["test-pack"]
        assert entry["publisher_slug"] == "acme-ai"

    def test_canonicalization_whitespace_only_becomes_none(self, monkeypatch):
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package

        install_package(
            slug="test-pack",
            version="1.0.0",
            artifact_url="https://example.com/pkg.tar.gz",
            artifact_hash="sha256:abc123def456",
            entrypoint="my_pack.tool",
            trust_level="trusted",
            permissions={"network_level": "none"},
            publisher_slug="   ",
        )
        lock = self._read()
        entry = lock["packages"]["test-pack"]
        assert entry["publisher_slug"] is None

    def test_none_publisher_slug_stored(self, monkeypatch):
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package

        install_package(
            slug="test-pack",
            version="1.0.0",
            artifact_url="https://example.com/pkg.tar.gz",
            artifact_hash="sha256:abc123def456",
            entrypoint="my_pack.tool",
            trust_level="trusted",
            permissions={"network_level": "none"},
        )
        lock = self._read()
        entry = lock["packages"]["test-pack"]
        assert entry["publisher_slug"] is None
        assert entry["_integrity"]["canonical_version"] == 1


# ---------------------------------------------------------------------------
# Install-time revocation deny (TG-2)
# ---------------------------------------------------------------------------

class TestInstallerKeyStatusRevocation:
    """Verify install_package() blocks install when key_status is revoked."""

    @pytest.fixture(autouse=True)
    def _tmp_lockfile(self, tmp_path, monkeypatch):
        self.lf = tmp_path / "agentnode.lock"
        monkeypatch.setenv("AGENTNODE_LOCKFILE", str(self.lf))
        self.tmp_path = tmp_path

    def _mock_install(self, monkeypatch):
        from agentnode_sdk import installer
        pkg_dir = self.tmp_path / "extracted" / "pkg"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "my_pack").mkdir(exist_ok=True)
        (pkg_dir / "my_pack" / "tool.py").write_text("def run(): pass")
        monkeypatch.setattr(installer, "download_artifact", lambda *a, **kw: None)
        monkeypatch.setattr(installer, "verify_hash", lambda *a, **kw: "abc123def456")
        monkeypatch.setattr(installer, "extract_archive", lambda *a, **kw: pkg_dir)
        monkeypatch.setattr(installer, "resolve_python", lambda: "python")
        monkeypatch.setattr(installer, "pip_install", lambda *a, **kw: None)

    def test_revoked_key_blocks_install(self, monkeypatch):
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package

        sigs = _real_sign_entry("test-pack", {
            "version": "1.0.0",
            "package_type": "toolpack",
            "runtime": "python",
            "entrypoint": "my_pack.tool",
            "artifact_hash": "sha256:abc123def456",
            "tools": [],
            "permissions": {"network_level": "none"},
            "prompts": [],
            "resources": [],
            "connector": None,
            "agent": None,
        })
        with pytest.raises(RuntimeError, match="revoked"):
            install_package(
                slug="test-pack",
                version="1.0.0",
                artifact_url="https://example.com/pkg.tar.gz",
                artifact_hash="sha256:abc123def456",
                entrypoint="my_pack.tool",
                trust_level="trusted",
                permissions={"network_level": "none"},
                signatures=sigs,
                publisher_slug="acme-ai",
                key_status="revoked",
            )

    def test_active_key_allows_install(self, monkeypatch):
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package

        result = install_package(
            slug="test-pack",
            version="1.0.0",
            artifact_url="https://example.com/pkg.tar.gz",
            artifact_hash="sha256:abc123def456",
            entrypoint="my_pack.tool",
            trust_level="trusted",
            permissions={"network_level": "none"},
            publisher_slug="acme-ai",
            key_status="active",
        )
        assert result["installed"] is True

    def test_none_key_status_allows_install(self, monkeypatch):
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package

        result = install_package(
            slug="test-pack",
            version="1.0.0",
            artifact_url="https://example.com/pkg.tar.gz",
            artifact_hash="sha256:abc123def456",
            entrypoint="my_pack.tool",
            trust_level="trusted",
            permissions={"network_level": "none"},
            publisher_slug="acme-ai",
            key_status=None,
        )
        assert result["installed"] is True

    def test_revoked_key_unsigned_package_not_blocked(self, monkeypatch):
        """Unsigned packages ignore key_status — revocation only applies to signed packages."""
        self._mock_install(monkeypatch)
        from agentnode_sdk.installer import install_package

        result = install_package(
            slug="test-pack",
            version="1.0.0",
            artifact_url="https://example.com/pkg.tar.gz",
            artifact_hash="sha256:abc123def456",
            entrypoint="my_pack.tool",
            trust_level="trusted",
            permissions={"network_level": "none"},
            key_status="revoked",
        )
        assert result["installed"] is True
