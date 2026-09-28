#!/bin/bash
# The control-plane half of a two-machine deployment. Run this FIRST: it is the CA, and a worker
# cannot be enrolled before one exists.
#
# WHAT THIS MACHINE BECOMES
#
#   The only account is agentnode-gateway. There is no worker account, no bridge group and no
#   container runtime -- and this script refuses to continue if it finds a runtime installed,
#   because the whole point of the arrangement is that foreign code does not run here.
#
# WHAT IT DOES NOT DO
#
#   It opens no port, on either interface. The client port is opened afterwards, on purpose, by
#   whoever decided clients should reach this. It does not order, reach or configure the worker
#   host; it prints the two things the worker host needs and stops.
#
# Re-runnable: every step checks before it acts.

set -euo pipefail

GATEWAY_USER=agentnode-gateway
PREFIX=/opt/agentnode
CONF=/etc/agentnode
TRUST=/etc/agentnode/trust
CA_DIR=/etc/agentnode/ca
STATE=/var/lib/agentnode
ENROL=/var/lib/agentnode/enrolment
FLOOR=/var/lib/agentnode-floor
PORT="${AGENTNODE_PORT:-8099}"
WHEEL="${AGENTNODE_WHEEL:-}"
WORKER_ADDRESS="${AGENTNODE_WORKER_ADDRESS:-}"
DEPLOYMENT="${AGENTNODE_DEPLOYMENT:-}"
GATEWAY_INSTANCE="${AGENTNODE_GATEWAY_INSTANCE:-g1}"
WORKER_INSTANCE="${AGENTNODE_WORKER_INSTANCE:-w1}"

say() { printf '\n== %s\n' "$1"; }
ok()  { printf '   %s\n' "$1"; }
die() { printf '\n!! %s\n' "$1" >&2; exit 1; }

[ "$(id -u)" = "0" ] || die "this makes system accounts and units, so it needs root"
command -v systemctl >/dev/null || die "there is no systemd here"
command -v python3   >/dev/null || die "python3 is not installed"
[ -n "$WORKER_ADDRESS" ] || die "set AGENTNODE_WORKER_ADDRESS to the worker's PRIVATE address, e.g. tcps://10.0.1.5:8443"
[ -n "$DEPLOYMENT" ]     || die "set AGENTNODE_DEPLOYMENT to this deployment's id; every identity is named inside it"

case "$WORKER_ADDRESS" in
  tcps://*) : ;;
  *) die "a worker on another machine is reached over mutual TLS and nothing else: $WORKER_ADDRESS" ;;
esac
case "$WORKER_ADDRESS" in
  *127.0.0.1*|*localhost*|*"[::1]"*|*0.0.0.0*)
    die "$WORKER_ADDRESS is not another machine. The product refuses this too -- it is refused here so the refusal is not a surprise at first start" ;;
esac

HERE="$(cd "$(dirname "$0")" && pwd)"
cd /

# ---------------------------------------------------------------------------------------------
say "this machine runs no foreign code, and that is checked rather than intended"

for runtime in docker podman; do
  if command -v "$runtime" >/dev/null 2>&1; then
    die "$runtime is installed on the control plane. The arrangement this script sets up is that
    foreign code runs on a different machine; a runtime here is how that stops being true. Remove
    it, or install the worker half on this host instead and accept that it is single-host."
  fi
done
ok "no container runtime on this host"

# ---------------------------------------------------------------------------------------------
say "the one account"

id "$GATEWAY_USER" >/dev/null 2>&1 || useradd --system --create-home --home-dir "$STATE" \
    --shell /usr/sbin/nologin --comment "AgentNode gateway (control plane)" "$GATEWAY_USER"
ok "$GATEWAY_USER uid $(id -u "$GATEWAY_USER")"

for dangerous in docker wheel sudo root adm; do
  if id -nG "$GATEWAY_USER" 2>/dev/null | tr ' ' '\n' | grep -qx "$dangerous"; then
    die "$GATEWAY_USER is in the $dangerous group. Nothing below is worth doing until it is not."
  fi
done
ok "in no group that reaches a runtime or root"

# ---------------------------------------------------------------------------------------------
say "the code"

