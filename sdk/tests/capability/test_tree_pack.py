"""Deterministic tree packing + content digest + safe extraction (Slice 2, mechanism b)."""
from __future__ import annotations

import os

import pytest
from agentnode_sdk.capability import tree_pack


def _make_tree(root):
    os.makedirs(os.path.join(root, "pkg", "sub"))
    with open(os.path.join(root, "pkg", "a.txt"), "w") as f:
        f.write("alpha")
    with open(os.path.join(root, "pkg", "sub", "b.txt"), "w") as f:
        f.write("beta")
    return root


def test_pack_is_deterministic_and_digest_stable(tmp_path):
    a = _make_tree(str(tmp_path / "a"))
    b = _make_tree(str(tmp_path / "b"))
    assert tree_pack.pack_tree(a) == tree_pack.pack_tree(b)
    assert tree_pack.tree_digest_of_dir(a) == tree_pack.tree_digest_of_dir(b)
    assert tree_pack.tree_digest_of_dir(a).startswith("sha256:")


def test_round_trip_preserves_digest(tmp_path):
    root = _make_tree(str(tmp_path / "src"))
    tar = tree_pack.pack_tree(root)
    assert tree_pack.tree_digest_of_tar(tar) == tree_pack.tree_digest_of_dir(root)
    dest = str(tmp_path / "out")
    tree_pack.safe_extract(tar, dest)
    assert tree_pack.tree_digest_of_dir(dest) == tree_pack.tree_digest_of_dir(root)
    assert open(os.path.join(dest, "pkg", "a.txt")).read() == "alpha"


def test_content_change_changes_digest(tmp_path):
    root = _make_tree(str(tmp_path / "src"))
    d1 = tree_pack.tree_digest_of_dir(root)
    with open(os.path.join(root, "pkg", "a.txt"), "w") as f:
        f.write("ALPHA")  # one byte of content differs
    assert tree_pack.tree_digest_of_dir(root) != d1


def test_within_tree_symlink_round_trips(tmp_path):
    if os.name == "nt":
        pytest.skip("symlink creation is privileged on Windows")
    root = _make_tree(str(tmp_path / "src"))
    os.symlink(os.path.join("pkg", "a.txt"), os.path.join(root, "link"))
    tar = tree_pack.pack_tree(root)
    dest = str(tmp_path / "out")
    tree_pack.safe_extract(tar, dest)
    assert tree_pack.tree_digest_of_dir(dest) == tree_pack.tree_digest_of_dir(root)


def test_escaping_symlink_is_refused(tmp_path):
    if os.name == "nt":
        pytest.skip("symlink creation is privileged on Windows")
    root = _make_tree(str(tmp_path / "src"))
    os.symlink(os.path.join("..", "..", "etc"), os.path.join(root, "escape"))
    with pytest.raises(tree_pack.TreePackError):
        tree_pack.pack_tree(root)


def test_safe_extract_refuses_traversal(tmp_path):
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        ti = tarfile.TarInfo(name="../escape.txt")
        data = b"x"
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))
    with pytest.raises(tree_pack.TreePackError):
        tree_pack.safe_extract(buf.getvalue(), str(tmp_path / "dest"))
