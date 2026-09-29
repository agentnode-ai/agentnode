#!/bin/bash
# The worker half of a two-machine deployment. Run install-control-plane.sh on the other machine
# FIRST: this host is enrolled by that machine's CA and cannot be set up before one exists.
#
# WHAT THIS MACHINE BECOMES
#
#   One account, agentnode-worker, which drives rootless podman as itself and holds: its own
#   certificate and key, the deployment's public CA certificate, the two signed lists, the key
#   for ONE pair, and its journal. It holds no client token, no account list, no signing
#   identity and no CA -- and this script refuses to continue if it finds control-plane state
#   here, because that is how a second machine stops being worth having.
#
# WHAT IT DOES NOT DO
#
#   It opens no port and it writes no firewall rule. It checks that the address it is about to
#   bind is a private one that exists on this machine, prints the rule that must exist, and
#   leaves applying it to whoever is responsible for this network.
#
# Re-runnable. It stops, on purpose, at the point where a human has to carry something between
# the two machines, and continues from there when run again.

set -euo pipefail

WORKER_USER=agentnode-worker
PREFIX=/opt/agentnode
CONF=/etc/agentnode
TRUST=/etc/agentnode/trust
HOME_DIR=/var/lib/agentnode-worker
TLS_DIR=/var/lib/agentnode-worker/tls
JOURNAL=/var/lib/agentnode-worker/journal
FLOOR=/var/lib/agentnode-floor
WHEEL="${AGENTNODE_WHEEL:-}"
LISTEN="${AGENTNODE_LISTEN:-}"
DEPLOYMENT="${AGENTNODE_DEPLOYMENT:-}"
GATEWAY_INSTANCE="${AGENTNODE_GATEWAY_INSTANCE:-g1}"

say() { printf '\n== %s\n' "$1"; }
ok()  { printf '   %s\n' "$1"; }
die() { printf '\n!! %s\n' "$1" >&2; exit 1; }
stop_here() { printf '\n-- %s\n\n' "$1"; exit 0; }

[ "$(id -u)" = "0" ] || die "this makes system accounts and units, so it needs root"
command -v systemctl >/dev/null || die "there is no systemd here"
command -v podman    >/dev/null || die "podman is not installed, and this worker runs rootless podman"
command -v python3   >/dev/null || die "python3 is not installed"
[ -n "$LISTEN" ]     || die "set AGENTNODE_LISTEN to this host's PRIVATE address, e.g. tcps://10.0.1.5:8443"
[ -n "$DEPLOYMENT" ] || die "set AGENTNODE_DEPLOYMENT to the same deployment id the control plane was given"

HERE="$(cd "$(dirname "$0")" && pwd)"

# WHERE A UNIT FILE IS depends on what this script is running from: ./unit/ inside an artefact,
# beside this script or one directory up in the repository. Guessing one of them is what made
# both artefacts unable to install themselves, so this asks. See the same function in
# install-control-plane.sh.
unit_file() {
  local name candidate
  for name in "$@"; do
    for candidate in "$HERE/unit/$name" "$HERE/$name" "$(dirname "$HERE")/$name"; do
      if [ -f "$candidate" ]; then printf '%s\n' "$candidate"; return 0; fi
    done
  done
  die "no unit file named $* is in this artefact or beside this script"
}

cd /

# ---------------------------------------------------------------------------------------------
say "the address this worker will bind"

case "$LISTEN" in
  tcps://*) : ;;
  *) die "a worker on its own machine is reached over mutual TLS and nothing else: $LISTEN" ;;
esac
BIND="${LISTEN#tcps://}"; BIND_IP="${BIND%:*}"; BIND_PORT="${BIND##*:}"
case "$BIND_IP" in
  0.0.0.0|"[::]"|"*")
    die "binding every interface puts this worker on whatever network this machine is on, which on
    a cloud host means the internet. The product refuses this as well; it is refused here so the
    refusal is not a surprise at first start." ;;
  127.*|localhost|"[::1]")
    die "$BIND_IP is this machine talking to itself, and the control plane is not on this machine" ;;
esac
ip -o addr show 2>/dev/null | grep -qw "$BIND_IP" \
  || die "no interface on this host has the address $BIND_IP. A worker that cannot bind its own
    address fails at start; better to find out now."
