#!/bin/sh
# AgentNode local worker -- Linux installer and updater (local-worker slice 2, Codex Q-LW2-0001 D7/D8/D10 as amended).
#
#   sh install.sh [--name NAME] [--concurrency 1] [--queue 2]     first install, then pairing in your browser
#   sh install.sh --update                                         a newer signed release, drained, health-checked
#
# Run as root from the unpacked release bundle (this directory). NOTHING here is downloaded or executed before the
# release signature is checked: OpenSSL 3 verifies the Ed25519 signature over the manifest with the public key
# below; then every file is checked by size and SHA-256 against the manifest, the release is checked for expiry, for
# this platform and against the root-owned monotonic floor; only then is anything installed -- from these files
# alone (no package index, no network resolution). Refusals are named:
#   release_verifier_unavailable  release_signature_invalid  release_manifest_invalid  release_expired
#   release_downgrade  release_platform_mismatch  release_floor_unreadable  artifact_hash_mismatch
#   runtime-absent  podman_too_old  cgroup_controllers_missing  subid_unavailable  runtime_identity_unsafe
#   state_permissions_unsafe  update_rolled_back
#
# What it makes: user agentnode-agent (no login, its own subordinate id range, a lingering user manager for rootless
# podman); /opt/agentnode-agent/releases/<seq> (root-owned, read-only) and the `current` link; /etc/agentnode-agent
# (pins.json from the signed manifest); /var/lib/agentnode-agent (0700, the agent's key, certificate, pair key,
# journal); /var/lib/agentnode-agent-floor (root's time floor for its identity); /var/lib/agentnode-agent-release
# (root's release floor); units agentnode-agent.service and agentnode-agent-floor.{service,timer}.
# It opens no port. Remove everything with uninstall.sh.
set -eu
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
HERE=$(cd "$(dirname "$0")" && pwd)
U=agentnode-agent
OPT=/opt/agentnode-agent
ETC=/etc/agentnode-agent
STATE=/var/lib/agentnode-agent
FLOORDIR=/var/lib/agentnode-agent-floor
RELFLOOR=/var/lib/agentnode-agent-release/floor.json
UNIT=/etc/systemd/system/agentnode-agent.service
PLATFORM=linux-x86_64
NAME=worker; CONC=1; QUEUE=2; MODE=install
while [ $# -gt 0 ]; do
    case "$1" in
        --name) NAME="$2"; shift 2;; --concurrency) CONC="$2"; shift 2;; --queue) QUEUE="$2"; shift 2;;
        --update) MODE=update; shift;;
        *) echo "unknown argument $1" >&2; exit 2;;
    esac
done
refuse() { echo "REFUSED $1${2:+: $2}" >&2; exit 2; }
[ "$(id -u)" = 0 ] || refuse not_root "run as root"

# ---- 1. the signature, before anything else ---------------------------------------------------------------------
RELEASE_PUB_PEM='@RELEASE_PUB_PEM@'
command -v openssl >/dev/null 2>&1 || refuse release_verifier_unavailable "openssl is not installed"
case "$(openssl version 2>/dev/null)" in OpenSSL\ 3.*) ;; *) refuse release_verifier_unavailable "OpenSSL 3 is required";; esac
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
printf '%s\n' "$RELEASE_PUB_PEM" > "$WORK/release-pub.pem"
printf 'agentnode-worker-release/1\n' > "$WORK/signed.bin"
cat "$HERE/manifest.json" >> "$WORK/signed.bin"
openssl pkeyutl -verify -pubin -inkey "$WORK/release-pub.pem" -rawin -in "$WORK/signed.bin" \
    -sigfile "$HERE/manifest.sig" >/dev/null 2>&1 || refuse release_signature_invalid
echo "  release signature verified"

# ---- 2. the manifest, every file, the floor -- with the system interpreter, isolated, reading only data ----------
command -v python3 >/dev/null 2>&1 || refuse release_platform_mismatch "python3 (3.12) is required"
python3 -I - "$HERE" "$PLATFORM" "$RELFLOOR" "$WORK" <<'PY' || exit 2
import hashlib, json, os, sys, time
here, platform, floor_path, work = sys.argv[1:5]
def refuse(code, detail=""):
    print("REFUSED %s%s" % (code, (": " + detail) if detail else ""), file=sys.stderr); sys.exit(2)
raw = open(os.path.join(here, "manifest.json"), "rb").read()
try:
    doc = json.loads(raw.decode("ascii"))
