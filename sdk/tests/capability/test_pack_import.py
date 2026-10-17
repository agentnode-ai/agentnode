"""Registry-Pack -> Capability: one pack becomes exactly one executable, signed capability.

Inputs are built with the product's own paths (``_pack_fixtures``): the publish artifact builder
and the installer's lock-entry shape, sealed with ``lock_integrity``. Execution uses the
``FakeWorker`` double (it runs the job's program locally); the real sandbox is proven on the
kept hosts. Criteria ids refer to ``registry-pack-import-r1`` (P1-P20).
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import io
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest
import yaml
from _doubles import FakeWorker
from _pack_fixtures import (
    FIXTURE_PACKS,
    STARTER,
    build_artifact,
    copy_pack,
    lock_entry_for,
)
from agentnode_sdk import lock_integrity
from agentnode_sdk.capability import _pack_shim, pack_import, tree_pack
from agentnode_sdk.capability import manifest as M
from agentnode_sdk.capability.approval import ApprovalError, ApprovalStore, grant
from agentnode_sdk.capability.audit import AuditLedger
from agentnode_sdk.capability.index import IndexError_, LocalIndex
from agentnode_sdk.capability.pack_import import PackRefused, import_pack
from agentnode_sdk.capability.usability import UsabilityClient
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

NAMESPACE = "local"
POSITIVE = ("regex-builder-pack", "document-redaction-pack", "citation-manager-pack")
_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))


def _pub(priv=_KEY):
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _manifest_of(pack_dir: Path) -> dict:
    return yaml.safe_load((pack_dir / "agentnode.yaml").read_text(encoding="utf-8"))


def _import(
    pack_dir: Path, *, artifact=None, lock=None, package_id=None, version=None, key=None
):
    man = _manifest_of(pack_dir)
    package_id = package_id or man["package_id"]
    artifact = build_artifact(pack_dir) if artifact is None else artifact
    lock = lock_entry_for(pack_dir, artifact) if lock is None else lock
    return import_pack(
        artifact,
        lock,
        package_key=key or package_id,
        package_id=package_id,
        version=version or man["version"],
        namespace=NAMESPACE,
        private_key=_KEY,
        public_key_bytes=_pub(),
    )


def _refusal(fn, *args, **kwargs) -> PackRefused:
    with pytest.raises(PackRefused) as info:
        fn(*args, **kwargs)
    return info.value


def _edit_yaml(pack_dir: Path, mutate) -> None:
    path = pack_dir / "agentnode.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _regex_copy(tmp_path: Path, name="pack") -> Path:
    return copy_pack(STARTER / "regex-builder-pack", tmp_path / name)


def _write_tool(pack_dir: Path, source: str) -> None:
    (pack_dir / "src" / "regex_builder_pack" / "tool.py").write_text(
        source, encoding="utf-8"
    )


# =============================================================== P11 positive packs
@pytest.mark.parametrize("name", POSITIVE)
def test_positive_pack_imports_registers_lists_and_describes(tmp_path, name):
    imp = _import(STARTER / name)
    index = LocalIndex(str(tmp_path / "index"))
    assert index.put_blob(imp.tar_bytes) == imp.tar_sha256
    resolved = index.register(imp.manifest)
    assert resolved.capability_id == f"{NAMESPACE}/{name}"
    assert {
        "capability_id": f"{NAMESPACE}/{name}",
        "capability_version": "1.0.0",
        "execution_model": "agentnode_executed_mcp",
    } in index.list()
    d = index.describe(f"{NAMESPACE}/{name}")
    assert d["provenance"]["signer_roles"] == ["importing_publisher"]
    assert d["provenance"]["upstream_signed"] is False
    reg = d["provenance"]["source"]["package"]["registry"]
    assert reg["kind"] == "agentnode-registry-pack" and reg["package_id"] == name
    assert reg["claimed_upstream_publisher"] == "agentnode"
    assert reg["upstream_signature_status"] == "absent"
    assert [t["name"] for t in d["tools"]] == [t["name"] for t in imp.tool_table]


def test_v02_multi_tool_pack_resolves_per_tool_entrypoints():
    imp = _import(FIXTURE_PACKS / "multi-tool-pack")
    assert [(t["name"], t["module"], t["function"]) for t in imp.tool_table] == [
        ("word_stats", "multi_tool_pack.tool", "word_stats"),
        ("slugify", "multi_tool_pack.slug", "slugify"),
    ]


# =============================================================== P4 capability permission mapping
def test_capability_permissions_are_the_exact_mapping_and_always_require_approval():
    imp = _import(STARTER / "regex-builder-pack")
    assert _manifest_of(STARTER / "regex-builder-pack")["permissions"][
        "user_approval"
    ] == {"required": "never"}
    assert imp.manifest["permissions"] == {
        "network": {"allowed_domains": []},
        "filesystem": {"level": "none"},
        "data_access": {"level": "input_only"},
        "env_keys": [],
        "code_execution": True,
        "user_approval": "required",
    }
    assert (
        imp.registry_provenance["source_permissions"]["code_execution_level"] == "none"
    )


# =============================================================== P9 provenance
def test_signed_provenance_binds_every_source_input():
    pack = STARTER / "citation-manager-pack"
    artifact = build_artifact(pack)
    lock = lock_entry_for(pack, artifact)
    imp = _import(pack, artifact=artifact, lock=lock)
    reg = imp.manifest["source"]["package"]["registry"]
    assert reg["artifact_sha256"] == hashlib.sha256(artifact).hexdigest()
    assert reg["artifact_bytes"] == len(artifact)
    assert (
        reg["manifest_sha256"]
        == hashlib.sha256((pack / "agentnode.yaml").read_bytes()).hexdigest()
    )
    assert (
        reg["pyproject_sha256"]
        == hashlib.sha256((pack / "pyproject.toml").read_bytes()).hexdigest()
    )
    assert reg["lock_integrity"] == lock["_integrity"]
    assert reg["shim_generator_version"] == _pack_shim.PACK_SHIM_VERSION
    assert reg["upstream_authenticity"] == "not established by this import"
    # the canonical content digest covers the WHOLE extracted artifact, tests included
    with tarfile.open(fileobj=io.BytesIO(artifact), mode="r:gz") as tf:
        assert any("/tests/" in m.name for m in tf.getmembers())
    assert reg["source_content_digest"] == imp.tree_provenance["source_content_digest"]
    # the signature verifies over the provenance, and only as the importing publisher
    assert M.verify_manifest(imp.manifest).ok
    assert [s["role"] for s in imp.manifest["signatures"]] == ["importing_publisher"]


def test_executable_tree_is_shim_plus_runtime_package_plus_provenance(tmp_path):
    imp = _import(STARTER / "document-redaction-pack")
    out = tmp_path / "tree"
    tree_pack.safe_extract(imp.tar_bytes, out)
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert files == [
        "agentnode-pack.json",
        "document_redaction_pack/__init__.py",
        "document_redaction_pack/tool.py",
        "entry.py",
    ]
    src = STARTER / "document-redaction-pack" / "src" / "document_redaction_pack"
    for f in ("__init__.py", "tool.py"):
        assert (out / "document_redaction_pack" / f).read_bytes() == (
            src / f
        ).read_bytes()
    assert (out / "entry.py").read_bytes() == imp.shim_bytes
    assert imp.tree_digest == tree_pack.tree_digest_of_dir(out)
    prov = json.loads((out / "agentnode-pack.json").read_text(encoding="utf-8"))
    assert (
        "artifact_sha256" not in prov
    )  # only content-derived values in the executable tree


# =============================================================== P2 exact input binding
def test_wrong_lock_key_is_refused():
    assert (
        _refusal(_import, STARTER / "regex-builder-pack", key="other-pack").code
        == "lock.wrong_key"
    )


def test_missing_lock_entry_is_refused():
    pack = STARTER / "regex-builder-pack"
    assert (
        _refusal(
            import_pack,
            build_artifact(pack),
            None,
            package_key="regex-builder-pack",
            package_id="regex-builder-pack",
            version="1.0.0",
            namespace=NAMESPACE,
            private_key=_KEY,
            public_key_bytes=_pub(),
        ).code
        == "lock.missing_entry"
    )


def test_missing_lock_integrity_is_refused():
    pack = STARTER / "regex-builder-pack"
    art = build_artifact(pack)
    lock = lock_entry_for(pack, art)
    lock.pop("_integrity")
    assert (
        _refusal(_import, pack, artifact=art, lock=lock).code
        == "lock.integrity_missing"
    )


@pytest.mark.parametrize(
    "integrity",
    [
        {"algorithm": "md5", "canonical_version": 1, "hash": "0" * 64},
        {"algorithm": "sha256", "canonical_version": 9, "hash": "0" * 64},
        {"algorithm": "sha256", "canonical_version": 1, "hash": "XYZ"},
        "not-a-mapping",
    ],
)
def test_malformed_lock_integrity_is_refused(integrity):
    pack = STARTER / "regex-builder-pack"
    art = build_artifact(pack)
    lock = lock_entry_for(pack, art)
    lock["_integrity"] = integrity
    assert (
        _refusal(_import, pack, artifact=art, lock=lock).code
        == "lock.integrity_malformed"
    )


def test_lock_changed_without_reseal_is_refused():
    pack = STARTER / "regex-builder-pack"
    art = build_artifact(pack)
    lock = lock_entry_for(pack, art)
    lock["entrypoint"] = "regex_builder_pack.other"
    assert (
        _refusal(_import, pack, artifact=art, lock=lock).code
        == "lock.integrity_mismatch"
    )


def test_lock_version_mismatch_is_refused():
    pack = STARTER / "regex-builder-pack"
    art = build_artifact(pack)
    lock = lock_entry_for(pack, art, version="1.0.1")
    assert (
        _refusal(_import, pack, artifact=art, lock=lock).code == "lock.version_mismatch"
    )


@pytest.mark.parametrize(
    "value,code",
    [
        ("", "lock.artifact_hash_missing"),
        ("deadbeef", "lock.artifact_hash_malformed"),
        ("sha256:" + "A" * 64, "lock.artifact_hash_malformed"),
    ],
)
def test_empty_or_malformed_lock_artifact_hash_is_refused(value, code):
    pack = STARTER / "regex-builder-pack"
    art = build_artifact(pack)
    lock = lock_entry_for(pack, art, artifact_hash=value)
    assert _refusal(_import, pack, artifact=art, lock=lock).code == code


def test_unknown_lock_field_is_refused():
    pack = STARTER / "regex-builder-pack"
    art = build_artifact(pack)
    lock = lock_entry_for(pack, art, surprise="x")
    assert (
        _refusal(_import, pack, artifact=art, lock=lock).code == "lock.unexpected_field"
    )


def test_artifact_digest_tampering_is_refused():
    pack = STARTER / "regex-builder-pack"
    art = build_artifact(pack)
    lock = lock_entry_for(pack, art)
    tampered = art[:-1] + bytes([art[-1] ^ 0x01])
    assert (
        _refusal(_import, pack, artifact=tampered, lock=lock).code
        == "artifact.hash_mismatch"
    )


def _tar_gz(members: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for info, data in members:
            tf.addfile(info, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


def _file(name: str, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, data


def _craft(members, tmp_path):
    """A lock-consistent crafted artifact: the lock is sealed for exactly these bytes."""
    pack = STARTER / "regex-builder-pack"
    art = _tar_gz(members)
    return art, lock_entry_for(pack, art)


def test_symlink_member_is_refused(tmp_path):
    link = tarfile.TarInfo("regex-builder-pack/src/regex_builder_pack/evil.py")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    art, lock = _craft([(link, None)], tmp_path)
    assert (
        _refusal(_import, STARTER / "regex-builder-pack", artifact=art, lock=lock).code
        == "artifact.link_or_special"
    )


def test_traversal_member_is_refused(tmp_path):
    art, lock = _craft([_file("regex-builder-pack/../escape.py", b"x")], tmp_path)
    assert (
        _refusal(_import, STARTER / "regex-builder-pack", artifact=art, lock=lock).code
        == "artifact.unsafe_path"
    )


def test_second_root_is_refused(tmp_path):
    art, lock = _craft(
        [_file("regex-builder-pack/agentnode.yaml", b"x"), _file("other/x.py", b"x")],
        tmp_path,
    )
    assert (
        _refusal(_import, STARTER / "regex-builder-pack", artifact=art, lock=lock).code
        == "artifact.wrong_root"
    )


def test_wrong_root_name_is_refused():
    pack = STARTER / "regex-builder-pack"
    art = build_artifact(pack, package_id="not-the-pack")
    assert (
        _refusal(_import, pack, artifact=art, lock=lock_entry_for(pack, art)).code
        == "artifact.wrong_root"
    )


def test_not_a_gzip_tar_is_refused():
    pack = STARTER / "regex-builder-pack"
    art = b"this is not an archive"
    assert (
        _refusal(_import, pack, artifact=art, lock=lock_entry_for(pack, art)).code
        == "artifact.not_gzip_tar"
    )


def test_member_count_and_size_limits_are_enforced(tmp_path, monkeypatch):
    pack = STARTER / "regex-builder-pack"
    art = build_artifact(pack)
    lock = lock_entry_for(pack, art)
    monkeypatch.setattr(pack_import, "MAX_MEMBERS", 3)
    assert (
        _refusal(_import, pack, artifact=art, lock=lock).code
        == "artifact.too_many_members"
    )
    monkeypatch.setattr(pack_import, "MAX_MEMBERS", 500)
    monkeypatch.setattr(pack_import, "MAX_MEMBER_BYTES", 100)
    assert (
        _refusal(_import, pack, artifact=art, lock=lock).code
        == "artifact.member_too_large"
    )
    monkeypatch.setattr(pack_import, "MAX_MEMBER_BYTES", 10 * 1024 * 1024)
    monkeypatch.setattr(pack_import, "MAX_TOTAL_BYTES", 4000)
    assert (
        _refusal(_import, pack, artifact=art, lock=lock).code
        == "artifact.total_too_large"
    )


def test_manifest_identity_mismatch_is_refused(tmp_path):
    pack = _regex_copy(tmp_path)
    _edit_yaml(pack, lambda m: m.update(package_id="regex-builder-pack-x"))
    art = build_artifact(pack, package_id="regex-builder-pack")
    lock = lock_entry_for(pack, art)
    assert (
        _refusal(
            _import, pack, artifact=art, lock=lock, package_id="regex-builder-pack"
        ).code
        == "manifest.identity_mismatch"
    )


# =============================================================== P3 manifest validity
@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda m: m.update(manifest_version="0.3"), "manifest.unsupported_version"),
        (lambda m: m.update(package_type="agent"), "manifest.unsupported_kind"),
        (lambda m: m.update(runtime="mcp"), "manifest.unsupported_kind"),
        (
            lambda m: m.update(install_mode="remote_endpoint"),
            "manifest.unsupported_kind",
        ),
        (lambda m: m.update(summary="too short"), "manifest.invalid"),
        (lambda m: m.update(entrypoint="Not A Module"), "entrypoint.invalid"),
        (
            lambda m: m["capabilities"].update(prompts=[{"name": "p"}]),
            "manifest.unsupported_feature",
        ),
    ],
)
def test_manifest_outside_the_admitted_subset_is_refused(tmp_path, mutate, code):
    pack = _regex_copy(tmp_path)
    _edit_yaml(pack, mutate)
    assert _refusal(_import, pack).code == code


# =============================================================== P4 permission closure
@pytest.mark.parametrize(
    "mutate,code",
    [
        (
            lambda m: m["permissions"].update(
                network={"level": "restricted", "allowed_domains": ["example.com"]}
            ),
            "permission.network",
        ),
        (
            lambda m: m["permissions"].update(network={"level": "unrestricted"}),
            "permission.network",
        ),
        (
            lambda m: m["permissions"].update(filesystem={"level": "temp"}),
            "permission.filesystem",
        ),
        (
            lambda m: m["permissions"].update(
                code_execution={"level": "limited_subprocess"}
            ),
            "permission.code_execution",
        ),
        (
            lambda m: m["permissions"].update(data_access={"level": "persistent"}),
            "permission.data_access",
        ),
        (
            lambda m: m["permissions"].update(external_integrations=["github"]),
            "permission.external_integrations",
        ),
        (
            lambda m: m.update(
                env_requirements=[{"name": "API_KEY", "required": True}]
            ),
            "permission.secrets",
        ),
        (
            lambda m: m.update(connector={"provider": "x", "auth_type": "api_key"}),
            "permission.connector",
        ),
        (lambda m: m.update(mcp_server={"command": ["x"]}), "permission.mcp_server"),
    ],
)
def test_permission_beyond_the_closure_is_refused(tmp_path, mutate, code):
    pack = _regex_copy(tmp_path)
    _edit_yaml(pack, mutate)
    assert code in [v["code"] for v in _refusal(_import, pack).violations]


def test_disallowed_network_is_refused(tmp_path):
    pack = _regex_copy(tmp_path)
    _edit_yaml(
        pack, lambda m: m["permissions"].update(network={"level": "unrestricted"})
    )
    assert _refusal(_import, pack).code == "permission.network"


def test_network_module_in_a_network_none_pack_is_refused(tmp_path):
    pack = _regex_copy(tmp_path)
    _write_tool(
        pack,
        "import urllib.request\n\n\ndef run(pattern, **kw):\n"
        "    return urllib.request.urlopen(pattern).read()\n",
    )
    assert _refusal(_import, pack).code == "permission.network"


def test_permission_widening_is_refused(tmp_path):
    pack = _regex_copy(tmp_path)
    _edit_yaml(
        pack, lambda m: m["permissions"].update(filesystem={"level": "workspace_write"})
    )
    assert _refusal(_import, pack).code == "permission.filesystem"


def test_deviating_lock_entry_is_refused():
    pack = STARTER / "regex-builder-pack"
    art = build_artifact(pack)
    lock = lock_entry_for(pack, art)
    widened = dict(lock)
    widened["permissions"] = dict(lock["permissions"], network_level="unrestricted")
    widened = lock_integrity.seal_entry(
        {k: v for k, v in widened.items() if k != "_integrity"}
    )
    assert (
        _refusal(_import, pack, artifact=art, lock=widened).code
        == "lock.manifest_mismatch"
    )


def test_changed_manifest_against_the_lock_is_refused(tmp_path):
    pack = copy_pack(FIXTURE_PACKS / "multi-tool-pack", tmp_path / "multi")
    original_art = build_artifact(pack)
    lock = lock_entry_for(pack, original_art)
    _edit_yaml(
        pack,
        lambda m: m["capabilities"]["tools"][1].update(
            entrypoint="multi_tool_pack.tool:word_stats"
        ),
    )
    changed = build_artifact(pack)
    lock = lock_integrity.seal_entry(
        {
            **{k: v for k, v in lock.items() if k != "_integrity"},
            "artifact_hash": "sha256:" + hashlib.sha256(changed).hexdigest(),
        }
    )
    assert (
        _refusal(_import, pack, artifact=changed, lock=lock).code
        == "lock.manifest_mismatch"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("connector", {"provider": "x"}),
        ("mcp_command", ["node", "x"]),
        ("env_requirements", [{"name": "K"}]),
        ("publisher_slug", "someone-else"),
    ],
)
def test_lock_field_the_pack_cannot_carry_is_refused(field, value):
    pack = STARTER / "regex-builder-pack"
    art = build_artifact(pack)
    lock = lock_entry_for(pack, art, **{field: value})
    assert _refusal(_import, pack, artifact=art, lock=lock).code in (
        "lock.unreconcilable_field",
        "lock.manifest_mismatch",
    )


# =============================================================== P5 dependency closure
def test_declared_dependency_is_refused(tmp_path):
    pack = _regex_copy(tmp_path)
    text = (
        (pack / "pyproject.toml")
        .read_text(encoding="utf-8")
        .replace("dependencies = []", 'dependencies = ["requests>=2"]')
    )
    (pack / "pyproject.toml").write_text(text, encoding="utf-8")
    assert _refusal(_import, pack).code == "dependency.declared"


def test_optional_dependency_is_refused(tmp_path):
    pack = _regex_copy(tmp_path)
    with open(pack / "pyproject.toml", "a", encoding="utf-8") as fh:
        fh.write(
            '\n[project.optional-dependencies]\nextra = ["numpy"]\n'
        )  # a sub-table of [project]
    assert _refusal(_import, pack).code == "dependency.optional"


def test_undeclared_static_dependency_is_refused(tmp_path):
    pack = _regex_copy(tmp_path)
    _write_tool(
        pack,
        "import yaml\n\n\ndef run(pattern, **kw):\n    return {'x': yaml.safe_dump(pattern)}\n",
    )
    assert _refusal(_import, pack).code == "dependency.undeclared"


def test_undeclared_dynamic_dependency_is_refused(tmp_path):
    pack = _regex_copy(tmp_path)
    _write_tool(
        pack,
        "def run(pattern, **kw):\n"
        "    mod = __import__(pattern)\n    return {'m': mod.__name__}\n",
    )
    assert _refusal(_import, pack).code == "dependency.dynamic_import"


@pytest.mark.parametrize(
    "source,code",
    [
        (
            "def run(pattern, **kw):\n    return __import__('pip')\n",
            "dependency.undeclared",
        ),
        (
            "def run(pattern, **kw):\n    loader = __import__\n    return loader('json')\n",
            "dependency.dynamic_import",
        ),
        (
            "import importlib.util\n\n\ndef run(pattern, **kw):\n    return {}\n",
            "dependency.dynamic_import",
        ),
        (
            "def run(pattern, **kw):\n    return eval(pattern)\n",
            "permission.code_execution",
        ),
        (
            "import subprocess\n\n\ndef run(pattern, **kw):\n    return {}\n",
            "permission.code_execution",
        ),
        (
            "from .nowhere import thing\n\n\ndef run(pattern, **kw):\n    return {}\n",
            "dependency.unresolved",
        ),
        (
            "from ... import thing\n\n\ndef run(pattern, **kw):\n    return {}\n",
            "dependency.unresolved",
        ),
        ("def run(pattern, **kw)\n    return {}\n", "layout.syntax_error"),
    ],
)
def test_unsafe_or_unresolvable_code_is_refused(tmp_path, source, code):
    pack = _regex_copy(tmp_path)
    _write_tool(pack, source)
    assert code in [v["code"] for v in _refusal(_import, pack).violations]


_RUN = "\n\n\ndef run(pattern, **kw):\n    return "


@pytest.mark.parametrize(
    "source,code",
    [
        pytest.param(
            "import builtins" + _RUN + "builtins.exec(pattern)\n",
            "permission.code_execution",
            id="builtins-exec",
        ),
        pytest.param(
            "import builtins" + _RUN + "getattr(builtins, '__import__')('socket')\n",
            "dependency.dynamic_import",
            id="getattr-builtins-import",
        ),
        pytest.param(
            _RUN.lstrip("\n") + "().__class__.__base__.__subclasses__()\n",
            "dependency.dynamic_import",
            id="dunder-chain",
        ),
        pytest.param(
            "import re" + _RUN + "getattr(re, pattern)\n",
            "dependency.dynamic_import",
            id="computed-getattr",
        ),
        pytest.param(
            _RUN.lstrip("\n") + "globals()['__builtins__']\n",
            "dependency.dynamic_import",
            id="globals-builtins",
        ),
        pytest.param(
            _RUN.lstrip("\n") + "__builtins__['exec'](pattern)\n",
            "dependency.dynamic_import",
            id="builtins-name",
        ),
        pytest.param(
            "import sys" + _RUN + "sys.modules['builtins']\n",
            "dependency.dynamic_import",
            id="sys-modules",
        ),
        pytest.param(
            "import sys" + _RUN + "sys._getframe().f_globals\n",
            "dependency.dynamic_import",
            id="frame-globals",
        ),
        pytest.param(
            "import os" + _RUN + "os.system(pattern)\n",
            "permission.code_execution",
            id="os-system",
        ),
        pytest.param(
            "from os import system" + _RUN + "system(pattern)\n",
            "permission.code_execution",
            id="from-os-import-system",
        ),
        pytest.param(
            "import pickle" + _RUN + "pickle.loads(pattern)\n",
            "permission.code_execution",
            id="pickle",
        ),
        pytest.param(
            "from operator import attrgetter" + _RUN + "attrgetter(pattern)(kw)\n",
            "dependency.dynamic_import",
            id="attrgetter",
        ),
        pytest.param(
            "import importlib"
            + _RUN
            + "getattr(importlib, 'import_module')(pattern)\n",
            "dependency.dynamic_import",
            id="getattr-import-module",
        ),
        pytest.param(
            "import inspect" + _RUN + "inspect.currentframe()\n",
            "dependency.dynamic_import",
            id="inspect",
        ),
        pytest.param(
            "import types" + _RUN + "types.CodeType\n",
            "permission.code_execution",
            id="codetype",
        ),
    ],
)
def test_reflective_routes_around_the_scan_are_refused(tmp_path, source, code):
    """Review R0001 F-P5: builtins.exec / getattr(builtins, "__import__") and their relatives."""
    pack = _regex_copy(tmp_path)
    _write_tool(pack, source)
    assert code in [v["code"] for v in _refusal(_import, pack).violations]


def test_ordinary_attributes_and_constant_getattr_stay_importable(tmp_path):
    pack = _regex_copy(tmp_path)
    _write_tool(
        pack,
        "import re\n\n\nclass Holder:\n    def __init__(self, x):\n        self.x = x\n\n\n"
        "def run(pattern, **kw):\n    h = Holder(pattern)\n"
        "    return {'n': __name__, 'x': getattr(h, 'x'), 'c': re.compile(pattern).pattern}\n",
    )
    _import(pack)


def test_constant_stdlib_dynamic_import_is_allowed(tmp_path):
    pack = _regex_copy(tmp_path)
    _write_tool(
        pack,
        "def run(pattern, **kw):\n    return {'n': __import__('json').dumps(pattern)}\n",
    )
    _import(pack)


@pytest.mark.parametrize(
    "source,code",
    [
        pytest.param(
            "import timeit" + _RUN + "timeit.timeit(pattern)\n",
            "dependency.stdlib_not_admitted",
            id="timeit",
        ),
        pytest.param(
            "import logging.config" + _RUN + "logging.config.dictConfig({})\n",
            "dependency.stdlib_not_admitted",
            id="logging-config",
        ),
        pytest.param(
            "import pydoc" + _RUN + "pydoc.locate(pattern)\n",
            "dependency.stdlib_not_admitted",
            id="pydoc-locate",
        ),
        pytest.param(
            "import sqlite3" + _RUN + "sqlite3.connect(':memory:')\n",
            "dependency.stdlib_not_admitted",
            id="sqlite3",
        ),
        pytest.param(
            "import doctest" + _RUN + "doctest.run_docstring_examples\n",
            "dependency.stdlib_not_admitted",
            id="doctest",
        ),
        pytest.param(
            "from concurrent.futures import ProcessPoolExecutor"
            + _RUN
            + "ProcessPoolExecutor\n",
            "permission.code_execution",
            id="process-pool",
        ),
        pytest.param(
            "import importlib" + _RUN + "importlib.import_module('json')\n",
            "dependency.stdlib_not_admitted",
            id="importlib-not-admitted",
        ),
        pytest.param(
            "import typing" + _RUN + "typing.get_type_hints(run)\n",
            "permission.code_execution",
            id="get-type-hints",
        ),
        pytest.param(
            "from string import Formatter"
            + _RUN
            + "Formatter().get_field(pattern, [], {})\n",
            "dependency.dynamic_import",
            id="string-formatter",
        ),
        pytest.param(
            "import os" + _RUN + "(lambda f: f(os, pattern))(getattr)\n",
            "dependency.dynamic_import",
            id="getattr-passed-on",
        ),
        pytest.param(
            "import os" + "\n\n\nfetch = getattr" + _RUN + "fetch(os, pattern)\n",
            "dependency.dynamic_import",
            id="getattr-aliased",
        ),
    ],
)
def test_stdlib_outside_the_admitted_list_and_string_driven_routes_are_refused(
    tmp_path, source, code
):
    """The stdlib's own code runners and string-driven reflection (allow-list, not deny-list)."""
    pack = _regex_copy(tmp_path)
    _write_tool(pack, source)
    assert code in [v["code"] for v in _refusal(_import, pack).violations]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "import os"
            + _RUN
            + "open(os.path.join(os.path.dirname(__file__), 'b.py'), 'w')\n",
            id="open-builtin",
        ),
        pytest.param("import io" + _RUN + "io.open(pattern, 'w')\n", id="io-open"),
        pytest.param("import os" + _RUN + "os.open(pattern, 1)\n", id="os-open"),
        pytest.param(
            "import os as o" + _RUN + "o.remove(pattern)\n", id="os-alias-remove"
        ),
        pytest.param(
            "from os import replace" + _RUN + "replace(pattern, 'x')\n",
            id="from-os-replace",
        ),
        pytest.param("import os" + _RUN + "os.makedirs(pattern)\n", id="os-makedirs"),
        pytest.param(
            "import logging" + _RUN + "logging.FileHandler(pattern)\n",
            id="log-file-handler",
        ),
    ],
)
def test_writing_files_is_refused_for_a_filesystem_none_pack(tmp_path, source):
    """A file the pack wrote could be imported as code the scan never saw."""
    pack = _regex_copy(tmp_path)
    _write_tool(pack, source)
    assert "permission.filesystem" in [
        v["code"] for v in _refusal(_import, pack).violations
    ]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("import os" + _RUN + "os.path.os.system\n", id="os-path-os"),
        pytest.param("import random" + _RUN + "random._os\n", id="random-os"),
    ],
)
def test_module_hops_through_attributes_are_refused(tmp_path, source):
    pack = _regex_copy(tmp_path)
    _write_tool(pack, source)
    assert "dependency.dynamic_import" in [
        v["code"] for v in _refusal(_import, pack).violations
    ]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "import functools\n\n\n@functools.singledispatch\ndef f(x):\n    return x\n\n\n"
            "@f.register\ndef _(x: \"__import__('os').system('id')\"):\n    return x\n"
            + _RUN
            + "f(pattern)\n",
            id="singledispatch-string-annotation",
        ),
        pytest.param("x: 'evil()' = 1" + _RUN + "x\n", id="annassign-string"),
        pytest.param(
            "import typing"
            + _RUN.replace("**kw)", "**kw) -> typing.List['evil']")
            + "[]\n",
            id="nested-string-return-annotation",
        ),
        pytest.param(
            "import warnings" + _RUN + "warnings._setoption(pattern)\n",
            id="warnings-setoption",
        ),
        pytest.param(
            "from warnings import _setoption" + _RUN + "_setoption(pattern)\n",
            id="from-warnings-import-private",
        ),
        pytest.param("import random as r" + _RUN + "r._inst\n", id="aliased-private"),
    ],
)
def test_string_annotations_and_private_stdlib_internals_are_refused(tmp_path, source):
    pack = _regex_copy(tmp_path)
    _write_tool(pack, source)
    assert "dependency.dynamic_import" in [
        v["code"] for v in _refusal(_import, pack).violations
    ]


