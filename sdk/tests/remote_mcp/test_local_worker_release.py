"""Local worker, slice 2: the signed release chain (Codex Q-LW2-0001 D7 as amended; profile local-worker-slice2-r2 L12,
counter-checks 21-23, 38): signature first, canonical bytes, expiry, the monotonic floor, platform, size and hash --
and a rollback is only ever a NEWER sequence."""
from __future__ import annotations

import json
import os
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentnode_sdk.worker import release as RL

PINS = {"schema": "agentnode.worker-pins/1", "channel": "beta", "release_seq": 7,
        "control_url": "https://mcp.agentnode.net/agent/v1/slot", "anchor_sha256": "a" * 64, "operator_pub": "b" * 64,
        "gateway_deployment": "rmcp", "gateway_instance": "g1"}


def _release(tmp_path, seq=7, *, now=None, key=None, days=30, files=None):
    key = key or Ed25519PrivateKey.generate()
    d = tmp_path / ("r%d" % seq)
    d.mkdir()
    files = files or {"agentnode_sdk-0.25.0-py3-none-any.whl": b"wheel bytes", "install.sh": b"#!/bin/sh\n"}
    arts = []
    for name, data in files.items():
        (d / name).write_bytes(data)
        arts.append({"name": name, "kind": "wheel" if name.endswith(".whl") else "installer",
                     "platform": "linux-x86_64" if name.endswith(".whl") else "any", "source": "", "version": "0.25.0"})
    RL.build(d, private_key=key, channel="beta", release_seq=seq, min_release_seq=1, version="0.25.0",
             git_commit="c" * 40, pins=PINS, artifacts=arts, compat={"python": "3.12", "podman_min": "5.8.2"},
             now=now, days=days)
    pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return d, key, pub


def _verify(d, pub, **kw):
    return RL.verify((d / "manifest.json").read_bytes(), (d / "manifest.sig").read_bytes(), pub, **kw)


def test_a_signed_release_verifies_and_every_artifact_matches(tmp_path):
    d, _k, pub = _release(tmp_path)
    doc = _verify(d, pub, floor=0, platform="linux-x86_64")
    for a in doc["artifacts"]:
        RL.check_artifact(doc, a["name"], d / a["name"])
    assert doc["pins"] == PINS and any(a["kind"] == "sbom" for a in doc["artifacts"])
    sbom = json.loads((d / "sbom.json").read_text())
    assert sbom["bomFormat"] == "CycloneDX" and sbom["specVersion"] == "1.5"
    assert {c["name"] for c in sbom["components"]} == {"agentnode_sdk-0.25.0-py3-none-any.whl", "install.sh"}
    assert all(c["licenses"] == [{"license": {"name": "NOASSERTION"}}] for c in sbom["components"])


def test_another_key_is_release_signature_invalid(tmp_path):
    """CC21/L12: a release signed by any other key."""
    d, _k, _pub = _release(tmp_path)
    other = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    with pytest.raises(RL.ReleaseRefused) as got:
        _verify(d, other)
    assert got.value.code == "release_signature_invalid"


def test_one_changed_manifest_byte_is_release_signature_invalid(tmp_path):
    d, _k, pub = _release(tmp_path)
    raw = bytearray((d / "manifest.json").read_bytes())
    raw[raw.index(b'"version":"0.25.0"') + 12] = ord("9")
    (d / "manifest.json").write_bytes(bytes(raw))
    with pytest.raises(RL.ReleaseRefused) as got:
        _verify(d, pub)
    assert got.value.code == "release_signature_invalid"


def test_a_non_canonical_manifest_is_invalid_even_if_signed(tmp_path):
    """CC21: the signed bytes must BE the canonical form -- a pretty-printed, signed copy is refused."""
    d, key, pub = _release(tmp_path)
    doc = json.loads((d / "manifest.json").read_bytes())
    pretty = json.dumps(doc, indent=1, sort_keys=True).encode("ascii")
    (d / "manifest.json").write_bytes(pretty)
    (d / "manifest.sig").write_bytes(key.sign(RL.signed_bytes(pretty)))
    with pytest.raises(RL.ReleaseRefused) as got:
        _verify(d, pub)
    assert got.value.code == "release_manifest_invalid"


def test_an_expired_release_is_refused(tmp_path):
    """CC21."""
    d, _k, pub = _release(tmp_path, now=time.time() - 31 * 86400, days=30)
    with pytest.raises(RL.ReleaseRefused) as got:
        _verify(d, pub)
    assert got.value.code == "release_expired"


def test_a_release_may_not_live_longer_than_thirty_days(tmp_path):
    with pytest.raises(RL.ReleaseRefused) as got:
        _release(tmp_path, days=31)
    assert got.value.code == "release_manifest_invalid"


