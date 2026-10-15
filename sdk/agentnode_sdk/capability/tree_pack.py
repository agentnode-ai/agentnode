"""Deterministic packing + content digest of a multi-file server tree (Slice 2, mechanism b).

Slice 1 delivered a single-file stdlib server as one blob. A REAL existing MCP server
(e.g. an npm package plus its resolved dependency closure) is a multi-file tree. This
module packs such a tree into a deterministic tar and binds it with a content tree-digest,
so the capability manifest can pin EVERY delivered file and the in-sandbox driver can
re-verify the delivered bytes before running.

The tree-digest scheme matches the frozen sandbox's install-tree hash
(``sandbox/mcp_preinstall._MCP_HASH_PY``): per entry a JSON record binding the relative
path + type (f/d/l) + octal mode + symlink target + file-content sha256; globally sorted;
length-prefixed; final sha256. mtime/owner are excluded (non-deterministic). Modes are
normalised so the digest is reproducible across hosts: dirs 0o755, files 0o755 if any
execute bit else 0o644.

Nothing here reaches out: pure stdlib, no network, no AgentNode import. The packer is used
at import time; ``safe_extract`` + ``tree_digest_of_dir`` are embedded into the per-run
driver and run INSIDE the worker's sandbox.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tarfile


class TreePackError(Exception):
    """A tree that cannot be packed or safely extracted (e.g. an escaping symlink)."""


def _norm_mode(mode: int, is_dir: bool) -> int:
    if is_dir:
        return 0o755
    return 0o755 if (mode & 0o111) else 0o644


def _rel(root: str, path: str) -> str:
    # POSIX-style relative path, the stable key on every platform.
    return os.path.relpath(path, root).replace(os.sep, "/")


def _iter_entries(root: str):
    """Yield (relpath, kind, mode, link_target, abspath) for every entry under *root*, sorted.

    kind is 'd', 'f' or 'l'. The root itself is not emitted. Deterministic order.
    """
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(dirnames + filenames):
            ap = os.path.join(dirpath, name)
            rel = _rel(root, ap)
            st = os.lstat(ap)
            mode = stat.S_IMODE(st.st_mode)
            if stat.S_ISLNK(st.st_mode):
                target = os.readlink(ap)
                _assert_link_within(root, ap, target)
                out.append((rel, "l", 0o777, target, ap))
            elif stat.S_ISDIR(st.st_mode):
                out.append((rel, "d", _norm_mode(mode, True), None, ap))
            elif stat.S_ISREG(st.st_mode):
                out.append((rel, "f", _norm_mode(mode, False), None, ap))
            else:
                raise TreePackError(f"unsupported file type in tree: {rel!r}")
    out.sort(key=lambda r: r[0])
    return out


def _assert_link_within(root: str, link_path: str, target: str) -> None:
    if os.path.isabs(target):
        raise TreePackError(f"absolute symlink not allowed: {target!r}")
    resolved = os.path.normpath(os.path.join(os.path.dirname(link_path), target))
    root_abs = os.path.normpath(root)
    if os.path.commonpath([root_abs, resolved]) != root_abs:
        raise TreePackError(f"symlink escapes the tree root: {target!r}")


def _records(root: str) -> list[dict]:
    records = []
    for rel, kind, mode, target, ap in _iter_entries(root):
        rec = {"p": rel, "t": kind, "m": oct(mode)}
        if kind == "l":
            rec["link"] = target
        elif kind == "f":
            h = hashlib.sha256()
            with open(ap, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
            rec["sha"] = h.hexdigest()
        records.append(rec)
    return records


def tree_digest_of_dir(root: str | os.PathLike[str]) -> str:
    """``sha256:<hex>`` over the canonical, sorted, length-prefixed records of *root*."""
    h = hashlib.sha256()
    for rec in _records(str(root)):
        line = json.dumps(rec, sort_keys=True, separators=(",", ":")).encode("utf-8")
        h.update(len(line).to_bytes(8, "big"))
        h.update(line)
    return "sha256:" + h.hexdigest()


def pack_tree(root: str | os.PathLike[str]) -> bytes:
    """A deterministic, reproducible uncompressed tar of *root* (sorted, mtime 0, uid/gid 0).

    Members carry normalised modes so the archive is byte-identical for identical content.
    Only directories, regular files and within-tree relative symlinks are included.
    """
    root = str(root)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for rel, kind, mode, target, ap in _iter_entries(root):
            ti = tarfile.TarInfo(name=rel)
            ti.mtime = 0
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            ti.mode = mode
            if kind == "d":
                ti.type = tarfile.DIRTYPE
                tar.addfile(ti)
            elif kind == "l":
                ti.type = tarfile.SYMTYPE
                ti.linkname = target
                tar.addfile(ti)
            else:
                with open(ap, "rb") as fh:
                    data = fh.read()
                ti.size = len(data)
                tar.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def safe_extract(tar_bytes: bytes, dest: str | os.PathLike[str]) -> None:
    """Extract *tar_bytes* under *dest*, refusing any member that escapes *dest*.

    Guards against absolute paths, ``..`` traversal and symlinks whose target would
    leave *dest*. Used inside the sandbox on the delivered artifact.
    """
    dest = os.path.abspath(str(dest))
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tar:
        members = tar.getmembers()
        for m in members:
            target = os.path.abspath(os.path.join(dest, m.name))
            if os.path.commonpath([dest, target]) != dest:
                raise TreePackError(f"tar member escapes dest: {m.name!r}")
            if m.issym() or m.islnk():
                link_abs = os.path.abspath(os.path.join(os.path.dirname(target), m.linkname))
                if os.path.commonpath([dest, link_abs]) != dest:
                    raise TreePackError(f"tar link escapes dest: {m.name!r} -> {m.linkname!r}")
        for m in members:
            tar.extract(m, dest)


def tree_digest_of_tar(tar_bytes: bytes) -> str:
    """The tree-digest of what *tar_bytes* would extract to (via a temp dir)."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        safe_extract(tar_bytes, d)
        return tree_digest_of_dir(d)