def test_literal_values_and_future_annotations_stay_importable(tmp_path):
    pack = _regex_copy(tmp_path)
    _write_tool(
        pack,
        "from __future__ import annotations\n\nimport typing\n\n\n"
        "def run(pattern: str, style: typing.Literal['apa', 'mla'] = 'apa', **kw) -> dict:\n"
        "    return {'p': pattern, 's': style}\n",
    )
    _import(pack)


def test_ordinary_methods_named_like_file_calls_stay_importable(tmp_path):
    pack = _regex_copy(tmp_path)
    _write_tool(
        pack,
        "import io\nimport os"
        + _RUN
        + "{'r': pattern.replace('a', 'b'), 'l': [1, 2].remove(1),"
        " 'p': os.path.join('a', 'b'), 'e': os.environ.get('HOME'), 's': io.StringIO('x').read()}\n",
    )
    _import(pack)


def test_the_admitted_stdlib_list_covers_the_positive_packs():
    for name in POSITIVE:
        _import(STARTER / name)
    _import(FIXTURE_PACKS / "multi-tool-pack")
    _import(FIXTURE_PACKS / "slow-pack")


@pytest.mark.parametrize(
    "relpath,code",
    [
        ("src/regex_builder_pack/native.so", "layout.compiled_or_native"),
        ("src/regex_builder_pack/tool.cpython-311.pyc", "layout.compiled_or_native"),
        ("src/regex_builder_pack/data.json", "layout.runtime_non_python"),
        ("src/second_pkg/__init__.py", "layout.unexpected_file"),
        ("setup.py", "layout.unexpected_file"),
    ],
)
def test_files_outside_the_layout_are_refused(tmp_path, relpath, code):
    pack = _regex_copy(tmp_path)
    target = pack / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x = 1\n")
    if relpath.endswith(".pyc"):
        # _build_artifact drops .pyc itself; craft the artifact so the importer sees one
        art = build_artifact(pack)
        with tarfile.open(fileobj=io.BytesIO(art), mode="r:gz") as tf:
            members = [
                (m, tf.extractfile(m).read() if m.isreg() else None)
                for m in tf.getmembers()
            ]
        members.append(_file("regex-builder-pack/" + relpath, b"\x00"))
        art = _tar_gz(members)
        assert code in [
            v["code"]
            for v in _refusal(
                _import, pack, artifact=art, lock=lock_entry_for(pack, art)
            ).violations
        ]
        return
    assert code in [v["code"] for v in _refusal(_import, pack).violations]