except Exception:
    refuse("release_manifest_invalid", "not ASCII JSON")
if json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") != raw:
    refuse("release_manifest_invalid", "not canonical")
keys = {"schema", "channel", "release_seq", "min_release_seq", "version", "git_commit", "issued_at", "expires_at",
        "compat", "pins", "artifacts"}
if not isinstance(doc, dict) or set(doc) != keys or doc["schema"] != "agentnode-worker-release/1":
    refuse("release_manifest_invalid", "shape")
now = time.time()
if not 0 < doc["expires_at"] - doc["issued_at"] <= 30 * 86400:
    refuse("release_manifest_invalid", "lifetime")
if now >= doc["expires_at"] or now < doc["issued_at"] - 300:
    refuse("release_expired")
floor = 0
if os.path.exists(floor_path):
    try:
        st = os.stat(floor_path)
        if st.st_uid != 0 or st.st_mode & 0o022:
            raise ValueError("owner")
        floor = int(json.load(open(floor_path))["release_seq"])
    except Exception:
        refuse("release_floor_unreadable")
if doc["release_seq"] < floor:
    refuse("release_downgrade", "%d is below the installed floor %d" % (doc["release_seq"], floor))
mine = [a for a in doc["artifacts"] if a["platform"] in (platform, "any")]
if not any(a["platform"] == platform for a in mine):
    refuse("release_platform_mismatch", platform)
for a in mine:
    p = os.path.join(here, a["name"])
    if "/" in a["name"] or not os.path.isfile(p) or os.path.getsize(p) != a["size"]:
        refuse("artifact_hash_mismatch", a["name"])
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != a["sha256"]:
        refuse("artifact_hash_mismatch", a["name"])
wheels = [a["name"] for a in mine if a["kind"] == "wheel"]
sdk = [a for a in mine if a["kind"] == "wheel" and a["name"].startswith("agentnode_sdk-")]
if len(sdk) != 1:
    refuse("release_manifest_invalid", "exactly one agentnode_sdk wheel")
out = {"seq": doc["release_seq"], "version": doc["version"], "commit": doc["git_commit"], "wheels": wheels,
       "sdk_wheel": sdk[0]["name"], "sdk_sha256": sdk[0]["sha256"], "compat": doc["compat"]}
open(os.path.join(work, "release.json"), "w").write(json.dumps(out))
open(os.path.join(work, "pins.json"), "w").write(json.dumps(doc["pins"], sort_keys=True, indent=1) + "\n")
print("  manifest: release %d (%s), %d files verified" % (doc["release_seq"], doc["version"], len(mine)))
PY
jq_() { python3 -I -c "import json,sys; print(json.load(open(sys.argv[1]))$2)" "$1"; }
SEQ=$(jq_ "$WORK/release.json" '["seq"]')
COMMIT=$(jq_ "$WORK/release.json" '["commit"]')
SDKWHEEL=$(jq_ "$WORK/release.json" '["sdk_wheel"]')
SDKSHA=$(jq_ "$WORK/release.json" '["sdk_sha256"]')
PODMAN_MIN=$(jq_ "$WORK/release.json" '["compat"]["podman_min"]')
IMAGE=$(jq_ "$WORK/release.json" '["compat"]["sandbox_image"]')

# ---- 3. what this machine must already be, BEFORE anything is changed --------------------------------------------
command -v podman >/dev/null 2>&1 || refuse runtime-absent "podman is not installed"
PV=$(podman --version 2>/dev/null | sed -n 's/^podman version \([0-9][0-9.]*\).*/\1/p')
python3 -I -c "import sys; a=[int(x) for x in sys.argv[1].split('.')]; b=[int(x) for x in sys.argv[2].split('.')]; sys.exit(0 if a>=b else 1)" \
    "${PV:-0.0.0}" "$PODMAN_MIN" || refuse podman_too_old "podman ${PV:-unknown} < $PODMAN_MIN"
for c in cpu memory pids; do
    grep -qw "$c" /sys/fs/cgroup/cgroup.controllers 2>/dev/null || refuse cgroup_controllers_missing "$c"
done
for h in newuidmap newgidmap; do
    P=$(command -v $h) || refuse subid_unavailable "$h is missing"
    O=$(stat -c '%U' "$P"); M=$(stat -c '%a' "$P")
    [ "$O" = root ] || refuse runtime_identity_unsafe "$P is not root's"
    case "$M" in *[2367]?|*?[2367]) refuse runtime_identity_unsafe "$P is writable by others";; esac
