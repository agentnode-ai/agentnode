"""Publish starter packs with real artifacts to DB + MinIO.
Run from /opt/agentnode/backend with venv activated."""

import asyncio
import hashlib
import json
import os
import sys

# Ensure we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import engine
from sqlalchemy import text


PACKS = [
    {
        "slug": "pdf-reader-pack",
        "name": "PDF Reader Pack",
        "summary": "Extract text, tables, and metadata from PDF files.",
        "description": "Wraps pdfplumber to provide reliable PDF text and table extraction.",
        "package_type": "toolpack",
        "version": "1.0.0",
        "runtime": "python",
        "entrypoint": "pdf_reader_pack.tool",
        "capabilities": [
            {"name": "extract_pdf_text", "capability_id": "pdf_extraction", "type": "tool"}
        ],
        "frameworks": ["generic"],
        "permissions": {
            "network": "none", "filesystem": "temp",
            "code_execution": "none", "data_access": "input_only",
            "user_approval": "never",
        },
        "artifact_path": "/tmp/pdf-reader-pack.tar.gz",
    },
    {
        "slug": "web-search-pack",
        "name": "Web Search Pack",
        "summary": "Search the web and retrieve structured results for AI agents.",
        "description": "Wraps duckduckgo-search to provide web search capabilities.",
        "package_type": "toolpack",
        "version": "1.0.0",
        "runtime": "python",
        "entrypoint": "web_search_pack.tool",
        "capabilities": [
            {"name": "search_web", "capability_id": "web_search", "type": "tool"}
        ],
        "frameworks": ["generic"],
        "permissions": {
            "network": "restricted", "filesystem": "none",
            "code_execution": "none", "data_access": "input_only",
            "user_approval": "never",
        },
        "artifact_path": "/tmp/web-search-pack.tar.gz",
    },
    {
        "slug": "webpage-extractor-pack",
        "name": "Webpage Extractor Pack",
        "summary": "Extract clean text and metadata from any webpage.",
        "description": "Wraps trafilatura to provide reliable webpage content extraction.",
        "package_type": "toolpack",
        "version": "1.0.0",
        "runtime": "python",
        "entrypoint": "webpage_extractor_pack.tool",
        "capabilities": [
            {"name": "extract_webpage", "capability_id": "webpage_extraction", "type": "tool"}
        ],
        "frameworks": ["generic"],
        "permissions": {
            "network": "unrestricted", "filesystem": "none",
            "code_execution": "none", "data_access": "input_only",
            "user_approval": "never",
        },
        "artifact_path": "/tmp/webpage-extractor-pack.tar.gz",
    },
]