# =============================================================== P7 entrypoint closure
def test_entrypoint_without_a_function_is_refused(tmp_path):
    pack = _regex_copy(tmp_path)
    _write_tool(pack, "def not_run(pattern, **kw):\n    return {}\n")
    assert _refusal(_import, pack).code == "entrypoint.unresolved"


def test_duplicate_tool_names_are_refused(tmp_path):
    pack = copy_pack(FIXTURE_PACKS / "multi-tool-pack", tmp_path / "multi")
    _edit_yaml(pack, lambda m: m["capabilities"]["tools"][1].update(name="word_stats"))
    assert _refusal(_import, pack).code == "tools.duplicate"


def test_embedded_table_is_proven_at_parser_level(monkeypatch):
    imp = _import(STARTER / "regex-builder-pack")
    assert _pack_shim.embedded_table(imp.shim_bytes) == json.loads(
        _pack_shim.canonical_table_json(imp.tool_table)
    )
    real = _pack_shim.render
    monkeypatch.setattr(
        _pack_shim,
        "render",
        lambda table: real([dict(t, function="something_else") for t in table]),
    )
    assert (
        _refusal(_import, STARTER / "regex-builder-pack").code == "shim.table_mismatch"
    )


def test_render_is_pure_and_template_has_one_placeholder():
    table = _import(STARTER / "regex-builder-pack").tool_table
    assert _pack_shim.render(copy.deepcopy(table)) == _pack_shim.render(table)
    assert _pack_shim._TEMPLATE.count("__AGENTNODE_TOOL_TABLE__") == 1