case "$BIND_IP" in
  10.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*) ok "$BIND_IP is a private address" ;;
  *) die "$BIND_IP is not in a private range. The worker is reached over the private network
    between the two hosts; a public address here is an open door with a certificate on it." ;;
esac

# ---------------------------------------------------------------------------------------------
say "no control-plane state on this machine"

for forbidden in /var/lib/agentnode "$CONF/ca"; do
  [ -e "$forbidden" ] && die "$forbidden exists on the worker host. Either the control plane was
    installed here too -- in which case this is single-host and should use deploy/ -- or
    something copied state that must not leave the other machine."
done
ok "no gateway state, no CA"

# ---------------------------------------------------------------------------------------------
say "the account that runs foreign code"

id "$WORKER_USER" >/dev/null 2>&1 || useradd --system --create-home --home-dir "$HOME_DIR" \
    --shell /usr/sbin/nologin --comment "AgentNode sandbox worker" "$WORKER_USER"
for dangerous in docker wheel sudo root adm; do
  if id -nG "$WORKER_USER" 2>/dev/null | tr ' ' '\n' | grep -qx "$dangerous"; then
    die "$WORKER_USER is in the $dangerous group, which is root-equivalent here. An escape from a
    sandbox would then own this machine outright."
  fi
done
ok "$WORKER_USER uid $(id -u "$WORKER_USER"), in no group that reaches root"

if ! grep -q "^${WORKER_USER}:" /etc/subuid 2>/dev/null; then
  usermod --add-subuids 400000-465535 --add-subgids 400000-465535 "$WORKER_USER"
fi
ok "subordinate ids: $(grep "^${WORKER_USER}:" /etc/subuid)"

# Without a session for this account podman falls back to cgroupfs and SILENTLY DOES NOT APPLY
# the memory ceiling. The worker re-checks by hitting a ceiling before it serves, so a mistake
# here fails closed -- but it fails closed at 3am rather than here.
loginctl enable-linger "$WORKER_USER"
WORKER_UID="$(id -u "$WORKER_USER")"
for _ in 1 2 3 4 5 6 7 8 9 10; do [ -d "/run/user/$WORKER_UID" ] && break; sleep 1; done
[ -d "/run/user/$WORKER_UID" ] || die "no session directory for $WORKER_USER; its ceilings would not bind"
ok "session at /run/user/$WORKER_UID"

# ---------------------------------------------------------------------------------------------
say "the code"

if [ -n "$WHEEL" ]; then
  [ -f "$WHEEL" ] || die "no wheel at $WHEEL"
  [ -d "$PREFIX/venv" ] || python3 -m venv "$PREFIX/venv"
  "$PREFIX/venv/bin/pip" install --quiet --force-reinstall --no-deps "$WHEEL"
  "$PREFIX/venv/bin/pip" install --quiet "$WHEEL"
  ok "installed $(basename "$WHEEL")"
else
  [ -x "$PREFIX/venv/bin/agentnode" ] || die "no wheel given and nothing installed at $PREFIX"
  ok "keeping what is already installed"
fi
ok "version: $("$PREFIX/venv/bin/agentnode" --version 2>/dev/null || echo unknown)"
chmod 0755 "$PREFIX" "$PREFIX/venv"
# ONE WHEEL, TWO ROLES. The control plane's modules are on this disk and nothing starts them.
# `agentnode_sdk/roles.py` names which modules belong to which role and
# tests/test_the_two_roles_are_separable.py fails if the worker's start path reaches the other
# role's. Two wheels would make that a packaging fact rather than an import-graph one, and that
# has not been done.

# ---------------------------------------------------------------------------------------------
say "directories"

install -d -o root         -g root         -m 0755 "$CONF" "$TRUST"
install -d -o "$WORKER_USER" -g "$WORKER_USER" -m 0700 "$HOME_DIR" "$HOME_DIR/tmp" "$TLS_DIR" "$JOURNAL"
ok "$HOME_DIR is 0700 to $WORKER_USER; its journal and its key are inside it"

# ---------------------------------------------------------------------------------------------
say "what had to be carried from the control plane"

missing=""
for needed in "$TRUST/ca.pem" "$TRUST/revoked.crl" "$TRUST/revoked-identities.json" \
              "$CONF/pair-keys.json"; do
  [ -f "$needed" ] || missing="$missing\n     $needed"
