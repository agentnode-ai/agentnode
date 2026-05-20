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
    CANONICAL_VERSION,
    MUTABLE_FIELDS,
    PERMISSION_ESCALATIONS,
    SENSITIVE_FIELDS,
    IntegrityResult,
    SensitiveChange,
    _build_canonical,
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
        assert result["canonical_version"] == CANONICAL_VERSION
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
        assert sealed["_integrity"]["canonical_version"] == CANONICAL_VERSION
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

    KNOWN_FIELDS = set(CANONICAL_FIELDS) | set(MUTABLE_FIELDS) | {"_integrity"}

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
            entrypoint="my_pack.tool",
            trust_level="trusted",
        )

        lock = self._read()
        hash_v1 = lock["packages"]["upgrade-pack"]["_integrity"]["hash"]

        monkeypatch.setattr(installer, "verify_hash", lambda *a, **kw: "v2hash")
        installer.install_package(
            slug="upgrade-pack", version="2.0.0",
            artifact_url="https://example.com/pkg2.tar.gz",
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