# =============================================================== P6 no host execution + runtime backstop
def test_import_runs_no_process_and_no_pack_code(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("the importer must not start a process")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    before = set(sys.modules)
    _import(STARTER / "citation-manager-pack")
    assert not any(
        m.startswith("citation_manager_pack") for m in set(sys.modules) - before
    )


def _run_shim(tree: Path, messages: list[dict], timeout=60) -> list[dict]:
    lines = "\n".join(json.dumps(m) for m in messages) + "\n"
    done = subprocess.run(
        [sys.executable, str(tree / "entry.py")],
        input=lines,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(tree),
    )
    return [json.loads(x) for x in done.stdout.splitlines() if x.strip()]


def test_shim_strips_third_party_imports_at_run_time(tmp_path):
    """A module installed in site-packages (pip, as in the sandbox image) cannot be imported by pack code."""
    tree = tmp_path / "tree"
    (tree / "probe_pkg").mkdir(parents=True)
    (tree / "probe_pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tree / "probe_pkg" / "tool.py").write_text(
        "def run(name):\n    import importlib\n    importlib.import_module(name)\n    return {'imported': name}\n",
        encoding="utf-8",
    )
    table = [
        {
            "name": "probe",
            "description": "",
            "input_schema": {"type": "object"},
            "output_schema": {},
            "module": "probe_pkg.tool",
            "function": "run",
        }
    ]
    (tree / "entry.py").write_bytes(_pack_shim.render(table))
    assert (
        importlib.util.find_spec("pip") is not None
    )  # present for the interpreter itself
    out = _run_shim(
        tree,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "probe", "arguments": {"name": "pip"}},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "probe", "arguments": {"name": "json"}},
            },
        ],
    )
    assert out[0]["result"]["isError"] is True
    assert out[0]["result"]["content"][0]["text"].startswith("ModuleNotFoundError")
    assert out[1]["result"]["structuredContent"] == {"imported": "json"}
    assert "Traceback" not in json.dumps(out)


