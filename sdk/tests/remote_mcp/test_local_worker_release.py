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
#: the account these tests run as owns their floors (production: root, the default)
ME = os.getuid() if hasattr(os, "getuid") else 0


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
    RL.raise_floor(floor, 8, owner_uid=ME)
    with pytest.raises(RL.ReleaseRefused):
        _verify(old, pub, floor=RL.read_floor(floor, owner_uid=ME))
    back, _k, _p = _release(tmp_path, seq=9, key=key, files={"agentnode_sdk-0.25.0-py3-none-any.whl": b"v1"})
    doc = _verify(back, pub, floor=RL.read_floor(floor, owner_uid=ME))
    assert next(a for a in doc["artifacts"] if a["kind"] == "wheel")["sha256"] == \
        next(a for a in _verify(old, pub)["artifacts"] if a["kind"] == "wheel")["sha256"]
    with pytest.raises(RL.ReleaseRefused):
        RL.raise_floor(floor, 7, owner_uid=ME)


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
    assert RL.read_floor(p, owner_uid=ME) == 0
    p.write_text("not json")
    with pytest.raises(RL.ReleaseRefused) as got:
        RL.read_floor(p, owner_uid=ME)
    assert got.value.code == "release_floor_unreadable"


@pytest.mark.skipif(os.name != "posix", reason="POSIX owner and mode")
def test_the_floor_is_its_owners_alone(tmp_path):
    """CC25: the release floor is written 0600 and read only when it is 0600 and owned by the expected account."""
    p = tmp_path / "floor.json"
    old = os.umask(0)
    try:
        RL.raise_floor(p, 3, owner_uid=ME)
    finally:
        os.umask(old)
    assert p.stat().st_mode & 0o777 == 0o600 and RL.read_floor(p, owner_uid=ME) == 3
    for mode in (0o640, 0o604, 0o620):
        os.chmod(p, mode)
        with pytest.raises(RL.ReleaseRefused) as got:
            RL.read_floor(p, owner_uid=ME)
        assert got.value.code == "release_floor_unreadable"
    os.chmod(p, 0o600)
    with pytest.raises(RL.ReleaseRefused):
        RL.read_floor(p, owner_uid=ME + 1)                              # another account's floor


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


def test_nothing_updates_the_agent_on_its_own():
    """CC37 / L12: a beta update is a person running the installer with --update. Every timer or scheduled task the
    installers create starts something that is not an install, an update or a download."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "deploy" / "local-worker"
    forbidden = re.compile(r"install\.sh|install-agentnode-worker|--update|pip |curl |wget |Invoke-WebRequest|"
                           r"Start-BitsTransfer|https?://", re.I)
    units = {}
    for sh in sorted(root.rglob("*.sh")):
        text = sh.read_text(encoding="utf-8")
        for name, body in re.findall(r"cat > /etc/systemd/system/(\S+) <<'?EOF'?\n(.*?)\nEOF", text, re.S):
            units[name] = body
    timers = [n for n in units if n.endswith(".timer")]
    assert timers == ["agentnode-agent-floor.timer"]
    for timer in timers:
        service = units[timer[:-len(".timer")] + ".service"]
        execs = [ln for ln in service.splitlines() if ln.startswith("ExecStart")]
        assert execs and all(" pki floor advance " in ln and not forbidden.search(ln) for ln in execs), execs
    ps = (root / "windows" / "install-agentnode-worker.ps1").read_text(encoding="utf-8")
    actions = re.findall(r"New-ScheduledTaskAction[^\n]*", ps)
    assert actions and all(not forbidden.search(a) for a in actions), actions
    assert len(re.findall(r"Register-ScheduledTask ", ps)) == 1
    assert not list(root.rglob("*.timer")) and not list(root.rglob("*.xml"))


ROCKY = "Rocky-10-WSL-Base-10.2-20260525.0.x86_64.wsl"


def _checksum(name, data):
    import hashlib
    return "# %s: %d bytes\nSHA256 (%s) = %s\n" % (name, len(data), name, hashlib.sha256(data).hexdigest())


def test_a_rootfs_must_be_the_file_its_upstream_checksum_names(tmp_path):
    """CC20 / L12: the dated image against Rocky's (signature-checked) CHECKSUM, at release time."""
    img = tmp_path / ROCKY
    img.write_bytes(b"rootfs bytes")
    good = _checksum(ROCKY, b"rootfs bytes")
    assert RL.check_upstream(img, good) == __import__("hashlib").sha256(b"rootfs bytes").hexdigest()
    for bad in (_checksum(ROCKY, b"other bytes"), _checksum(ROCKY, b"rootfs bytez"), _checksum("Rocky-10-WSL-Base.latest.x86_64.wsl", b"rootfs bytes"),
                good + good.splitlines()[1] + "\n", good.replace(": 12 bytes", ": 13 bytes"), ""):
        with pytest.raises(RL.ReleaseRefused) as got:
            RL.check_upstream(img, bad)
        assert got.value.code == "release_provenance_invalid"


