#!/usr/bin/env python3
"""Build .tar.gz artifacts for all starter packs.

For each pack, creates build/artifacts/{slug}.tar.gz containing:
- agentnode.yaml
- pyproject.toml
- src/
- tests/
"""
import io
import sys
import tarfile
from pathlib import Path

STARTER_PACKS_DIR = Path(__file__).resolve().parent.parent / "starter-packs"
BUILD_DIR = Path(__file__).resolve().parent.parent / "build" / "artifacts"


def build_artifact(pack_dir: Path, output_path: Path) -> bool:
    """Build a .tar.gz artifact for a single pack. Returns True on success."""
    slug = pack_dir.name
    include_dirs = {"src", "tests", "fixtures"}
    include_files = {"agentnode.yaml", "pyproject.toml"}

    try:
        with tarfile.open(output_path, "w:gz") as tar:
            for name in include_files:
                fpath = pack_dir / name
                if fpath.exists():
                    tar.add(fpath, arcname=f"{slug}/{name}")

            for dname in include_dirs:
                dpath = pack_dir / dname
                if dpath.is_dir():
                    for item in sorted(dpath.rglob("*")):
                        if item.is_file() and "__pycache__" not in str(item):
                            arcname = f"{slug}/{item.relative_to(pack_dir)}"
                            tar.add(item, arcname=arcname)

        # Validate: must have tests
        with tarfile.open(output_path, "r:gz") as tar:
            names = tar.getnames()
            has_tests = any("tests/" in n and n.endswith(".py") and "__init__" not in n for n in names)
            has_pyproject = any(n.endswith("pyproject.toml") for n in names)
            if not has_tests:
                print(f"  WARN  {slug}: artifact missing test files")
            if not has_pyproject:
                print(f"  WARN  {slug}: artifact missing pyproject.toml")

        return True
    except Exception as e:
        print(f"  ERROR {slug}: {e}")
        return False


def main():
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    packs = sorted(d for d in STARTER_PACKS_DIR.iterdir() if d.is_dir() and (d / "agentnode.yaml").exists())

    built = 0
    failed = 0

    for pack_dir in packs:
        slug = pack_dir.name
        output = BUILD_DIR / f"{slug}.tar.gz"
        if build_artifact(pack_dir, output):
            size_kb = output.stat().st_size / 1024
            print(f"  BUILT {slug}.tar.gz ({size_kb:.1f} KB)")
            built += 1
        else:
            failed += 1

    print(f"\nDone: {built} built, {failed} failed")
    print(f"Artifacts in: {BUILD_DIR}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
