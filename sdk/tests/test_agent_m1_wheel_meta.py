"""A1-M1: import-free wheel build + distribution-identity validation."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from agentnode_sdk import _wheel_meta as wm
from tests.agent_m1_helpers import make_raw_zip, make_wheel, pip_python


# ---- canonicalization (§4) ----

def test_canonical_dist_name_pep503():
    assert wm.canonical_dist_name("Foo_Bar.Baz") == "foo-bar-baz"
    assert wm.canonical_dist_name("academic-research-agent") == "academic-research-agent"


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_canonical_dist_name_rejects_empty(bad):
    with pytest.raises(wm.WheelValidationError):
        wm.canonical_dist_name(bad)


def test_canonical_version_canonicalizes_and_rejects():
    assert wm.canonical_dist_version("2.0.0") == "2.0.0"
    assert wm.canonical_dist_version("1.0") == "1.0"
    with pytest.raises(wm.WheelValidationError):
        wm.canonical_dist_version("not-a-version!!")


def test_top_level_from_entrypoint():
    assert wm.top_level_from_entrypoint("pkg.sub.agent:run") == "pkg"
    assert wm.top_level_from_entrypoint("mod:run") == "mod"
    for bad in ("", ":run", None):
        with pytest.raises(wm.WheelValidationError):
            wm.top_level_from_entrypoint(bad)


# ---- import-free validation (§6) using synthetic wheels ----

def test_validate_regular_package(tmp_path):
    w = make_wheel(tmp_path / "w.whl", name="foo-agent", version="2.0.0",
                   payload={"foo_agent/__init__.py": '""" """\n', "foo_agent/agent.py": "def run():...\n"},
                   top_level_txt="foo_agent\n")
    ident = wm.validate_wheel(w, "foo_agent")
    assert ident.distribution == "foo-agent"
    assert ident.version == "2.0.0"
    assert "foo_agent" in ident.top_levels


def test_validate_top_level_module(tmp_path):
    w = make_wheel(tmp_path / "w.whl", name="mod-agent", version="1.0.0",
                   payload={"modagent.py": "def run():...\n"})
    ident = wm.validate_wheel(w, "modagent")
    assert ident.distribution == "mod-agent"


def test_namespace_package_fail_closed(tmp_path):
    # directory present but NO __init__.py → PEP 420 namespace → refuse
    w = make_wheel(tmp_path / "w.whl", name="ns-agent", version="1.0.0",
                   payload={"nsagent/thing.py": "x=1\n"})
    with pytest.raises(wm.WheelValidationError) as ei:
        wm.validate_wheel(w, "nsagent")
    assert ei.value.code == "entrypoint_namespace"


def test_extension_module_fail_closed(tmp_path):
    w = make_wheel(tmp_path / "w.whl", name="ext-agent", version="1.0.0",
                   payload={"extagent/__init__.py": "", "extagent/_speed.pyd": "\0"})
    with pytest.raises(wm.WheelValidationError) as ei:
        wm.validate_wheel(w, "extagent")
    assert ei.value.code == "unsupported_layout"


def test_ambiguous_two_dist_info_fail_closed(tmp_path):
    w = make_raw_zip(tmp_path / "w.whl", {
        "a-1.0.dist-info/METADATA": "Name: a\nVersion: 1.0\n",
        "a-1.0.dist-info/RECORD": "",
        "b-1.0.dist-info/METADATA": "Name: b\nVersion: 1.0\n",
        "b-1.0.dist-info/RECORD": "",
        "a/__init__.py": "",
    })
    with pytest.raises(wm.WheelValidationError) as ei:
        wm.validate_wheel(w, "a")
    assert ei.value.code == "metadata_ambiguous"


def test_missing_record_fail_closed(tmp_path):
    w = make_wheel(tmp_path / "w.whl", name="no-rec", version="1.0.0",
                   payload={"norec/__init__.py": ""}, include_record=False)
    with pytest.raises(wm.WheelValidationError) as ei:
        wm.validate_wheel(w, "norec")
    assert ei.value.code == "metadata_missing"


def test_incoherent_dist_info_name_fail_closed(tmp_path):
    # dist-info dir says "other", METADATA says "foo-agent" → incoherent identity
    w = make_wheel(tmp_path / "w.whl", name="foo-agent", version="2.0.0",
                   dist_info_name="other", payload={"foo_agent/__init__.py": ""})
    with pytest.raises(wm.WheelValidationError) as ei:
        wm.validate_wheel(w, "foo_agent")
    assert ei.value.code == "metadata_ambiguous"


def test_unsafe_absolute_member_fail_closed(tmp_path):
    w = make_raw_zip(tmp_path / "w.whl", {
        "foo-1.0.dist-info/METADATA": "Name: foo\nVersion: 1.0\n",
        "foo-1.0.dist-info/RECORD": "",
        "/etc/evil": "x",
    })
    with pytest.raises(wm.WheelValidationError) as ei:
        wm.validate_wheel(w, "foo")
    assert ei.value.code == "unsupported_layout"


def test_unsafe_parent_escape_member_fail_closed(tmp_path):
    w = make_raw_zip(tmp_path / "w.whl", {
        "foo-1.0.dist-info/METADATA": "Name: foo\nVersion: 1.0\n",
        "foo-1.0.dist-info/RECORD": "",
        "../escape.py": "x",
    })
    with pytest.raises(wm.WheelValidationError) as ei:
        wm.validate_wheel(w, "foo")
    assert ei.value.code == "unsupported_layout"


def test_entrypoint_top_level_must_be_provided(tmp_path):
    # wheel provides "foo_agent" but the entrypoint expects "missing"
    w = make_wheel(tmp_path / "w.whl", name="foo-agent", version="2.0.0",
                   payload={"foo_agent/__init__.py": ""})
    with pytest.raises(wm.WheelValidationError) as ei:
        wm.validate_wheel(w, "missing")
    assert ei.value.code == "entrypoint_namespace"


def test_consistency_hash_changes_with_bytes(tmp_path):
    w1 = make_wheel(tmp_path / "a.whl", name="c", version="1.0.0", payload={"c/__init__.py": "a"})
    w2 = make_wheel(tmp_path / "b.whl", name="c", version="1.0.0", payload={"c/__init__.py": "bb"})
    assert wm.wheel_consistency_hash(w1) != wm.wheel_consistency_hash(w2)


# ---- build-wheel-first: empty-dir contract + real build (§5) ----

def test_build_wheel_requires_empty_dest(tmp_path):
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / "stray.txt").write_text("x")
    with pytest.raises(wm.WheelValidationError):
        wm.build_wheel(pip_python(), tmp_path, dest)


STARTER = Path(__file__).resolve().parents[2] / "starter-packs"


@pytest.mark.skipif(not STARTER.is_dir(), reason="starter-packs not present")
def test_starter_agents_build_and_validate():
    """§22 metadata: starter-pack agents build to exactly one wheel with a coherent
    METADATA/RECORD/top-level and canonical name/version.

    By default a deterministic representative SAMPLE is built (each ``pip wheel``
    with build isolation is seconds; the full set is ~24 min). Set
    ``AGENTNODE_M1_FULL_BUILD=1`` to sweep all agents — verified green out of band."""
    import os
    py = pip_python()
    agent_dirs = sorted(p for p in STARTER.iterdir()
                        if p.is_dir() and (p / "agentnode.yaml").is_file()
                        and (p / "pyproject.toml").is_file())
    assert len(agent_dirs) >= 25, f"expected the full starter-pack agent set, saw {len(agent_dirs)}"
    if not os.environ.get("AGENTNODE_M1_FULL_BUILD"):
        # deterministic spread across the sorted set
        idx = {0, len(agent_dirs) // 4, len(agent_dirs) // 2,
               (3 * len(agent_dirs)) // 4, len(agent_dirs) - 1}
        agent_dirs = [agent_dirs[i] for i in sorted(idx)]
    import yaml
    checked = 0
    for adir in agent_dirs:
        manifest = yaml.safe_load((adir / "agentnode.yaml").read_text(encoding="utf-8"))
        entrypoint = manifest.get("entrypoint")
        if not entrypoint:
            continue
        top = wm.top_level_from_entrypoint(entrypoint)
        dest = Path(tempfile.mkdtemp(prefix="anwheel-"))
        try:
            wheel = wm.build_wheel(py, adir, dest)
            ident = wm.validate_wheel(wheel, top)
            assert ident.distribution == wm.canonical_dist_name(ident.distribution)
            assert ident.version == wm.canonical_dist_version(ident.version)
            assert top in ident.top_levels
            checked += 1
        finally:
            shutil.rmtree(dest, ignore_errors=True)
    assert checked >= 3