done
command -v systemctl >/dev/null 2>&1 || refuse release_platform_mismatch "systemd is required"
PYV=$(python3 -I -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')
case "$PYV" in 3.12.*) ;; *) refuse release_platform_mismatch "python 3.12 is required, this is $PYV";; esac

# ---- 4. the release, into its own directory, from these files only ------------------------------------------------
REL=$OPT/releases/$SEQ
if [ -e "$REL" ]; then rm -rf "$REL"; fi
install -d -o root -g root -m 0755 "$OPT" "$OPT/releases"
python3 -I -m venv "$REL/venv"
for w in $(python3 -I -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))["wheels"]))' "$WORK/release.json"); do
    "$REL/venv/bin/pip" install --quiet --no-index --no-deps --disable-pip-version-check "$HERE/$w"
done
"$REL/venv/bin/pip" check --disable-pip-version-check >/dev/null || refuse release_manifest_invalid "the wheel set is incomplete"
DIST=$(ls -d "$REL"/venv/lib/python3.12/site-packages/agentnode_sdk-*.dist-info)
printf '%s\n' "$SDKSHA" > "$DIST/AGENTNODE_ARTEFACT"
cp "$HERE/manifest.json" "$HERE/manifest.sig" "$REL/"
chmod -R a+rX,go-w "$REL"
echo "  installed release $SEQ into $REL"

# ---- 5. the account, its state, its pins -----------------------------------------------------------------------
if ! id "$U" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "$STATE" --shell /sbin/nologin "$U"
    START=$(awk -F: 'BEGIN{m=100000} {e=$2+$3; if (e>m) m=e} END{print m}' /etc/subuid /etc/subgid)
    START=$(( (START / 65536 + 1) * 65536 ))
    usermod --add-subuids "$START-$((START + 65535))" --add-subgids "$START-$((START + 65535))" "$U"
fi
grep -q "^$U:" /etc/subuid && grep -q "^$U:" /etc/subgid || refuse subid_unavailable "no subordinate ids for $U"
UIDN=$(id -u "$U")
loginctl enable-linger "$U"
systemctl start "user@$UIDN.service"
install -d -o "$U" -g "$U" -m 0700 "$STATE" "$STATE/tls" "$STATE/journal" "$STATE/tmp" "$STATE/.config" \
    "$STATE/.config/containers"
printf '[containers]\ndefault_sysctls = []\n' > "$STATE/.config/containers/containers.conf"
chown "$U:$U" "$STATE/.config/containers/containers.conf"
for d in "$STATE" "$STATE/tls" "$STATE/journal"; do
    [ "$(stat -c '%U %a' "$d")" = "$U 700" ] || refuse state_permissions_unsafe "$d"
done
install -d -o root -g root -m 0755 "$ETC" "$FLOORDIR" "$(dirname "$RELFLOOR")"
install -o root -g root -m 0644 "$WORK/pins.json" "$ETC/pins.json"
install -o root -g root -m 0644 "$WORK/release-pub.pem" "$ETC/release-pub.pem"

asuser() { ( cd "$STATE" && runuser -u "$U" -- env HOME="$STATE" XDG_RUNTIME_DIR="/run/user/$UIDN" TMPDIR="$STATE/tmp" "$@" ); }

# PRIVATE TEST STAND ONLY (Codex D14): AGENTNODE_STAND_HOSTS names a root-owned hosts file that maps the public name to
# the stand's private address for the AGENT ALONE -- pairing runs in its own mount namespace with that file over
# /etc/hosts, and the unit gets the same file read-only. The host's /etc/hosts is never changed. Unset in production.
STAND_HOSTS=${AGENTNODE_STAND_HOSTS:-}
if [ -n "$STAND_HOSTS" ]; then
    [ "$(stat -c '%U %a' "$STAND_HOSTS" 2>/dev/null)" = "root 644" ] || refuse release_platform_mismatch "stand hosts file"
    echo "  TEST STAND: the agent resolves through $STAND_HOSTS (its namespace only)"
fi
asuser_pairing() {
    if [ -n "$STAND_HOSTS" ]; then
        unshare -m sh -c 'mount --bind "$0" /etc/hosts && shift && cd "$1" && shift && exec runuser -u "$@"' \
            "$STAND_HOSTS" x "$STATE" "$U" -- env HOME="$STATE" XDG_RUNTIME_DIR="/run/user/$UIDN" TMPDIR="$STATE/tmp" "$@"
    else
        asuser "$@"
    fi
}