def _build_inputs(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as K
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    (wheels / "agentnode_sdk-0.25.0-py3-none-any.whl").write_bytes(b"wheel")
    pins = tmp_path / "pins.json"
    pins.write_text(json.dumps({k: PINS[k] for k in ("control_url", "anchor_sha256", "operator_pub",
                                                     "gateway_deployment", "gateway_instance")}))
    key = tmp_path / "release.key"
    key.write_text(K.generate().private_bytes_raw().hex())
    wsl = tmp_path / "wsl"
    (wsl / "rpms").mkdir(parents=True)
    (wsl / ROCKY).write_bytes(b"rootfs bytes")
    (wsl / (ROCKY + ".CHECKSUM")).write_text(_checksum(ROCKY, b"rootfs bytes"))
    (wsl / (ROCKY + ".CHECKSUM.asc")).write_text("signature checked on the build host")
    (wsl / "rpms" / "podman-5.8.2-11.el10_2.x86_64.rpm").write_bytes(b"rpm")
    return wheels, pins, key, wsl


def _builder(tmp_path, wheels, pins, key, wsl, out):
    import subprocess
    import sys
    from pathlib import Path
    sdk = Path(__file__).resolve().parents[2]
    return subprocess.run([sys.executable, "-I", str(sdk / "deploy" / "local-worker" / "release-tools" / "build_bundle.py"),
                           "--wheels", str(wheels), "--sdk", str(sdk), "--pins", str(pins), "--commit", "c" * 40,
                           "--version", "0.25.0", "--key", str(key), "--out", str(out), "--seq", "3",
                           "--windows", str(wsl)], capture_output=True, text=True, timeout=300)


def test_the_builder_signs_a_windows_release_only_over_the_named_rootfs(tmp_path):
    """CC20: the product's builder checks the image against its upstream CHECKSUM BEFORE it signs anything."""
    wheels, pins, key, wsl = _build_inputs(tmp_path)
    out = tmp_path / "bundle.tar"
    ok = _builder(tmp_path, wheels, pins, key, wsl, out)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    import tarfile
    with tarfile.open(out) as tar:
        doc = json.loads(tar.extractfile("manifest.json").read())
    assert doc["compat"]["rootfs_upstream_checksum"] == RL.check_upstream(wsl / ROCKY, (wsl / (ROCKY + ".CHECKSUM")).read_text())
    out.unlink()
    (wsl / ROCKY).write_bytes(b"rootfs bytez")
    bad = _builder(tmp_path, wheels, pins, key, wsl, out)
    assert bad.returncode == 2 and "REFUSED release_provenance_invalid" in bad.stdout and not out.exists()


def test_the_wsl_step_installs_nothing_it_has_not_rechecked():
    """CC20/CC24 (order): inside the distribution the manifest is the bytes Windows verified, then every RPM's hash,
    then rpm -K against a private database -- and only then is anything installed; nothing comes from a repository."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "deploy" / "local-worker"
    s = (root / "windows" / "wsl-inner.sh").read_text(encoding="utf-8")
    first_rpm = s.index("\nrpm ")
    assert s.index('= "$WANT" ]') < s.index("REFUSED artifact_hash_mismatch") < first_rpm
    checks = [m.start() for m in re.finditer(r"rpm --dbpath \"\$DB\" -K", s)]
    install = s.index("rpm -Uvh")
    assert checks and max(checks) < s.index('"--verify-only" ] && {') < install
    assert "|| true" not in s[install:s.index("\n", install)]
    assert not re.search(r"\b(dnf|yum|curl|wget)\b", s)
    ps = (root / "windows" / "install-agentnode-worker.ps1").read_text(encoding="utf-8")
    assert "wsl-inner.sh $mSha" in ps and "ComputeHash($mBytes)" in ps
    lin = (root / "linux" / "install.sh").read_text(encoding="utf-8")
    pips = re.findall(r"pip\" install[^\n]*", lin)
    assert pips and all("--no-index" in p and "--no-deps" in p for p in pips)
    assert not re.search(r"\b(dnf|yum|curl|wget)\b", lin)


@pytest.mark.skipif(os.name != "posix" or not __import__("shutil").which("sha256sum"), reason="POSIX shell tools")
def test_the_wsl_step_refuses_a_changed_manifest_or_rpm_before_any_rpm_call(tmp_path):
    """CC20/CC24, executed: a manifest that is not the verified bytes, an RPM with another hash, an RPM it does not
    name -- each refused by name before rpm is asked anything."""
    import hashlib
    import subprocess
    from pathlib import Path
    script = Path(__file__).resolve().parents[2] / "deploy" / "local-worker" / "windows" / "wsl-inner.sh"
    b = tmp_path / "bundle"
    b.mkdir()
    (b / "a.rpm").write_bytes(b"rpm one")
    doc = {"artifacts": [{"name": "a.rpm", "kind": "rpm", "platform": "wsl-rocky10-x86_64", "size": 7,
                          "sha256": hashlib.sha256(b"rpm one").hexdigest()}]}
    (b / "manifest.json").write_text(json.dumps(doc))
    good = hashlib.sha256((b / "manifest.json").read_bytes()).hexdigest()

    def run(want):
        return subprocess.run(["sh", str(script), want, "--verify-only", str(b)], capture_output=True, text=True)
    assert "REFUSED release_signature_invalid" in run("0" * 64).stdout
    assert "REFUSED release_manifest_invalid" in run("").stdout
    (b / "a.rpm").write_bytes(b"rpm two")
    assert "REFUSED artifact_hash_mismatch: a.rpm" in run(good).stdout
    (b / "a.rpm").write_bytes(b"rpm one")
    (b / "b.rpm").write_bytes(b"stranger")
    assert "REFUSED artifact_hash_mismatch: the RPMs here" in run(good).stdout


def test_a_rollback_restores_the_rolled_back_releases_pins():
    """CC38 / L12 (found preparing the final stand run): the pins in force are those of the release `current` points
    at -- each release keeps its own, switch_to installs them, nothing writes them ahead of the switch -- so an update
    rolled back for wrong pins does not leave the old release running with the new release's pins."""
    import re
    from pathlib import Path
    s = (Path(__file__).resolve().parents[2] / "deploy" / "local-worker" / "linux" / "install.sh").read_text(encoding="utf-8")
    writes = [m.start() for m in re.finditer(r'"\$ETC/pins\.json(\.next)?"', s)]
    body = s[s.index("switch_to() {"):s.index("\n}\n", s.index("switch_to() {"))]
    assert 'install -o root -g root -m 0644 "$1/pins.json" "$ETC/pins.json.next"' in body
    assert 'mv -T "$ETC/pins.json.next" "$ETC/pins.json"' in body
    inside = [w for w in writes if s.index("switch_to() {") < w < s.index("switch_to() {") + len(body)]
    writers = [w for w in writes if w not in inside and re.match(r'.*(\binstall\s|\bcp\s|\bmv\s|>\s*$)', s[s.rfind("\n", 0, w) + 1:w])]
    assert writers == [], [s[s.rfind("\n", 0, w) + 1:w + 20] for w in writers]
    assert len(inside) >= 2                                              # the pattern does find the switch's own
    assert 'cp "$WORK/pins.json" "$REL/pins.json"' in s
