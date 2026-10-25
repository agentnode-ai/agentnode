#!/bin/sh
# Inside the freshly imported AgentNode WSL2 distribution (root), after Windows verified the release: the offline RPM
# set (podman >= 5.8.2 and what it needs), each RPM checked with `rpm -K` against Rocky's release key -- EXACTLY the
# pinned fingerprint -- before ANY is installed; nothing is fetched from a repository (Codex Q-LW2-0002 Q1).
set -eu
B=/opt/agentnode-agent-bundle
FPR=fc226859c0860bf0ddb95b085b106c736fedfc85
KEYFILE=/etc/pki/rpm-gpg/RPM-GPG-KEY-Rocky-10
refuse() { echo "REFUSED $1${2:+: $2}"; exit 2; }
[ -f "$KEYFILE" ] || refuse release_manifest_invalid "the image carries no Rocky release key"
rpm --import "$KEYFILE" 2>/dev/null || true
rpm -q gpg-pubkey --qf '%{VERSION}\n' | grep -qx "${FPR#????????????????????????????????}" \
    || refuse release_signature_invalid "the pinned Rocky key ($FPR) is not the one in this image"
n=0
for r in "$B"/*.rpm; do
    rpm -K "$r" | grep -q 'digests signatures OK' || refuse release_signature_invalid "$(basename "$r") fails rpm -K"
    n=$((n + 1))
done
echo "  rpm -K: $n package(s) signed by the pinned Rocky key"
rpm -Uvh --quiet --replacepkgs "$B"/*.rpm 2>&1 | tail -2 || true
for p in podman openssl crun; do rpm -q "$p" >/dev/null || refuse runtime-absent "$p did not install"; done
echo "  $(podman --version) installed offline; $(openssl version | cut -d' ' -f1-2)"