switch_to() {   # atomically point `current` at a release directory
    ln -sfn "$1" "$OPT/current.next"
    mv -T "$OPT/current.next" "$OPT/current"
}
raise_floor() {
    python3 -I -c 'import json,os,sys,time; p=sys.argv[1]; t=p+".tmp"; open(t,"w").write(json.dumps({"release_seq": int(sys.argv[2]), "at": time.time()})); os.chmod(t,0o644); os.replace(t,p)' "$RELFLOOR" "$SEQ"
}
write_unit() {
    python3 -I - "$STATE/tls/target.json" "$UIDN" "$UNIT" "$ETC/pins.json" <<'PY'
import json, sys
t = json.load(open(sys.argv[1])); uid = sys.argv[2]; unit = sys.argv[3]; pins = sys.argv[4]
S = "/var/lib/agentnode-agent"
args = ["/opt/agentnode-agent/current/venv/bin/agentnode", "worker", "connect", "--control", t["control"],
        "--pins", pins, "--target-json", S + "/tls/target.json", "--max-runs", str(int(t["limits"]["concurrency"])),
        "--keyring", S + "/tls/keyring.json", "--journal", S + "/journal", "--tls-dir", S + "/tls",
        "--trust", S + "/tls/ca.pem", "--deployment", t["deployment"], "--accept-gateway", t["accept_gateway"],
        "--revocation-list", S + "/tls/revoked.crl", "--tombstones", S + "/tls/revoked-identities.json",
        "--floor", "/var/lib/agentnode-agent-floor/worker.floor"]
for a in args:
    if any(c in a for c in " \t\n\"'\\$%;"):
        sys.exit("unsafe unit argument")
open(unit, "w").write("""[Unit]
Description=AgentNode local worker (dials mcp.agentnode.net:443, listens nowhere)
After=network-online.target user@{uid}.service
Wants=network-online.target
Requires=user@{uid}.service

[Service]
User=agentnode-agent
Group=agentnode-agent
WorkingDirectory={S}
Environment=HOME={S} XDG_RUNTIME_DIR=/run/user/{uid} TMPDIR={S}/tmp PYTHONUNBUFFERED=1
ExecStart={cmd}
Restart=on-failure
RestartSec=10
KillSignal=SIGTERM
TimeoutStopSec=170
NoNewPrivileges=@NNP@
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths={S} /run/user/{uid}
InaccessiblePaths=-/root -/etc/agentnode -/var/lib/agentnode-worker -/var/lib/agentnode-mcp
CapabilityBoundingSet=
AmbientCapabilities=
RestrictSUIDSGID=no
LockPersonality=yes
UMask=0077

[Install]
WantedBy=multi-user.target
""".format(uid=uid, S=S, cmd=" ".join(args)))
PY
    sed -i "s/@NNP@/${NNP:-no}/" "$UNIT"
    chmod 0644 "$UNIT"
}
health() {   # 0 when the agent completed an inner handshake (or was refused by NAME) after $1, within 60 s
    python3 -I - "$STATE/status.json" "$1" <<'PY'
import json, sys, time
path, since = sys.argv[1], float(sys.argv[2])
named = ("edge_not_parkable", "edge_authority_unavailable")
end = time.time() + 60
while time.time() < end:
    try:
        st = json.load(open(path))
        if st.get("started_at", 0) >= since and (st.get("last_admitted_at", 0) >= since or st.get("last_refusal") in named):
            print("  health: admitted" if st.get("last_admitted_at", 0) >= since else "  health: refused by name: " + st["last_refusal"])
            sys.exit(0)
    except (OSError, ValueError):
        pass
    time.sleep(2)
sys.exit(1)
PY
}

# ---- 6a. update: drain, switch, health, roll back on failure ---------------------------------------------------
if [ "$MODE" = update ]; then
    [ -f "$STATE/tls/target.json" ] || refuse release_manifest_invalid "nothing to update: not paired"
    PREV=$(readlink "$OPT/current" || true)
    raise_floor
    systemctl stop agentnode-agent.service          # SIGTERM: drain (no new run; running ones end within bounds)
    switch_to "$REL"
    NOW=$(date +%s)
    systemctl start agentnode-agent.service
    if health "$NOW"; then
        echo "  updated to release $SEQ"
        exit 0
    fi
    echo "  the new release did not come up healthy; rolling back to $PREV" >&2
    systemctl stop agentnode-agent.service
    [ -n "$PREV" ] && switch_to "$PREV"
    systemctl start agentnode-agent.service
    printf '{"outcome":"update_rolled_back","from":%s,"to":"%s","at":%s}\n' "$SEQ" "$PREV" "$(date +%s)" \
        > "$(dirname "$RELFLOOR")/last-update.json"
    echo "REFUSED update_rolled_back" >&2
    exit 3
