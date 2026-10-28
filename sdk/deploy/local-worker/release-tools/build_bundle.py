"""Build and SIGN one release bundle of the local worker, on the release key holder's own machine (the key never
leaves it; local-worker slice 2, Codex Q-LW2-0001 D7 as amended).

  python build_bundle.py --wheels DIR --sdk SDKDIR --seq N --pins PINS.json --commit SHA --version V
                         --key KEYFILE --out OUT.tar [--channel beta] [--days 30] [--issued EPOCH]
                         [--windows WSLDIR]

--wheels   the SDK wheel (built reproducibly from the commit) and its dependency wheels (downloaded once, pinned)
--sdk      the commit's sdk/ tree (the installers are taken from deploy/local-worker/)
--pins     {anchor_sha256, operator_pub, gateway_deployment, gateway_instance, control_url} of ONE control plane
--windows  a Windows/WSL2 bundle: the directory `prepare-wsl-inputs.sh` made -- the dated Rocky WSL image, its
           upstream CHECKSUM (signature verified there against the pinned Rocky key) and rpms/. The image is checked
           against that CHECKSUM HERE, before anything is signed (`release_provenance_invalid`).
Prints the bundle's SHA-256 and the manifest summary; never key material.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile

ROCKY_RELEASE_KEY = "FC226859C0860BF0DDB95B085B106C736FEDFC85"


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    for a in ("--wheels", "--sdk", "--pins", "--commit", "--version", "--key", "--out"):
        p.add_argument(a, required=True)
    p.add_argument("--seq", type=int, required=True)
    p.add_argument("--min-seq", type=int, default=1)
    p.add_argument("--channel", default="beta")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--issued", type=int, default=None)
    p.add_argument("--tamper", default="", help="TEST ONLY: name of a file to alter AFTER signing")
    p.add_argument("--windows", default="", help="the prepared WSL inputs (see above)")
    args = p.parse_args(argv)
    sys.path.insert(0, args.sdk)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from agentnode_sdk.sandbox.container_backend import _BASE_IMAGE
    from agentnode_sdk.worker import pins as PN
    from agentnode_sdk.worker import release as RL

    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(open(args.key, encoding="ascii").read().strip()))
    pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    target = json.load(open(args.pins, encoding="utf-8"))
    pins = {"schema": PN.PINS_SCHEMA, "channel": args.channel, "release_seq": args.seq,
            "control_url": target["control_url"], "anchor_sha256": target["anchor_sha256"],
            "operator_pub": target["operator_pub"], "gateway_deployment": target["gateway_deployment"],
            "gateway_instance": target["gateway_instance"]}
    stage = tempfile.mkdtemp(prefix="agentnode-bundle-")
    try:
        arts = []
        for name in sorted(os.listdir(args.wheels)):
            if not name.endswith(".whl"):
                continue
            shutil.copyfile(os.path.join(args.wheels, name), os.path.join(stage, name))
            dist, ver = name.split("-")[:2]
            arts.append({"name": name, "kind": "wheel", "platform": "linux-x86_64",
                         "source": "" if dist == "agentnode_sdk" else "https://pypi.org/project/%s/%s/" % (dist, ver),
                         "version": ver})
        linux = os.path.join(args.sdk, "deploy", "local-worker", "linux")
        text = open(os.path.join(linux, "install.sh"), encoding="utf-8").read()
        if text.count("@RELEASE_PUB_PEM@") != 1:
            raise SystemExit("install.sh must carry exactly one @RELEASE_PUB_PEM@")
        text = text.replace("@RELEASE_PUB_PEM@", RL.public_pem(pub).strip())
        with open(os.path.join(stage, "install.sh"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        for n in ("uninstall.sh", "check-runtime.sh"):
            with open(os.path.join(linux, n), encoding="utf-8") as fh:
                body = fh.read()
            with open(os.path.join(stage, n), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
        for n in ("install.sh", "uninstall.sh", "check-runtime.sh"):
            arts.append({"name": n, "kind": "installer", "platform": "linux-x86_64", "source": "", "version": args.version})
        compat_extra = {}
        if args.windows:
            win = os.path.join(args.sdk, "deploy", "local-worker", "windows")
            ps = open(os.path.join(win, "install-agentnode-worker.ps1"), encoding="utf-8").read()
            verifier = open(os.path.join(win, "Ed25519.ps1"), encoding="utf-8").read()
            if ps.count("# @ED25519_VERIFIER@") != 1 or ps.count("@RELEASE_PUB_HEX@") != 1:
                raise SystemExit("the Windows bootstrap must carry exactly one verifier and one key placeholder")
            ps = ps.replace("# @ED25519_VERIFIER@", verifier).replace("@RELEASE_PUB_HEX@", pub.hex())
            with open(os.path.join(stage, "install-agentnode-worker.ps1"), "w", encoding="utf-8-sig", newline="\r\n") as fh:
                fh.write(ps)
            arts.append({"name": "install-agentnode-worker.ps1", "kind": "bootstrap", "platform": "windows-x86_64",
                         "source": "", "version": args.version})
            with open(os.path.join(win, "wsl-inner.sh"), encoding="utf-8") as fh:
                body = fh.read()
            with open(os.path.join(stage, "wsl-inner.sh"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
            arts.append({"name": "wsl-inner.sh", "kind": "installer", "platform": "wsl-rocky10-x86_64", "source": "",
                         "version": args.version})
            wi = args.windows
            rootfs = [n for n in os.listdir(wi) if n.endswith(".wsl")]
            if len(rootfs) != 1 or ".latest." in rootfs[0]:
                raise SystemExit("exactly one DATED rootfs image is expected in %s: %r" % (wi, rootfs))
            image = os.path.join(wi, rootfs[0])
            for extra in (".CHECKSUM", ".CHECKSUM.asc"):
                if not os.path.isfile(image + extra):
                    raise SystemExit("the upstream %s of %s is missing" % (extra, rootfs[0]))
            checksum = open(image + ".CHECKSUM", encoding="ascii").read()
            try:
                upstream = RL.check_upstream(image, checksum)          # BEFORE anything is signed
            except RL.ReleaseRefused as refused:
                print("REFUSED %s: %s" % (refused.code, refused.detail))
                return 2
            shutil.copyfile(image, os.path.join(stage, rootfs[0]))
            arts.append({"name": rootfs[0], "kind": "rootfs", "platform": "wsl-rocky10-x86_64",
                         "source": "https://dl.rockylinux.org/pub/rocky/10/images/x86_64/" + rootfs[0],
                         "version": rootfs[0].split("-WSL-Base-")[1].rsplit(".x86_64", 1)[0]})
            for n in sorted(os.listdir(os.path.join(wi, "rpms"))):
                shutil.copyfile(os.path.join(wi, "rpms", n), os.path.join(stage, n))
                arts.append({"name": n, "kind": "rpm", "platform": "wsl-rocky10-x86_64",
                             "source": "https://dl.rockylinux.org/pub/rocky/10/ (BaseOS/AppStream)",
                             "version": n[:-4]})
            compat_extra = {"rocky_release_key": ROCKY_RELEASE_KEY, "rootfs_upstream_checksum": upstream,
                            "rootfs_checksum_file_sha256": hashlib.sha256(checksum.encode("ascii")).hexdigest(),
                            "wsl_min": "2.0.0"}
        doc = RL.build(stage, private_key=key, channel=args.channel, release_seq=args.seq, min_release_seq=args.min_seq,
                       version=args.version, git_commit=args.commit, pins=pins, artifacts=arts,
                       compat=dict({"python": "3.12", "podman_min": "5.8.2", "sandbox_image": _BASE_IMAGE,
                                    "distribution": "rocky-10.2", "shadow_utils": "shadow-utils-4.15.0-11.el10.x86_64"},
                                   **compat_extra),
                       now=args.issued, days=args.days)
        RL.verify(open(os.path.join(stage, "manifest.json"), "rb").read(),
                  open(os.path.join(stage, "manifest.sig"), "rb").read(), pub, platform="linux-x86_64")
        if args.tamper:
            with open(os.path.join(stage, args.tamper), "ab") as fh:
                fh.write(b"\n# tampered after signing\n")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for name in sorted(os.listdir(stage)):
                info = tar.gettarinfo(os.path.join(stage, name), arcname=name)
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                info.mtime = doc["issued_at"]
                info.mode = 0o644
                with open(os.path.join(stage, name), "rb") as fh:
                    tar.addfile(info, fh)
        data = buf.getvalue()
        with open(args.out, "wb") as fh:
            fh.write(data)
        print(json.dumps({"bundle": os.path.basename(args.out), "sha256": hashlib.sha256(data).hexdigest(),
                          "bytes": len(data), "release_seq": doc["release_seq"], "version": doc["version"],
                          "artifacts": len(doc["artifacts"]), "release_pub": pub.hex(),
                          "manifest_sha256": hashlib.sha256(open(os.path.join(stage, "manifest.json"), "rb").read()).hexdigest(),
                          "expires_at": doc["expires_at"], "tampered": args.tamper or None}, indent=1))
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