if [ -n "$WHEEL" ]; then
  [ -f "$WHEEL" ] || die "no wheel at $WHEEL"
  [ -d "$PREFIX/venv" ] || python3 -m venv "$PREFIX/venv"
  # --force-reinstall because a development wheel keeps its version number while its contents
  # change, and "already satisfied" would leave the previous code running while every check said
  # it was deployed.
  "$PREFIX/venv/bin/pip" install --quiet --force-reinstall --no-deps "$WHEEL"
  "$PREFIX/venv/bin/pip" install --quiet "$WHEEL"
  ok "installed $(basename "$WHEEL")"
else
  [ -x "$PREFIX/venv/bin/agentnode" ] || die "no wheel given and nothing installed at $PREFIX"
  ok "keeping what is already installed"
fi
ok "version: $("$PREFIX/venv/bin/agentnode" --version 2>/dev/null || echo unknown)"
chmod 0755 "$PREFIX" "$PREFIX/venv"
# What is installed here is one wheel containing both roles. The worker's modules are present on
# this machine and nothing starts them; `agentnode_sdk/roles.py` says which those are.

# ---------------------------------------------------------------------------------------------
say "directories, and who cannot read them"

install -d -o root -g root -m 0755 "$CONF"
install -d -o root -g root -m 0755 "$TRUST"
install -d -o "$GATEWAY_USER" -g "$GATEWAY_USER" -m 0700 "$STATE" "$STATE/state"
ok "$STATE is 0700 to $GATEWAY_USER"
ok "$TRUST is root's: the gateway reads the signed lists and cannot write them"

# ---------------------------------------------------------------------------------------------
say "the deployment's own issuer, and this gateway's identity"

# The issuer's key is root's, in /etc/agentnode/ca, and its certificate goes in the trust
# directory beside the two signed lists. Neither is under $STATE: that directory belongs to the
# gateway's account, and a CA it could write would be a CA it could issue with.
if [ ! -f "$CA_DIR/ca.key" ]; then
  "$PREFIX/venv/bin/agentnode" pki init --deployment "$DEPLOYMENT"
fi
ok "issuer for deployment $DEPLOYMENT (key in $CA_DIR, certificate in $TRUST)"

"$PREFIX/venv/bin/agentnode" pki floor init
install -m 0644 "$HERE/../agentnode-pki-tick.service" /etc/systemd/system/
install -m 0644 "$HERE/../agentnode-pki-tick.timer"   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now agentnode-pki-tick.timer >/dev/null
ok "root's time floor at $FLOOR, ticked by a timer. If the timer stops, this gateway stops"
ok "   serving over TLS once the floor ages out -- on purpose."

# ---------------------------------------------------------------------------------------------
say "this gateway's own certificate"

# Both ends of the enrolment are on this machine, so it can be done here in one go. The worker's
# cannot, and that is the whole difference between this section and the one printed at the end.
install -d -o "$GATEWAY_USER" -g "$GATEWAY_USER" -m 0700 "$STATE/state/gtls"
if [ ! -f "$STATE/state/gtls/cert.pem" ]; then
  "$PREFIX/venv/bin/agentnode" pki add --role gateway --instance "$GATEWAY_INSTANCE" \
      --account "$GATEWAY_USER" --tls-dir "$STATE/state/gtls"
  # As the account, because the private key must be made where it will stay and by whoever will
  # read it. Root making it would leave root's key in the gateway's directory.
  runuser -u "$GATEWAY_USER" -- "$PREFIX/venv/bin/agentnode" pki request \
      --tls-dir "$STATE/state/gtls"
  "$PREFIX/venv/bin/agentnode" pki enroll --request "$STATE/state/gtls/request.json"
fi
[ -f "$STATE/state/gtls/cert.pem" ] || die "the gateway has no certificate, so it cannot dial its worker"
# The residues enrolment leaves behind. `request.json` contains the one-shot secret in clear and
# neither file is any use once the certificate exists. The product does not remove them; this
# does, and that omission is recorded as an open item rather than hidden by this line.
rm -f "$STATE/state/gtls/secret" "$STATE/state/gtls/request.json"
ok "certificate in $STATE/state/gtls, enrolment residues deleted"

