"""Patch manifest_raw in DB with permissions + connector from local YAML files.

Merges only the 'permissions' and 'connector' sections — does not overwrite
anything else in manifest_raw.

Usage:
    python -m scripts.patch_manifest_permissions --dry-run
    python -m scripts.patch_manifest_permissions
"""
import asyncio
import copy
import json
import os
import sys

import yaml
from sqlalchemy import select
from sqlalchemy.orm import selectinload

import app.auth.models  # noqa: F401
import app.publishers.models  # noqa: F401
import app.packages.models  # noqa: F401
import app.blog.models  # noqa: F401
import app.verification.models  # noqa: F401

from app.database import async_session_factory
from app.packages.models import Package

STARTER_PACKS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "starter-packs")


def load_yaml(slug: str) -> dict | None:
    path = os.path.join(STARTER_PACKS_DIR, slug, "agentnode.yaml")
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return yaml.safe_load(f)


async def patch(dry_run: bool = False):
    async with async_session_factory() as session:
        result = await session.execute(
            select(Package).options(selectinload(Package.versions))
        )
        packages = result.scalars().all()

        patched = 0
        skipped = 0
        not_found = 0

        for pkg in packages:
            version = None
            if pkg.latest_version_id:
                for v in pkg.versions:
                    if v.id == pkg.latest_version_id:
                        version = v
                        break
            if not version and pkg.versions:
                version = pkg.versions[0]
            if not version:
                print(f"  SKIP {pkg.slug}: no version")
                skipped += 1
                continue

            local = load_yaml(pkg.slug)
            if not local:
                print(f"  SKIP {pkg.slug}: no local YAML found")
                not_found += 1
                continue

            local_perms = local.get("permissions")
            local_connector = local.get("connector")

            if not local_perms and not local_connector:
                print(f"  SKIP {pkg.slug}: no permissions or connector in local YAML")
                skipped += 1
                continue

            manifest = copy.deepcopy(version.manifest_raw or {})
            existing_perms = manifest.get("permissions")
            existing_connector = manifest.get("connector")
            changes = []

            if local_perms and local_perms != existing_perms:
                changes.append("permissions")
            if local_connector and local_connector != existing_connector:
                changes.append("connector")

            if not changes:
                print(f"  OK   {pkg.slug}: already up to date")
                skipped += 1
                continue

            print(f"  PATCH {pkg.slug}: {', '.join(changes)}")
            if "permissions" in changes:
                old_net = (existing_perms or {}).get("network", {}).get("level") if existing_perms else None
                new_net = (local_perms or {}).get("network", {}).get("level")
                old_fs = (existing_perms or {}).get("filesystem", {}).get("level") if existing_perms else None
                new_fs = (local_perms or {}).get("filesystem", {}).get("level")
                old_exec = (existing_perms or {}).get("code_execution", {}).get("level") if existing_perms else None
                new_exec = (local_perms or {}).get("code_execution", {}).get("level")
                print(f"         network: {old_net} -> {new_net}")
                print(f"         filesystem: {old_fs} -> {new_fs}")
                print(f"         code_execution: {old_exec} -> {new_exec}")
            if "connector" in changes:
                old_prov = existing_connector.get("provider") if existing_connector else None
                new_prov = local_connector.get("provider") if local_connector else None
                print(f"         connector: {old_prov} -> {new_prov} ({local_connector.get('auth_type', '?')})")

            if not dry_run:
                if local_perms:
                    manifest["permissions"] = local_perms
                if local_connector:
                    manifest["connector"] = local_connector
                version.manifest_raw = manifest
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(version, "manifest_raw")

            patched += 1

        if not dry_run:
            await session.commit()

        mode = "Would patch" if dry_run else "Patched"
        print(f"\n{mode} {patched} packages ({skipped} skipped, {not_found} not found in starter-packs)")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("=== DRY RUN ===\n")
    asyncio.run(patch(dry_run))