fi

# ---- 6b. first install: pair, root's floor, the pin, the runtime, the unit -----------------------------------------
switch_to "$REL"
raise_floor
if [ ! -f "$STATE/tls/target.json" ]; then
    asuser_pairing "$OPT/current/venv/bin/agentnode" worker pair --pins "$ETC/pins.json" --state-dir "$STATE/tls" \
        --name "$NAME" --concurrency "$CONC" --queue "$QUEUE" || refuse pairing_failed
fi
"$OPT/current/venv/bin/agentnode" pki floor init --role worker --certificate "$STATE/tls/cert.pem" \
    --anchor "$STATE/tls/ca.pem" --floor-dir "$FLOORDIR" >/dev/null 2>&1 || true
"$OPT/current/venv/bin/agentnode" pki floor advance --role worker --certificate "$STATE/tls/cert.pem" \
    --anchor "$STATE/tls/ca.pem" --revocation-list "$STATE/tls/revoked.crl" --floor-dir "$FLOORDIR" >/dev/null
python3 -I -c 'import json,os,sys; p=sys.argv[1]; open(p,"w").write(json.dumps({"python_version": sys.argv[2], "artefact_sha256": sys.argv[3], "commit": sys.argv[4], "build_id": "managed-%s+%s" % (sys.argv[4][:12], sys.argv[3][:12])}, indent=1, sort_keys=True)+"\n")' \
    "$STATE/tls/runtime-pin.json" "$PYV" "$SDKSHA" "$COMMIT"
chown root:"$U" "$STATE/tls/runtime-pin.json"; chmod 0640 "$STATE/tls/runtime-pin.json"
asuser /usr/bin/podman system migrate >/dev/null 2>&1 || true
asuser /usr/bin/podman image exists "$IMAGE" || asuser /usr/bin/podman pull --quiet "$IMAGE" >/dev/null \
    || refuse runtime-absent "the pinned sandbox image could not be fetched"
asuser /usr/bin/podman run --rm --network none --user 1000:1000 "$IMAGE" /bin/true \
    || refuse runtime-absent "a rootless container does not start for $U"
cat > /etc/systemd/system/agentnode-agent-floor.service <<EOF
[Unit]
Description=AgentNode local worker: root's time floor for its identity
[Service]
Type=oneshot
ExecStart=$OPT/current/venv/bin/agentnode pki floor advance --role worker --certificate $STATE/tls/cert.pem --anchor $STATE/tls/ca.pem --revocation-list $STATE/tls/revoked.crl --floor-dir $FLOORDIR
EOF
cat > /etc/systemd/system/agentnode-agent-floor.timer <<EOF
[Unit]
Description=AgentNode local worker: advance the time floor
[Timer]
OnBootSec=30s
OnUnitActiveSec=10min
[Install]
WantedBy=timers.target
EOF
NNP=${AGENTNODE_AGENT_NNP:-yes} write_unit
if [ -n "$STAND_HOSTS" ]; then
    install -d -m 0755 /etc/systemd/system/agentnode-agent.service.d
    printf '[Service]\n# PRIVATE TEST STAND ONLY: the public name resolves to the stand for this unit alone\nBindReadOnlyPaths=%s:/etc/hosts\n' \
        "$STAND_HOSTS" > /etc/systemd/system/agentnode-agent.service.d/stand-hosts.conf
fi
chmod 0644 /etc/systemd/system/agentnode-agent-floor.service /etc/systemd/system/agentnode-agent-floor.timer
systemctl daemon-reload
systemctl enable --now agentnode-agent-floor.timer >/dev/null
NOW=$(date +%s)
systemctl enable --now agentnode-agent.service >/dev/null
health "$NOW" || { echo "  the agent did not reach the control plane within 60 s; see: journalctl -u agentnode-agent" >&2; exit 4; }
echo "  installed and running. It listens nowhere; it dials $(jq_ "$ETC/pins.json" '["control_url"]')."
echo "  Remove everything with: sh $HERE/uninstall.sh [--revoke]"
