"""Build registry-shaped inputs for pack-import tests with the PRODUCT's own code paths.

* the artifact is built by ``cli.publish._build_artifact`` (the publish path: a ``.tar.gz``
  whose members live under ``<package_id>/``);
* the lock entry has exactly the shape ``installer.install_package`` writes for a python
  toolpack (``installer.py`` lock_entry, with permissions flattened as ``client.
  _permissions_to_dict`` does) and is sealed with ``lock_integrity.seal_entry``.

No registry is contacted: the live registry is out of bounds for this slice (stated limitation).
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

import yaml
from agentnode_sdk import lock_integrity
from agentnode_sdk.cli.publish import _build_artifact

REPO = Path(__file__).resolve().parents[3]
STARTER = REPO / "starter-packs"
FIXTURE_PACKS = Path(__file__).parent / "fixtures" / "packs"


def build_artifact(pack_dir: Path, package_id: str | None = None) -> bytes:
    manifest = yaml.safe_load((pack_dir / "agentnode.yaml").read_text(encoding="utf-8"))
    data, _count = _build_artifact(pack_dir, package_id or manifest["package_id"])
    return data


def flat_permissions(manifest: dict[str, Any]) -> dict[str, Any]:
    """As the registry serves them: a 0.2 manifest is normalised with the backend defaults first."""
    from agentnode_sdk.capability.pack_import import _normalize

    perms = _normalize(manifest).get("permissions") or {}
    net = perms.get("network") or {}
    return {
        "network_level": net.get("level"),
        "filesystem_level": (perms.get("filesystem") or {}).get("level"),
        "code_execution_level": (perms.get("code_execution") or {}).get("level"),
        "data_access_level": (perms.get("data_access") or {}).get("level"),
        "user_approval_level": (perms.get("user_approval") or {}).get("required"),
        "allowed_domains": list(net.get("allowed_domains") or []),
    }


def lock_entry_for(pack_dir: Path, artifact: bytes, **overrides: Any) -> dict[str, Any]:
    manifest = yaml.safe_load((pack_dir / "agentnode.yaml").read_text(encoding="utf-8"))
    tools = (manifest.get("capabilities") or {}).get("tools") or []
    entry: dict[str, Any] = {
        "version": manifest["version"],
        "package_type": manifest["package_type"],
        "runtime": manifest.get("runtime", "python"),
        "entrypoint": manifest.get("entrypoint") or "",
        "capability_ids": [t.get("capability_id") for t in tools],
        "tools": [
            {"name": t["name"], "entrypoint": t["entrypoint"]}
            for t in tools
            if t.get("entrypoint")
        ],
        "artifact_hash": "sha256:" + hashlib.sha256(artifact).hexdigest(),
        "installed_at": "2026-10-17T00:00:00+00:00",
        "source": "sdk",
        "trust_level": "verified",
        "last_trust_check": "2026-10-17T00:00:00+00:00",
        "permissions": flat_permissions(manifest),
        "prompts": [],
        "resources": [],
        "connector": manifest.get("connector"),
        "agent": None,
        "publisher_slug": manifest.get("publisher"),
    }
    if manifest.get("env_requirements"):
        entry["env_requirements"] = manifest["env_requirements"]
    entry.update(overrides)
    return lock_integrity.seal_entry(entry)


def copy_pack(src: Path, dest: Path) -> Path:
    """A writable copy of a pack directory (for tests that change one byte or one field)."""
    shutil.copytree(src, dest)
    return dest
