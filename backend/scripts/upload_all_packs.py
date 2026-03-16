"""Build tar.gz artifacts for all starter-packs and upload to S3/MinIO."""
import asyncio
import hashlib
import io
import os
import sys
import tarfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import engine
from app.shared.storage import upload_artifact
from sqlalchemy import text

STARTER_PACKS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "starter-packs",
)


def build_tarball(pack_dir: str) -> bytes:
    """Build a tar.gz from a pack directory."""
    buf = io.BytesIO()
    pack_name = os.path.basename(pack_dir)
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, dirs, files in os.walk(pack_dir):
            # Skip __pycache__, .egg-info, etc.
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".egg-info", "dist", "build", ".git")]
            for fname in files:
                if fname.endswith((".pyc", ".pyo")):
                    continue
                full_path = os.path.join(root, fname)
                arcname = os.path.join(pack_name, os.path.relpath(full_path, pack_dir))
                tar.add(full_path, arcname=arcname)
    return buf.getvalue()


async def main():
    if not os.path.isdir(STARTER_PACKS_DIR):
        print(f"ERROR: starter-packs dir not found: {STARTER_PACKS_DIR}")
        return

    pack_dirs = sorted([
        d for d in os.listdir(STARTER_PACKS_DIR)
        if os.path.isdir(os.path.join(STARTER_PACKS_DIR, d))
    ])

    print(f"Found {len(pack_dirs)} packs in {STARTER_PACKS_DIR}\n")

    async with engine.begin() as conn:
        uploaded = 0
        skipped = 0

        for pack_slug in pack_dirs:
            pack_path = os.path.join(STARTER_PACKS_DIR, pack_slug)

            # Check if package exists in DB
            result = await conn.execute(
                text("SELECT pv.id, pv.artifact_object_key FROM packages p "
                     "JOIN package_versions pv ON pv.id = p.latest_version_id "
                     "WHERE p.slug = :slug"),
                {"slug": pack_slug},
            )
            row = result.first()
            if not row:
                print(f"SKIP {pack_slug} (not in DB)")
                skipped += 1
                continue

            version_id = row[0]
            existing_key = row[1]

            if existing_key:
                print(f"SKIP {pack_slug} (artifact exists: {existing_key})")
                skipped += 1
                continue

            # Build tarball
            tarball = build_tarball(pack_path)
            sha256 = hashlib.sha256(tarball).hexdigest()
            size = len(tarball)
            s3_key = f"artifacts/{pack_slug}/1.0.0/package.tar.gz"

            # Upload to S3
            try:
                upload_artifact(s3_key, tarball)
            except Exception as e:
                print(f"FAIL {pack_slug}: S3 upload error: {e}")
                continue

            # Update DB
            await conn.execute(
                text("UPDATE package_versions SET "
                     "artifact_object_key = :key, "
                     "artifact_hash_sha256 = :hash, "
                     "artifact_size_bytes = :size "
                     "WHERE id = :vid"),
                {"key": s3_key, "hash": sha256, "size": size, "vid": version_id},
            )

            uploaded += 1
            print(f"OK {pack_slug} ({size:,} bytes, sha256={sha256[:16]}...)")

    await engine.dispose()
    print(f"\nDone! Uploaded: {uploaded}, Skipped: {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