# =============================================================== P8 determinism
_DETERMINISM_SCRIPT = r"""
import hashlib, json, os, shutil, sys, time
sys.path[:0] = [os.environ["PACK_TESTS_DIR"]]
from _pack_fixtures import build_artifact, lock_entry_for
from agentnode_sdk.capability import pack_import as pack_import_mod
from agentnode_sdk.capability.pack_import import import_pack
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
src, dest, reverse = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
files = sorted(p for p in os.listdir(src))
os.makedirs(dest)
# copy in a different enumeration order and with different mtimes
for root, dirs, names in os.walk(src):
    rel = os.path.relpath(root, src)
    os.makedirs(os.path.join(dest, rel), exist_ok=True)
    for n in (reversed(sorted(names)) if reverse else sorted(names)):
        shutil.copyfile(os.path.join(root, n), os.path.join(dest, rel, n))
        stamp = 1_000_000 if reverse else 2_000_000_000
        os.utime(os.path.join(dest, rel, n), (stamp, stamp))
from pathlib import Path
art = build_artifact(Path(dest))
lock = lock_entry_for(Path(dest), art)
key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
imp = import_pack(art, lock, package_key=sys.argv[4], package_id=sys.argv[4], version="1.0.0",
                  namespace="local", private_key=key, public_key_bytes=pub)
unsigned = {k: v for k, v in imp.manifest.items() if k not in ("signatures", "artifact_digest")}
unsigned = json.loads(json.dumps(unsigned))
reg = unsigned["source"]["package"]["registry"]
instance = {k: reg.pop(k) for k in pack_import_mod.ARTIFACT_INSTANCE_FIELDS}
print(json.dumps({
  "shim": hashlib.sha256(imp.shim_bytes).hexdigest(),
  "provenance": hashlib.sha256(json.dumps(imp.tree_provenance, sort_keys=True).encode()).hexdigest(),
  "tar": imp.tar_sha256, "tree_digest": imp.tree_digest,
  "schema_digest": imp.manifest["schema_digest"]["value"],
  "unsigned_manifest": hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode()).hexdigest(),
  "artifact_instance": instance}))
"""


