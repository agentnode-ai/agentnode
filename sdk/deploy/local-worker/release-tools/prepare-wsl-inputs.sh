#!/bin/sh
# The WSL2 inputs of a Windows release (local-worker slice 2, Codex Q-LW2-0002 Q1), prepared once on a Rocky Linux 10
# build host, as root, in ONE directory of its own ($2, default /root/agentnode-wsl-inputs):
#   rootfs   the DATED Rocky 10 WSL Base image, its CHECKSUM and CHECKSUM.asc; the CHECKSUM's signature is verified
#            against Rocky's release key whose fingerprint is pinned here, and the image against the CHECKSUM -- any
#            mismatch stops here. Never a `.latest` name.
#   rpms     the RPM closure for podman (and what the agent needs) RELATIVE TO THAT IMAGE, resolved inside a container
#            made from the image -- root podman on a SEPARATE storage root, never the host's default storage -- from
#            the Rocky 10 BaseOS/AppStream repositories only; every RPM then checked with `rpm -K` against a PRIVATE
#            rpm database holding only Rocky's key file. One failure stops here.
#   clean    removes the directory and the separate storage
# build_bundle.py --windows <dir> checks the image against the CHECKSUM once more before it signs anything.
set -eu
CMD="$1"
W="${2:-/root/agentnode-wsl-inputs}"
IMG=Rocky-10-WSL-Base-10.2-20260525.0.x86_64.wsl
BASE=https://dl.rockylinux.org/pub/rocky/10/images/x86_64
KEY=/etc/pki/rpm-gpg/RPM-GPG-KEY-Rocky-10
FPR=FC226859C0860BF0DDB95B085B106C736FEDFC85
PODMAN="podman --root $W/storage --runroot $W/runroot --storage-driver overlay"
stop() { echo "STOPPED: $*"; exit 2; }
case "$CMD" in
rootfs)
    mkdir -p "$W" && cd "$W"
    for f in "$IMG" "$IMG.CHECKSUM" "$IMG.CHECKSUM.asc"; do [ -f "$f" ] || curl -fsSL -o "$f" "$BASE/$f"; done
    rm -rf gnupg; mkdir -m 0700 gnupg
    # the key file holds two keys: Rocky's RSA (v4) release key and a v6 key gpg 2.4 cannot parse. Import each block on
    # its own; the v4 key must be EXACTLY the pinned fingerprint.
    awk -v d="$W/gnupg" '/BEGIN PGP PUBLIC KEY BLOCK/ {n++} {print > (d "/block" n ".asc")}' "$KEY"
    for b in gnupg/block*.asc; do gpg --homedir gnupg --quiet --import "$b" 2>/dev/null || true; done
    gpg --homedir gnupg --with-colons --fingerprint 2>/dev/null | grep -q "^fpr:::::::::$FPR:" || stop "the pinned Rocky key is not in $KEY"
    echo "rocky release key pinned: $FPR (from $(rpm -qf $KEY))"
    gpg --homedir gnupg --status-fd 1 --verify "$IMG.CHECKSUM.asc" "$IMG.CHECKSUM" 2>/dev/null | grep -q "VALIDSIG $FPR" \
        || stop "the CHECKSUM is not signed by $FPR"
    echo "CHECKSUM signature: VALID by $FPR"
    sha256sum -c --strict "$IMG.CHECKSUM" >/dev/null 2>&1 || stop "$IMG is not the image its signed CHECKSUM names"
    [ "$(stat -c %s "$IMG")" = "$(sed -n "s/^# $IMG: \([0-9]*\) bytes\$/\1/p" "$IMG.CHECKSUM")" ] || stop "$IMG has another size"
    echo "rootfs sha256 $(sha256sum "$IMG" | cut -d' ' -f1) size $(stat -c %s "$IMG"): matches the signed CHECKSUM"
    ;;
rpms)
    cd "$W"
    [ -f "$IMG" ] || stop "run 'rootfs' first"
    sha256sum -c --strict "$IMG.CHECKSUM" >/dev/null 2>&1 || stop "$IMG changed since 'rootfs'"
    rm -rf rpms rpmdb; mkdir -p rpms
    $PODMAN import --quiet "$IMG" rocky-wsl-base:20260525 >/dev/null
    $PODMAN run --rm --network host -v "$W/rpms:/out:Z" rocky-wsl-base:20260525 sh -c '
        . /etc/os-release; echo "image: $PRETTY_NAME"
        dnf -q -y --setopt=install_weak_deps=False --disablerepo="*" --enablerepo=baseos --enablerepo=appstream \
            install --downloadonly --downloaddir=/out podman python3 python3-pip-wheel openssl shadow-utils libcap \
            procps-ng util-linux iproute crun 2>&1 | tail -3
        echo "resolved: $(ls /out | wc -l) rpm(s)"'
    rpm --dbpath "$W/rpmdb" --initdb
    rpm --dbpath "$W/rpmdb" --import "$KEY"
    n=0
    for r in rpms/*.rpm; do
        rpm --dbpath "$W/rpmdb" -K "$r" | grep -q 'digests signatures OK' || stop "$r fails rpm -K against the pinned key"
        n=$((n + 1))
    done
    [ "$n" -gt 0 ] || stop "no RPM was resolved"
    echo "rpm -K: $n checked against $FPR, all OK"
    ls rpms | sed 's/\.rpm$//' | sort > rpms.nevra
    ( cd rpms && sha256sum *.rpm ) > rpms.sha256
    echo "podman: $(grep '^podman-[0-9]' rpms.nevra)"
    $PODMAN rmi -a -f >/dev/null 2>&1 || true
    ;;
clean)
    $PODMAN rmi -a -f >/dev/null 2>&1 || true
    rm -rf "$W"
    echo "removed $W"
    ;;
*)
    stop "usage: prepare-wsl-inputs.sh rootfs|rpms|clean [dir]"
    ;;
esac
