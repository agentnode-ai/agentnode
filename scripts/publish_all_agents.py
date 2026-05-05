"""Publish all agent starter-packs to the AgentNode registry."""
import json
import os
import sys
import tarfile
import tempfile
import time

import httpx
import yaml

API_BASE = "https://api.agentnode.net"
PUBLISH_URL = f"{API_BASE}/v1/packages/publish"
BASE = os.path.join(os.path.dirname(__file__), "..", "starter-packs")
TOKEN = os.environ.get("AUTH_TOKEN", "")

if not TOKEN:
    print("ERROR: Set AUTH_TOKEN environment variable")
    sys.exit(1)

published = 0
skipped = 0
failed = 0
failures = []

def do_publish(slug, manifest, tar_path):
    manifest_json = json.dumps(manifest)
    with open(tar_path, "rb") as af:
        return httpx.post(
            PUBLISH_URL,
            headers={"Authorization": f"Bearer {TOKEN}"},
            data={"manifest": manifest_json},
            files={"artifact": (f"{slug}.tar.gz", af, "application/gzip")},
            timeout=120,
        )

for d in sorted(os.listdir(BASE)):
    manifest_path = os.path.join(BASE, d, "agentnode.yaml")
    if not os.path.isfile(manifest_path):
        continue
    with open(manifest_path) as f:
        manifest = yaml.safe_load(f)
    if manifest.get("package_type") != "agent":
        continue

    slug = manifest.get("package_id", d)
    version = manifest.get("version", "?")
    print(f"[{slug}] v{version}...", end=" ", flush=True)

    pack_dir = os.path.join(BASE, d)
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tar_path = tmp.name
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(pack_dir, arcname=d,
                filter=lambda ti: None if any(x in ti.name for x in [
                    "__pycache__", ".pyc", ".git", ".venv", ".egg-info",
                    ".pytest_cache", ".DS_Store", ".env"
                ]) else ti)

    try:
        resp = do_publish(slug, manifest, tar_path)
        if resp.status_code == 201:
            print("OK")
            published += 1
        elif resp.status_code == 409:
            print("SKIP (exists)")
            skipped += 1
        elif resp.status_code == 429:
            print("rate limited...", end=" ", flush=True)
            time.sleep(60)
            resp = do_publish(slug, manifest, tar_path)
            if resp.status_code == 201:
                print("OK (retry)")
                published += 1
            else:
                print(f"FAIL {resp.status_code}")
                failed += 1
                failures.append(slug)
        else:
            try:
                msg = resp.json().get("error", {}).get("message", resp.text[:100])
            except Exception:
                msg = resp.text[:100]
            print(f"FAIL {resp.status_code}: {msg}")
            failed += 1
            failures.append(slug)
    except Exception as exc:
        print(f"FAIL: {exc}")
        failed += 1
        failures.append(slug)
    finally:
        os.unlink(tar_path)
    time.sleep(5)

print(f"\nPublished: {published}  Skipped: {skipped}  Failed: {failed}")
if failures:
    print(f"Failures: {failures}")