done
if [ -n "$missing" ]; then
  printf '\n   Not here yet:'; printf "$missing\n"
  stop_here "Copy those from the control plane and run this again. They are signed, so the copy
   does not have to be trusted -- but it does have to be FRESH: both lists expire, and a list too
   old to believe is a refusal rather than an empty list."
fi
chown root:"$WORKER_USER" "$CONF/pair-keys.json"; chmod 0640 "$CONF/pair-keys.json"
chmod 0644 "$TRUST/ca.pem" "$TRUST/revoked.crl" "$TRUST/revoked-identities.json"
ok "trust anchor, revocation list, withdrawn-identity list, pair key"

# ---------------------------------------------------------------------------------------------
say "this worker's own certificate"

if [ ! -f "$TLS_DIR/cert.pem" ]; then
  if [ ! -f "$TLS_DIR/secret" ]; then
    stop_here "This worker has no certificate and no enrolment secret.
   On the CONTROL PLANE, as root:
     install -d -m 0700 /var/lib/agentnode/enrolment/<instance>
     agentnode pki add --role worker --instance <instance> --account root \\
                       --tls-dir /var/lib/agentnode/enrolment/<instance>
   Copy the file it writes -- 'secret' -- to $TLS_DIR here, owned by $WORKER_USER, mode 0400,
   and run this script again. It is single-use and it is the only thing that has to travel
   secretly in either direction."
  fi
  chown "$WORKER_USER":"$WORKER_USER" "$TLS_DIR/secret"; chmod 0400 "$TLS_DIR/secret"
  # As the account: the private key is made where it will stay, by whoever will read it.
  runuser -u "$WORKER_USER" -- "$PREFIX/venv/bin/agentnode" pki request --tls-dir "$TLS_DIR"
  stop_here "A request is at $TLS_DIR/request.json.
   Carry it to the CONTROL PLANE and run, as root:
     agentnode pki enroll --request <the copy>
   Then bring back the cert.pem it writes beside the request, put it in $TLS_DIR owned by
   $WORKER_USER mode 0444, and run this script again. The request contains the one-shot secret in
   clear, so delete every copy of it on both machines once the certificate exists."
fi
chown "$WORKER_USER":"$WORKER_USER" "$TLS_DIR/cert.pem"; chmod 0444 "$TLS_DIR/cert.pem"
# The residues. `request.json` holds the secret in clear and neither file is any use once the
# certificate is here. The product leaves both behind; this removes them, and that omission is
# recorded as an open item rather than hidden by this line.
rm -f "$TLS_DIR/secret" "$TLS_DIR/request.json"
ok "certificate in place, enrolment residues deleted"

# ---------------------------------------------------------------------------------------------
say "this machine's own time floor"

# Keyed to THIS kernel's boot id and THIS machine's monotonic clock, so a floor written on the
# control plane is unusable here. This is the step most often forgotten and the worker refuses
# every connection without it.
#
# AND IT IS MADE HERE WITHOUT A CA. It used to call `agentnode pki floor init`, which is the
# issuer's, and the issuer opens /etc/agentnode/ca/ca.key -- the one file a worker must never
# hold. On one host that worked because the CA was on the same disk; on a real worker host the
# install simply stopped. The bound a floor starts at is the trust ANCHOR's notBefore, which is
# public and already here, and whose floor it is comes from this worker's own certificate.
if [ ! -f "$FLOOR/worker.floor" ]; then
  "$PREFIX/venv/bin/agentnode" pki floor init --role worker \
      --certificate "$TLS_DIR/cert.pem" --anchor "$TRUST/ca.pem" --floor-dir "$FLOOR" \
      || die "this worker could not set its time floor up, so it would refuse every connection."
else
  ok "the worker's floor is already set up; left as it stands"
fi
install -m 0644 "$(unit_file agentnode-floor-advance.service)" /etc/systemd/system/
install -m 0644 "$(unit_file agentnode-floor-advance.timer)"   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now agentnode-floor-advance.timer >/dev/null
ok "$FLOOR/worker.floor, kept by this host's own timer -- not the issuer's run"

# ---------------------------------------------------------------------------------------------
say "the image jobs run in"

