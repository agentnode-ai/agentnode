"""Vendor a pinned npm MCP server + its dependency closure into a local tree (Slice 2).

Build-time only. Runs the SAME pinned sandbox image the capability executes in, installs the
exact ``package@version`` with the image's own ``npm`` (build-time network, like the frozen
sandbox's own MCP pre-install build), and extracts the resulting ``/install`` tree locally so
``npm_import.import_vendored_tree`` can pack + pin it deterministically. Nothing is installed
on the host; no host node/npm is needed; the RUN path never has network.

This is a convenience/orchestration helper, not part of the capability core execution path:
the core only ever runs the already-pinned tree over the Worker/Job path.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentnode_sdk.sandbox.container_backend import _BASE_IMAGE

_INSTALL_SH = (
    "set -e; export HOME=/tmp/home; mkdir -p \"$HOME\" /tmp/npm-cache; "
    "export npm_config_cache=/tmp/npm-cache; "
    "npm install -g --prefix /install '{pkg}@{ver}' 1>&2; "
    "tar -C /install -cf - ."
)
_META_SH = (
    "set -e; export HOME=/tmp/home; mkdir -p \"$HOME\" /tmp/npm-cache; "
    "export npm_config_cache=/tmp/npm-cache; "
    "npm view '{pkg}@{ver}' dist.integrity dist.tarball version 2>/dev/null"
)


@dataclass(frozen=True)
class VendoredTree:
    tree_dir: str
    entry: str
    interpreter: str
    registry: dict[str, Any]


def _run_image(runtime: str, image: str, script: str) -> bytes:
    argv = [runtime, "run", "--rm", image, "sh", "-c", script]
    done = subprocess.run(argv, capture_output=True, timeout=600, check=False)
    if done.returncode != 0:
        raise RuntimeError(
            f"image command failed (exit {done.returncode}): {done.stderr.decode('utf-8', 'replace')[:2000]}")
    return done.stdout


def _resolve_entry(install_root: Path, package: str) -> str:
    """The server entry file RELATIVE to the install root, from the installed package.json."""
    pkg_dir = install_root / "lib" / "node_modules" / package
    pj = pkg_dir / "package.json"
    meta = json.loads(pj.read_text(encoding="utf-8"))
    binv = meta.get("bin")
    rel_file: str | None = None
    if isinstance(binv, str):
        rel_file = binv
    elif isinstance(binv, dict) and binv:
        rel_file = next(iter(binv.values()))
    if not rel_file:
        rel_file = meta.get("main") or "index.js"
    entry_abs = (pkg_dir / rel_file).resolve()
    return os.path.relpath(entry_abs, install_root.resolve()).replace(os.sep, "/")


def vendor_npm(package: str, version: str, dest_dir: str | os.PathLike[str], *,
               runtime: str = "podman", image: str = _BASE_IMAGE,
               source_url: str = "", license_: str = "", publisher: str = "") -> VendoredTree:
    """Install ``package@version`` in the pinned image and extract its /install tree into *dest_dir*."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    tar_bytes = _run_image(runtime, image, _INSTALL_SH.format(pkg=package, ver=version))
    dest_abs = os.path.abspath(str(dest))
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tar:
        # trusted: produced by our own install in the pinned image; still extract under dest only.
        for m in tar.getmembers():
            target = os.path.abspath(os.path.join(dest_abs, m.name))
            if os.path.commonpath([dest_abs, target]) != dest_abs:
                raise RuntimeError(f"refusing tar member escaping dest: {m.name!r}")
        tar.extractall(dest_abs)

    entry = _resolve_entry(dest, package)

    integrity = tarball = resolved_version = ""
    try:
        meta = _run_image(runtime, image, _META_SH.format(pkg=package, ver=version)).decode(
            "utf-8", "replace").split()
        if meta:
            integrity = meta[0] if len(meta) > 0 else ""
            tarball = meta[1] if len(meta) > 1 else ""
            resolved_version = meta[2] if len(meta) > 2 else ""
    except Exception:  # noqa: BLE001 -- metadata is provenance-nice-to-have, not required to pin
        pass

    registry = {
        "manager": "npm",
        "name": package,
        "version": version,
        "resolved_version": resolved_version or version,
        "dist_integrity": integrity,
        "dist_tarball": tarball,
        "source_url": source_url,
        "license": license_,
        "upstream_publisher": publisher,
        "image": image,
    }
    return VendoredTree(tree_dir=str(dest), entry=entry, interpreter="node", registry=registry)
