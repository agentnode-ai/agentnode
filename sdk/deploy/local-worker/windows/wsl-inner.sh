#!/bin/sh
# Inside the freshly imported AgentNode WSL2 distribution (root), after Windows verified the release: the offline RPM
# set (podman >= 5.8.2 and what it needs), installed only after (Codex Q-LW2-0002 Q1):
#   1. the manifest copied in here is EXACTLY the bytes Windows verified (their SHA-256 is this script's argument) --
#      nothing that changed between Windows' check and the copy is believed;
#   2. every RPM here is one the manifest names, with its size and SHA-256, and every RPM it names is here;
#   3. every RPM passes `rpm -K` against a PRIVATE rpm database that holds only the image's Rocky key file, whose v4 key
#      is the pinned fingerprint -- not whatever keys the image's own database carries.
# Nothing is fetched from a repository. `--verify-only` stops after 3 (the build host's test of this script).
#   wsl-inner.sh <manifest-sha256> [--verify-only] [bundle-dir]
set -eu
WANT="${1:-}"
MODE="${2:-}"
B="${3:-/opt/agentnode-agent-bundle}"
FPR=fc226859c0860bf0ddb95b085b106c736fedfc85
KEYFILE=/etc/pki/rpm-gpg/RPM-GPG-KEY-Rocky-10
refuse() { echo "REFUSED $1${2:+: $2}"; exit 2; }
case "$WANT" in
    [0-9a-f]*) [ "${#WANT}" -eq 64 ] || refuse release_manifest_invalid "the verified manifest's SHA-256 is 64 hex" ;;
    *) refuse release_manifest_invalid "the verified manifest's SHA-256 was not passed" ;;
esac
[ "$(sha256sum "$B/manifest.json" | cut -d' ' -f1)" = "$WANT" ] \
    || refuse release_signature_invalid "the manifest in the distribution is not the one Windows verified"
python3 -I - "$B" <<'PY' || exit 2
import hashlib, json, os, sys
b = sys.argv[1]
doc = json.load(open(os.path.join(b, "manifest.json"), encoding="ascii"))
named = {a["name"]: a for a in doc["artifacts"] if a["kind"] == "rpm" and a["platform"] == "wsl-rocky10-x86_64"}
here = sorted(n for n in os.listdir(b) if n.endswith(".rpm"))
if not named or sorted(named) != here:
    print("REFUSED artifact_hash_mismatch: the RPMs here are not exactly the ones the manifest names")
    sys.exit(2)
for n in here:
    p = os.path.join(b, n)
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    if os.path.getsize(p) != named[n]["size"] or h.hexdigest() != named[n]["sha256"]:
        print("REFUSED artifact_hash_mismatch: " + n)
        sys.exit(2)
print("  %d RPM(s) match the verified manifest" % len(here))
PY
[ -f "$KEYFILE" ] || refuse release_manifest_invalid "the image carries no Rocky release key"
DB=$(mktemp -d)
trap 'rm -rf "$DB"' EXIT
rpm --dbpath "$DB" --initdb
rpm --dbpath "$DB" --import "$KEYFILE" 2>/dev/null || true
rpm --dbpath "$DB" -q gpg-pubkey --qf '%{VERSION}\n' | grep -qx "${FPR#????????????????????????????????}" \
    || refuse release_signature_invalid "the pinned Rocky key ($FPR) is not the one in this image"
n=0
for r in "$B"/*.rpm; do
    rpm --dbpath "$DB" -K "$r" | grep -q 'digests signatures OK' || refuse release_signature_invalid "$(basename "$r") fails rpm -K"
    n=$((n + 1))
done
echo "  rpm -K: $n package(s) signed by the pinned Rocky key"
[ "$MODE" = "--verify-only" ] && { echo "  verified only; nothing installed"; exit 0; }
rpm --import "$KEYFILE" 2>/dev/null || true
rpm -Uvh --quiet --replacepkgs "$B"/*.rpm || refuse runtime-absent "the offline RPM set did not install"
for p in podman openssl crun; do rpm -q "$p" >/dev/null || refuse runtime-absent "$p did not install"; done
echo "  $(podman --version) installed offline; $(openssl version | cut -d' ' -f1-2)"