IMAGE="$("$PREFIX/venv/bin/python3" -c 'from agentnode_sdk.sandbox.container_backend import _BASE_IMAGE; print(_BASE_IMAGE)')"
ok "pinned by digest: ${IMAGE#*@}"
if ! runuser -u "$WORKER_USER" -- env XDG_RUNTIME_DIR="/run/user/$WORKER_UID" HOME="$HOME_DIR" \
     TMPDIR="$HOME_DIR/tmp" podman image exists "$IMAGE" 2>/dev/null; then
  runuser -u "$WORKER_USER" -- env XDG_RUNTIME_DIR="/run/user/$WORKER_UID" HOME="$HOME_DIR" \
     TMPDIR="$HOME_DIR/tmp" podman pull --quiet "$IMAGE" >/dev/null \
     || die "could not pull the sandbox image as $WORKER_USER"
fi
ok "in the worker's own rootless storage"

# ---------------------------------------------------------------------------------------------
say "the service"

cat > "$CONF/worker.env" <<EOF
# Written by install-worker-host.sh. The session directory holds the cgroups its ceilings live in.
XDG_RUNTIME_DIR=/run/user/$WORKER_UID
HOME=$HOME_DIR
TMPDIR=$HOME_DIR/tmp
AGENTNODE_LISTEN=$LISTEN
AGENTNODE_DEPLOYMENT=$DEPLOYMENT
AGENTNODE_GATEWAY_INSTANCE=$GATEWAY_INSTANCE
EOF
chmod 0644 "$CONF/worker.env"

install -m 0644 "$(unit_file agentnode-worker.service worker-host.service)" \
        /etc/systemd/system/agentnode-worker.service
install -d -m 0755 /etc/systemd/system/agentnode-worker.service.d
cat > /etc/systemd/system/agentnode-worker.service.d/session.conf <<EOF
# Written by install-worker-host.sh for uid $WORKER_UID. A unit cannot expand it: %U in a system
# unit is the manager's uid, not the account in User=.
[Unit]
After=user@$WORKER_UID.service
Wants=user@$WORKER_UID.service
EOF
systemctl daemon-reload
systemctl start "user@$WORKER_UID.service" 2>/dev/null || true

# The same check the unit runs before every start, run here so a mistake is a failed install
# rather than a service that flaps. It opens nothing and prints no key material.
set -a; . "$CONF/worker.env"; set +a
runuser -u "$WORKER_USER" -- env XDG_RUNTIME_DIR="/run/user/$WORKER_UID" HOME="$HOME_DIR" \
  "$PREFIX/venv/bin/agentnode" worker preflight \
    --topology separate-worker-host --listen "$LISTEN" \
    --keyring "$CONF/pair-keys.json" --journal "$JOURNAL" --tls-dir "$TLS_DIR" \
    --trust "$TRUST/ca.pem" --deployment "$DEPLOYMENT" \
    --accept-gateway "$GATEWAY_INSTANCE" \
    --revocation-list "$TRUST/revoked.crl" \
    --tombstones "$TRUST/revoked-identities.json" \
    --floor "$FLOOR/worker.floor" --key "$CONF/pair-keys.json" \
  || die "preflight refused this configuration. Nothing was started."

systemctl enable agentnode-worker.service >/dev/null
systemctl restart agentnode-worker.service
sleep 3
if ! systemctl is-active --quiet agentnode-worker.service; then
  printf '\n!! the worker did not start. What it said:\n\n'
  journalctl -u agentnode-worker.service -n 30 --no-pager | sed 's/^/   /'
  die "not going any further while the worker is down"
fi
ok "worker is running, which means it hit a ceiling and the ceiling held"

# ---------------------------------------------------------------------------------------------
say "the rule this script will not write for you"

cat <<EOF

   Nothing here opened a port. $BIND_PORT must be reachable FROM THE CONTROL PLANE'S PRIVATE
   ADDRESS AND FROM NOWHERE ELSE, in two places, because one of them is somebody else's software
   and the other is a rule this machine can show you:

     * the cloud firewall, on the private network;
     * this host, e.g.
         nft add rule inet filter input ip saddr <control-plane-private-ip> tcp dport $BIND_PORT accept
         nft add rule inet filter input tcp dport $BIND_PORT drop

   Check it from somewhere that is NOT the control plane afterwards. A rule that was never
   applied and a rule that works look identical from the machine it protects.

   Topology: separate-worker-host. That two machines isolate anything is NOT MEASURED.

EOF