def _determinism_run(
    tmp_path: Path, src: Path, tag: str, reverse: bool, package_id: str
) -> dict:
    env = dict(
        os.environ,
        PACK_TESTS_DIR=str(Path(__file__).parent),
        PYTHONPATH=os.pathsep.join(
            [str(Path(__file__).resolve().parents[2])] + sys.path
        ),
    )
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            _DETERMINISM_SCRIPT,
            str(src),
            str(tmp_path / tag),
            "1" if reverse else "0",
            package_id,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("name", POSITIVE)
def test_generation_is_deterministic_across_processes_paths_mtimes_and_order(
    tmp_path, name
):
    a = _determinism_run(tmp_path, STARTER / name, "a", False, name)
    time.sleep(
        1.1
    )  # a different gzip header second: the registry archive bytes may differ
    b = _determinism_run(tmp_path, STARTER / name, "bb/deeper", True, name)
    for k in (
        "shim",
        "provenance",
        "tar",
        "tree_digest",
        "schema_digest",
        "unsigned_manifest",
    ):
        assert a[k] == b[k], k
    # the archive itself is not byte-reproducible (gzip mtime) -- yet the capability tree is
    assert (
        a["artifact_instance"]["artifact_sha256"]
        != b["artifact_instance"]["artifact_sha256"]
    )