# ---------------------------------------------------------------------------------------------
say "the pair key, and what the worker host needs from here"

# One key per (gateway, worker) pair, selected by the identity TLS proved. A key shared with
# everything would mean that reaching one worker is reaching all of them.
if [ ! -f "$CONF/pair-keys.json" ]; then
  "$PREFIX/venv/bin/agentnode" worker key --pair "$GATEWAY_INSTANCE:$WORKER_INSTANCE" \
      --at "$CONF/pair-keys.json"
fi
chown root:"$GATEWAY_USER" "$CONF/pair-keys.json"
chmod 0640 "$CONF/pair-keys.json"
ok "pair-keys.json is 0640 root:$GATEWAY_USER"

cat > "$CONF/gateway.env" <<EOF
# Written by install-control-plane.sh.
AGENTNODE_PORT=$PORT
# 127.0.0.1 until somebody decides otherwise. Letting a client machine reach this is three
# deliberate steps -- change this, restart, open the port -- and none of them happens because
# something was installed.
AGENTNODE_HOST=${AGENTNODE_HOST:-127.0.0.1}
EOF
chmod 0644 "$CONF/gateway.env"

# ---------------------------------------------------------------------------------------------
say "where the worker is"

python3 - "$STATE/state/config.json" "$WORKER_ADDRESS" "$CONF/pair-keys.json" \
          "$DEPLOYMENT" "$WORKER_INSTANCE" "$STATE" "$TRUST" "$FLOOR" <<'PY'
import json, os, sys
path, address, keyring, deployment, worker, state, trust, floor = sys.argv[1:9]
try:
    with open(path, encoding="utf-8") as fh:
        body = json.load(fh)
except (OSError, ValueError):
    body = {}
body["worker_topology"] = "separate-worker-host"
body["worker_address"] = address
body["worker_keyring"] = keyring
body["worker_tls"] = {
    "certificate": state + "/state/gtls/cert.pem",
    "key": state + "/state/gtls/key.pem",
    "anchor": trust + "/ca.pem",
    "deployment": deployment,
    "accept": [worker],
    "revocation_list": trust + "/revoked.crl",
    "identity_tombstones": trust + "/revoked-identities.json",
    "floor": floor + "/gateway.floor",
}
tmp = path + ".new"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(body, fh, indent=2, sort_keys=True)
os.replace(tmp, path)
print("   worker_topology = separate-worker-host")
print("   worker_address  = " + address)
PY
chown "$GATEWAY_USER":"$GATEWAY_USER" "$STATE/state/config.json"
chmod 0600 "$STATE/state/config.json"

# The declaration and the address have to agree, and the product checks that itself before it
# dials. Checking it here as well means a typo is a failed install rather than a gateway that
# starts and refuses every job.
"$PREFIX/venv/bin/agentnode" gateway doctor --dir "$STATE/state" || \
    die "the gateway does not accept this configuration. Nothing was started."

# ---------------------------------------------------------------------------------------------
say "the service"

install -m 0644 "$HERE/control-plane.service" /etc/systemd/system/agentnode-gateway.service
systemctl daemon-reload
systemctl enable agentnode-gateway.service >/dev/null
# NOT started. The gateway has no worker to reach until the worker host is installed and
# enrolled, and a control plane that spends its first minutes refusing every job teaches whoever
# is watching to ignore it.
ok "installed and enabled, NOT started"

# ---------------------------------------------------------------------------------------------
say "what the worker host needs from this machine"

printf '\n   Copy these to the worker host (they are signed; the copy does not have to be trusted,\n'
printf '   but it does have to be FRESH -- both expire, and a list too old to believe is a refusal):\n\n'
printf '     %s\n' "$TRUST/ca.pem" "$TRUST/revoked.crl" "$TRUST/revoked-identities.json" "$CONF/pair-keys.json"
printf '\n   Then, here, make the worker an identity and hand over the one-shot secret:\n\n'
printf '     agentnode pki add --dir %s --role worker --instance %s\n\n' "$STATE/state" "$WORKER_INSTANCE"
printf '   The client port is still closed. Open %s when a client should reach this, and not before.\n' "$PORT"
printf '   Topology: separate-worker-host. That two machines isolate anything is NOT MEASURED.\n\n'