def test_a_lower_sequence_than_the_floor_is_a_downgrade(tmp_path):
    """CC22."""
    d, _k, pub = _release(tmp_path, seq=6)
    with pytest.raises(RL.ReleaseRefused) as got:
        _verify(d, pub, floor=7)
    assert got.value.code == "release_downgrade"
    assert _verify(d, pub, floor=6)["release_seq"] == 6


def test_a_rollback_is_a_newer_sequence_naming_older_artifacts(tmp_path):
    """CC38: going back means signing a HIGHER sequence; the older manifest itself is a downgrade."""
    key = Ed25519PrivateKey.generate()
    old, _k, pub = _release(tmp_path, seq=7, key=key, files={"agentnode_sdk-0.25.0-py3-none-any.whl": b"v1"})
    new, _k, _p = _release(tmp_path, seq=8, key=key, files={"agentnode_sdk-0.25.0-py3-none-any.whl": b"v2"})
    floor = tmp_path / "floor.json"
    RL.raise_floor(floor, 8)
    with pytest.raises(RL.ReleaseRefused):
        _verify(old, pub, floor=RL.read_floor(floor))
    back, _k, _p = _release(tmp_path, seq=9, key=key, files={"agentnode_sdk-0.25.0-py3-none-any.whl": b"v1"})
    doc = _verify(back, pub, floor=RL.read_floor(floor))
    assert next(a for a in doc["artifacts"] if a["kind"] == "wheel")["sha256"] == \
        next(a for a in _verify(old, pub)["artifacts"] if a["kind"] == "wheel")["sha256"]
    with pytest.raises(RL.ReleaseRefused):
        RL.raise_floor(floor, 7)


def test_a_wrong_platform_is_refused(tmp_path):
    d, _k, pub = _release(tmp_path, files={"x.whl": b"1"})
    with pytest.raises(RL.ReleaseRefused) as got:
        _verify(d, pub, platform="windows-x86_64")
    assert got.value.code == "release_platform_mismatch"


@pytest.mark.parametrize("change", ["byte", "size"])
def test_a_changed_artifact_is_artifact_hash_mismatch(tmp_path, change):
    """CC23."""
    d, _k, pub = _release(tmp_path)
    doc = _verify(d, pub)
    p = d / "install.sh"
    p.write_bytes(b"#!/bin/sh\n" + (b"x" if change == "size" else b"")) if change == "size" else \
        p.write_bytes(b"#!/bin/zh\n")
    with pytest.raises(RL.ReleaseRefused) as got:
        RL.check_artifact(doc, "install.sh", p)
    assert got.value.code == "artifact_hash_mismatch"


def test_an_unsafe_or_unreadable_floor_is_refused(tmp_path):
    p = tmp_path / "floor.json"
    assert RL.read_floor(p) == 0
    p.write_text("not json")
    with pytest.raises(RL.ReleaseRefused) as got:
        RL.read_floor(p)
    assert got.value.code == "release_floor_unreadable"


def test_the_public_key_pem_is_what_openssl_reads(tmp_path):
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    k = Ed25519PrivateKey.generate()
    raw = k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    loaded = load_pem_public_key(RL.public_pem(raw).encode("ascii"))
    assert loaded.public_bytes(Encoding.Raw, PublicFormat.Raw) == raw


@pytest.mark.skipif(os.name != "nt", reason="the Windows verifier runs under Windows PowerShell")
def test_the_vendored_windows_verifier_agrees_with_python(tmp_path):
    """L12 / D7: the PowerShell Ed25519 verifier accepts exactly what Python's signs and refuses a flipped byte."""
    import subprocess
    from pathlib import Path
    d, key, pub = _release(tmp_path)
    ps = Path(__file__).resolve().parents[2] / "deploy" / "local-worker" / "windows" / "Ed25519.ps1"
    script = ("$ErrorActionPreference='Stop'; . '%s'; "
              "$m=[IO.File]::ReadAllBytes('%s'); $s=[IO.File]::ReadAllBytes('%s'); "
              "$pk=[byte[]](%s); $pre=[Text.Encoding]::ASCII.GetBytes(\"agentnode-worker-release/1`n\"); "
              "$all=New-Object byte[] ($pre.Length+$m.Length); [Array]::Copy($pre,$all,$pre.Length); "
              "[Array]::Copy($m,0,$all,$pre.Length,$m.Length); "
              "$ok=Test-Ed25519Signature -PublicKey $pk -Message $all -Signature $s; $s[0]=$s[0] -bxor 1; "
              "$bad=Test-Ed25519Signature -PublicKey $pk -Message $all -Signature $s; Write-Output \"$ok $bad\""
              % (ps, d / "manifest.json", d / "manifest.sig", ",".join(str(b) for b in pub)))
    out = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                         capture_output=True, text=True, timeout=300)
    assert out.stdout.strip().splitlines()[-1] == "True False", out.stdout + out.stderr