def test_one_changed_source_byte_changes_the_content_and_tree_digests(tmp_path):
    pack = _regex_copy(tmp_path)
    before = _import(pack)
    tool = pack / "src" / "regex_builder_pack" / "tool.py"
    tool.write_bytes(tool.read_bytes() + b"\n")
    after = _import(pack)
    assert (
        before.tree_provenance["source_content_digest"]
        != after.tree_provenance["source_content_digest"]
    )
    assert before.tree_digest != after.tree_digest


def test_a_changed_test_file_changes_the_content_digest_but_not_the_tree(tmp_path):
    pack = _regex_copy(tmp_path)
    before = _import(pack)
    t = pack / "tests" / "test_tool.py"
    t.write_bytes(t.read_bytes() + b"\n# changed\n")
    after = _import(pack)
    assert (
        before.registry_provenance["source_content_digest"]
        != after.registry_provenance["source_content_digest"]
    )
    # the tree's provenance file carries the content digest, so the tree binds the change too
    assert before.tree_digest != after.tree_digest


# =============================================================== P10 index semantics
def test_reimport_is_idempotent_and_changed_content_needs_a_new_version(tmp_path):
    pack = _regex_copy(tmp_path)
    index = LocalIndex(str(tmp_path / "index"))
    art = build_artifact(pack)
    lock = lock_entry_for(pack, art)
    first = _import(pack, artifact=art, lock=lock)
    index.put_blob(first.tar_bytes)
    index.register(first.manifest)
    again = _import(pack, artifact=art, lock=lock)
    assert again.manifest == first.manifest
    index.register(again.manifest)  # identical -> idempotent
    tool = pack / "src" / "regex_builder_pack" / "tool.py"
    tool.write_bytes(tool.read_bytes() + b"\n")
    changed = _import(pack)
    with pytest.raises(IndexError_):
        index.register(changed.manifest)
    _edit_yaml(pack, lambda m: m.update(version="1.0.1"))
    bumped = _import(pack)
    index.put_blob(bumped.tar_bytes)
    assert index.register(bumped.manifest).capability_version == "1.0.1"


# =============================================================== P12 negative starter packs
@pytest.mark.parametrize(
    "name,expected",
    [
        (
            "arxiv-search-pack",
            {"permission.network", "dependency.declared", "dependency.undeclared"},
        ),
        (
            "ai-image-generator-pack",
            {
                "permission.network",
                "permission.filesystem",
                "permission.connector",
                "dependency.declared",
            },
        ),
        ("csv-analyzer-pack", {"permission.filesystem", "dependency.declared"}),
    ],
)
def test_negative_starter_packs_are_refused_with_structured_reasons(name, expected):
    refusal = _refusal(_import, STARTER / name)
    codes = {v["code"] for v in refusal.violations}
    assert expected <= codes, codes
    assert refusal.to_dict()["status"] == "refused"


# =============================================================== P11/P15/P17 execution via the contract
def _local_reference(pack: Path, module: str, function: str, args: dict) -> dict:
    sys.path.insert(0, str(pack / "src"))
    try:
        fn = getattr(importlib.import_module(module), function)
        try:
            return {"ok": True, "value": json.loads(json.dumps(fn(**args)))}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "text": f"{type(exc).__name__}: {exc}"}
    finally:
        sys.path.remove(str(pack / "src"))


def _stand(tmp_path, imp, *, worker):
    au = Ed25519PrivateKey.generate()
    index = LocalIndex(str(tmp_path / "index"))
    index.put_blob(imp.tar_bytes)
    index.register(imp.manifest)
    ledger = AuditLedger(
        str(tmp_path / "audit.jsonl"),
        ledger_id="L1",
        signer_private=au,
        signer_public_bytes=_pub(au),
    )
    return UsabilityClient(
        index=index,
        worker=worker,
        audit_ledger=ledger,
        approval_store=ApprovalStore(consumed_dir=str(tmp_path / "consumed")),
        operator_public_bytes=_pub(),
        worker_identity="host:test",
        stand=str(tmp_path / "stand"),
    )


def _approve(client, inv):
    ap = grant(
        inv.invocation_hash,
        audience=inv.capability_id,
        human_actor="human@test",
        operator_private=_KEY,
        operator_public_bytes=_pub(),
    )
    client.approval_store.put(ap)
    return ap


CASES = [
    (
        "regex-builder-pack",
        {
            "pattern": r"(?P<y>\d{4})-(?P<m>\d{2})",
            "test_string": "x 2026-10",
            "operation": "test",
        },
    ),
    (
        "regex-builder-pack",
        {
            "pattern": "agent",
            "test_string": "Agent agent",
            "operation": "sub",
            "replacement": "node",
            "flags": ["IGNORECASE"],
        },
    ),
    ("regex-builder-pack", {"pattern": "(unclosed", "test_string": "x"}),
    (
        "document-redaction-pack",
        {"text": "mail jane@example.org from 10.1.2.3, SSN 123-45-6789"},
    ),
    ("document-redaction-pack", {"text": "x", "redact_types": ["nonsense"]}),
    (
        "citation-manager-pack",
        {
            "source": {
                "type": "book",
                "authors": ["Ada Lovelace"],
                "title": "Notes",
                "year": 1843,
                "publisher": "Taylor",
            },
            "style": "chicago",
        },
    ),
    (
        "citation-manager-pack",
        {
            "source": {"type": "book", "authors": ["X"], "title": "T", "year": 1},
            "style": "nope",
        },
    ),
]