async def publish_all():
    async with engine.begin() as conn:
        # Get publisher ID
        row = await conn.execute(
            text("SELECT id FROM publishers WHERE slug = 'agentnode'")
        )
        publisher_id = row.scalar()
        if not publisher_id:
            print("ERROR: Publisher 'agentnode' not found!")
            return
        print(f"Publisher ID: {publisher_id}")

        # Delete old document-summary-pack
        row = await conn.execute(
            text("SELECT id FROM packages WHERE slug = 'document-summary-pack'")
        )
        old_pkg = row.scalar()
        if old_pkg:
            for tbl in ["capabilities", "permissions", "compatibility_rules",
                        "dependencies", "upgrade_metadata"]:
                await conn.execute(text(
                    f"DELETE FROM {tbl} WHERE package_version_id IN "
                    f"(SELECT id FROM package_versions WHERE package_id = :pid)"
                ), {"pid": old_pkg})
            await conn.execute(
                text("DELETE FROM package_versions WHERE package_id = :pid"),
                {"pid": old_pkg},
            )
            await conn.execute(
                text("DELETE FROM packages WHERE id = :pid"), {"pid": old_pkg}
            )
            print("Deleted old document-summary-pack")

        for pack in PACKS:
            print(f"\n=== Publishing {pack['slug']} ===")

            # Check if package exists
            row = await conn.execute(
                text("SELECT id FROM packages WHERE slug = :slug"),
                {"slug": pack["slug"]},
            )
            pkg_id = row.scalar()

            if pkg_id:
                # Clean existing versions
                for tbl in ["capabilities", "permissions", "compatibility_rules",
                            "dependencies", "upgrade_metadata"]:
                    await conn.execute(text(
                        f"DELETE FROM {tbl} WHERE package_version_id IN "
                        f"(SELECT id FROM package_versions WHERE package_id = :pid)"
                    ), {"pid": pkg_id})
                await conn.execute(
                    text("UPDATE packages SET latest_version_id = NULL WHERE id = :pid"),
                    {"pid": pkg_id},
                )
                await conn.execute(
                    text("DELETE FROM package_versions WHERE package_id = :pid"),
                    {"pid": pkg_id},
                )
                # Update package metadata
                await conn.execute(text(
                    "UPDATE packages SET name=:name, summary=:summary, description=:desc "
                    "WHERE id = :pid"
                ), {
                    "pid": pkg_id, "name": pack["name"],
                    "summary": pack["summary"], "desc": pack["description"],
                })
                print(f"  Cleared and updated existing {pack['slug']}")
            else:
                result = await conn.execute(text(
                    "INSERT INTO packages "
                    "(publisher_id, slug, name, package_type, summary, description) "
                    "VALUES (:pub_id, :slug, :name, :ptype, :summary, :desc) "
                    "RETURNING id"
                ), {
                    "pub_id": publisher_id, "slug": pack["slug"],
                    "name": pack["name"], "ptype": pack["package_type"],
                    "summary": pack["summary"], "desc": pack["description"],
                })
                pkg_id = result.scalar()
                print(f"  Created package (id={pkg_id})")

            # Artifact hash
            with open(pack["artifact_path"], "rb") as f:
                artifact_bytes = f.read()
            artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
            artifact_size = len(artifact_bytes)
            artifact_key = f"artifacts/{pack['slug']}/{pack['version']}/package.tar.gz"

            # Upload to MinIO
            from app.shared.storage import upload_artifact
            await upload_artifact(artifact_key, artifact_bytes)
            print(f"  Uploaded artifact ({artifact_size} bytes)")

            # Read manifest
            manifest_path = f"/opt/agentnode/starter-packs/{pack['slug']}/agentnode.yaml"
            try:
                import yaml
                with open(manifest_path) as f:
                    manifest_raw = yaml.safe_load(f)
            except ImportError:
                manifest_raw = {"package_id": pack["slug"], "version": pack["version"]}

            # Create version
            result = await conn.execute(text(
                "INSERT INTO package_versions "
                "(package_id, version_number, channel, manifest_raw, runtime, "
                "install_mode, hosting_type, entrypoint, "
                "artifact_object_key, artifact_hash_sha256, artifact_size_bytes, "
                "quarantine_status) "
                "VALUES (:pkg_id, :ver, 'stable', :manifest, :runtime, "
                "'package', 'agentnode_hosted', :ep, "
                ":akey, :ahash, :asize, 'cleared') "
                "RETURNING id"
            ), {
                "pkg_id": pkg_id, "ver": pack["version"],
                "manifest": json.dumps(manifest_raw),
                "runtime": pack["runtime"], "ep": pack["entrypoint"],
                "akey": artifact_key, "ahash": artifact_hash,
                "asize": artifact_size,
            })
            version_id = result.scalar()

            # Set latest_version_id
            await conn.execute(text(
                "UPDATE packages SET latest_version_id = :vid WHERE id = :pid"
            ), {"vid": version_id, "pid": pkg_id})

            # Capabilities
            for cap in pack["capabilities"]:
                await conn.execute(text(
                    "INSERT INTO capabilities "
                    "(package_version_id, capability_type, capability_id, name, description) "
                    "VALUES (:vid, :ctype, :cid, :name, :desc)"
                ), {
                    "vid": version_id, "ctype": cap["type"],
                    "cid": cap["capability_id"], "name": cap["name"],
                    "desc": cap["name"],
                })

            # Permissions
            p = pack["permissions"]
            await conn.execute(text(
                "INSERT INTO permissions "
                "(package_version_id, network_level, filesystem_level, "
                "code_execution_level, data_access_level, user_approval_level) "
                "VALUES (:vid, :net, :fs, :exec, :data, :approval)"
            ), {
                "vid": version_id, "net": p["network"], "fs": p["filesystem"],
                "exec": p["code_execution"], "data": p["data_access"],
                "approval": p["user_approval"],
            })

            # Compatibility rules
            for fw in pack["frameworks"]:
                await conn.execute(text(
                    "INSERT INTO compatibility_rules "
                    "(package_version_id, framework, runtime_version) "
                    "VALUES (:vid, :fw, '>=3.10')"
                ), {"vid": version_id, "fw": fw})

            print(f"  Published {pack['slug']}@{pack['version']} with artifact")

    await engine.dispose()
    print("\n=== All packs published ===")


if __name__ == "__main__":
    asyncio.run(publish_all())