@pytest.mark.parametrize("name,args", CASES)
def test_worker_result_equals_the_local_reference(tmp_path, name, args):
    pack = STARTER / name
    imp = _import(pack)
    client = _stand(tmp_path, imp, worker=FakeWorker(available=True))
    tool = imp.tool_table[0]
    inv = client.prepare(f"{NAMESPACE}/{name}", tool["name"], args)
    res = client.submit(inv, _approve(client, inv).approval_id)
    call = res.exec_result.result["call"]
    ref = _local_reference(pack, tool["module"], tool["function"], args)
    if ref["ok"]:
        assert call["isError"] is False
        assert call["structuredContent"] == ref["value"]
        assert json.loads(call["content"][0]["text"]) == ref["value"]
    else:
        assert call["isError"] is True and call["content"][0]["text"] == ref["text"]
    assert "Traceback" not in json.dumps(call)
    client.audit_ledger.verify()


def test_v02_pack_runs_both_tools_on_the_worker(tmp_path):
    imp = _import(FIXTURE_PACKS / "multi-tool-pack")
    client = _stand(tmp_path, imp, worker=FakeWorker(available=True))
    for tool, args, expected in (
        ("word_stats", {"text": "a b\nc"}, {"words": 3, "characters": 5, "lines": 2}),
        ("slugify", {"text": "Hello, Wörld!"}, {"slug": "hello-world"}),
    ):
        inv = client.prepare(f"{NAMESPACE}/multi-tool-pack", tool, args)
        res = client.submit(inv, _approve(client, inv).approval_id)
        assert res.exec_result.result["call"]["structuredContent"] == expected


def test_approval_replay_mutation_and_audit(tmp_path):
    imp = _import(STARTER / "document-redaction-pack")
    client = _stand(tmp_path, imp, worker=FakeWorker(available=True))
    cap = f"{NAMESPACE}/document-redaction-pack"
    inv = client.prepare(cap, "document_parsing", {"text": "a@b.co"})
    with pytest.raises(ApprovalError):  # no approval
        client.submit(inv, "no-such-approval")
    ap = _approve(client, inv)
    ok = client.submit(inv, ap.approval_id)
    with pytest.raises(ApprovalError):  # replay of the consumed single-use approval
        client.submit(inv, ap.approval_id)
    inv2 = client.prepare(cap, "document_parsing", {"text": "c@d.co"})
    ap2 = _approve(client, inv2)
    inv2.arguments["text"] = "changed after approval"
    with pytest.raises(ValueError):  # argument mutation breaks the binding
        client.submit(inv2, ap2.approval_id)
    inv3 = client.prepare(
        cap, "document_parsing", {"text": "x", "redact_types": ["nonsense"]}
    )
    failed = client.submit(inv3, _approve(client, inv3).approval_id)
    assert (
        failed.exec_result.result["call"]["isError"] is True
    )  # a failed tool call is still audited
    client.audit_ledger.verify()
    assert [ok.audit_entry.seq, failed.audit_entry.seq] == [1, 2]
    assert ok.audit_entry.body["invocation_hash"] == inv.invocation_hash


def test_no_worker_refuses_without_local_fallback(tmp_path):
    from agentnode_sdk.capability.execution import ExecutionError

    imp = _import(STARTER / "regex-builder-pack")
    client = _stand(tmp_path, imp, worker=FakeWorker(available=False))
    inv = client.prepare(
        f"{NAMESPACE}/regex-builder-pack", "data_cleaning", {"pattern": "a"}
    )
    with pytest.raises(ExecutionError):
        client.submit(inv, _approve(client, inv).approval_id)


# =============================================================== CLI front door (import-pack)
def test_cli_import_pack_registers_and_refuses_structurally(tmp_path, capsys):
    from agentnode_sdk.capability import usability_cli

    pack = STARTER / "regex-builder-pack"
    art = build_artifact(pack)
    (tmp_path / "pack.tar.gz").write_bytes(art)
    (tmp_path / "agentnode.lock").write_text(
        json.dumps(
            {
                "lockfile_version": "0.1",
                "packages": {"regex-builder-pack": lock_entry_for(pack, art)},
            }
        ),
        encoding="utf-8",
    )
    argv = [
        "import-pack",
        "--stand",
        str(tmp_path / "stand"),
        "--artifact",
        str(tmp_path / "pack.tar.gz"),
        "--lockfile",
        str(tmp_path / "agentnode.lock"),
        "--version",
        "1.0.0",
        "--namespace",
        NAMESPACE,
    ]
    assert usability_cli.main(argv + ["--package", "regex-builder-pack"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["imported"] == f"{NAMESPACE}/regex-builder-pack" and out["tools"] == [
        "data_cleaning"
    ]
    assert (tmp_path / "stand" / "capability_id.txt").read_text(
        encoding="utf-8"
    ) == out["imported"]
    assert usability_cli.main(argv + ["--package", "citation-manager-pack"]) == 5
    refused = json.loads(capsys.readouterr().out)
    assert refused["status"] == "refused" and refused["code"] == "lock.missing_entry"


# =============================================================== P14 official MCP client over stdio
_MCP_CLIENT_SCRIPT = r"""
import asyncio, json, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def drive(tree):
    params = StdioServerParameters(command=sys.executable, args=[tree + "/entry.py"], cwd=tree)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            good = await session.call_tool("citation_extraction", {
                "source": {"type": "book", "authors": ["Ada Lovelace"], "title": "Notes",
                           "year": 1843},
                "style": "apa"})
            bad = await session.call_tool("citation_extraction", {"source": {"type": "nope"}})
            return {"server": init.serverInfo.name, "protocol": init.protocolVersion,
                    "tools": [t.name for t in tools.tools],
                    "good": [good.isError, good.content[0].text],
                    "bad": [bad.isError, bad.content[0].text]}

print(json.dumps(asyncio.run(drive(sys.argv[1]))))
"""


def test_official_python_mcp_client_drives_the_generated_shim(tmp_path):
    # In its own process: importing the mcp client here would load jsonschema into the test
    # session, which other suites (test_guard_schema) assert never happens.
    imp = _import(STARTER / "citation-manager-pack")
    tree = tmp_path / "tree"
    tree_pack.safe_extract(imp.tar_bytes, tree)
    done = subprocess.run(
        [sys.executable, "-c", _MCP_CLIENT_SCRIPT, tree.as_posix()],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["server"] == "agentnode-pack-shim"
    assert out["tools"] == ["citation_extraction"]
    assert out["good"][0] is False and "Lovelace" in out["good"][1]
    assert out["bad"][0] is True and out["bad"][1].startswith("ValueError")
    assert "Traceback" not in out["bad"][1]
    assert "mcp" not in sys.modules and "jsonschema" not in sys.modules
