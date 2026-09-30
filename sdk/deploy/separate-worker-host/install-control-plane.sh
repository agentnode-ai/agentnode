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
# ---------------------------------------------------------------------------------------------
# PHASE 2. The bootstrap has two phases because it cannot have one: phase 1 builds everything
# this machine can build alone and stops, because the worker cannot be enrolled before the CA
# exists; phase 2 runs after the worker answers, because a gateway that cannot measure its
# worker must not serve. Before this split the last gate of phase 1 demanded a running worker
# and the worker needed phase 1 to have finished -- a circle, and the 2026-09-29 run sat in it.
#
# Separate verb rather than a re-run: "install again" would mean either "build" or "activate"
# depending on state, and an operator would not be able to tell which one they just did.
if [ "${1:-}" = "--verify" ]; then
  [ -f "$STATE/state/config.json" ] || die "phase 1 has not run here: there is no configuration \
at $STATE/state/config.json. Run this script without --verify first."
  [ -f "$STATE/state/gtls/cert.pem" ] || die "this gateway has no certificate; phase 1 did not \
finish. Run it again -- every step of it checks before it acts."
  [ -f /etc/systemd/system/agentnode-gateway.service ] || die "the unit is not installed; phase \
1 did not finish."

  say "measuring the worker, as the account that owns this state"
  # A gateway that has not measured its worker does not know what it enforces, and says so:
  # "Nothing is in force yet: this gateway has not been measured." Phase 2 used to run only the
  # plain doctor, which reached the worker, reported exactly that, and refused -- so the
  # documented bring-up could not complete even with both machines up and talking. The
  # measurement runs real work on the worker; that is what makes it a measurement.
  if ! runuser -u "$GATEWAY_USER" -- "$PREFIX/venv/bin/agentnode" gateway doctor --measure \
        --dir "$STATE/state"; then
    printf '\n!! The measurement did not pass. Nothing was started. The worker is up but this\n'
    printf '   gateway cannot say what it enforces, and it will not serve on that basis.\n\n'
    exit 1
  fi
  ok "measured"

  say "reaching the worker, as the account that owns this state"
  # AS THE ACCOUNT. `securedir` requires that the state directory belong to whoever is looking
  # at it (st_uid == getuid()), and phase 1 deliberately makes it 0700 to the gateway. Running
  # this as root therefore failed with "belongs to another user" -- the installer's own last
  # gate, refusing by construction, on every host. The script already knew the right pattern:
  # `pki request` is run under this same account a few steps above.
  if runuser -u "$GATEWAY_USER" -- "$PREFIX/venv/bin/agentnode" gateway doctor \
        --dir "$STATE/state"; then
    ok "the worker answered and is the identity this deployment expects"
  else
    printf '\n!! The worker did not pass. The gateway is installed and enabled, and is NOT\n'
    printf '   started -- which is the safe end of this, not a failure of it.\n\n'
    printf '   When the worker is running, run this again:\n\n     %s --verify\n\n' "$0"
    exit 1
  fi

  systemctl start agentnode-gateway.service
  sleep 1
  if systemctl is-active --quiet agentnode-gateway.service; then
    ok "gateway started"
  else
    die "the gateway did not stay up; systemctl status agentnode-gateway.service"
  fi
  printf '\n   Phase 2 done. The client port is still closed: open %s when a client should\n' "$PORT"
  printf '   reach this, and not before.\n\n'
  exit 0
fi

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

# WHERE A UNIT FILE IS depends on what this script is running from. Inside an artefact the units
# are in ./unit/ and two of them are renamed; in the repository the role's unit sits beside this
# script and the shared PKI ones a directory up. Both are legitimate, and the 2026-09-29
# cross-host run showed what guessing costs: six references that resolved in the repository and
# nowhere else, so neither artefact could install itself. This asks instead.
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
  # WHICH ARTEFACT THIS IS, recorded inside the installed distribution, exactly as
  # deploy/deploy-pinned.sh does it. A service refuses to start when its pin names an artefact
  # the installation cannot confirm, and both installers here used to leave that record absent.
  ARTEFACT_DIGEST="$(sha256sum "$WHEEL" | cut -d' ' -f1)"
  SITE="$(ls -d "$PREFIX"/venv/lib*/python3*/site-packages 2>/dev/null | head -1)"
  DIST="$(ls -d "$SITE"/agentnode_sdk-*.dist-info 2>/dev/null | head -1)"
  [ -n "$DIST" ] || die "the installed distribution has no dist-info, so nothing can record
    which artefact it came from and this gateway would refuse to start."
  printf '%s\n' "$ARTEFACT_DIGEST" > "$DIST/AGENTNODE_ARTEFACT"
  ok "artefact digest recorded in $(basename "$DIST")"
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

# ---------------------------------------------------------------------------------------------
say "the runtime pin"

# The gateway refuses to start without one, for the same reason the worker does: a service that
# cannot say which interpreter and artefact it was meant to run from cannot notice that it is
# running from the wrong one. The commit and the wheel's digest come from the artefact's own
# BUILD.json, so nobody has to remember them.
if [ -f "$CONF/runtime-pin.json" ]; then
  ok "a pin is already here; left as it stands"
else
  [ -f "$HERE/BUILD.json" ] || die "this artefact carries no BUILD.json, so it cannot say which
    commit it was built from, and the gateway will not start without a pin that names one."
  "$PREFIX/venv/bin/python" - "$HERE/BUILD.json" "$CONF" <<'PINEOF'
import json, sys
from agentnode_sdk.gateway import runtime_pin
build = json.load(open(sys.argv[1], encoding="utf-8"))
running = "%d.%d.%d" % sys.version_info[:3]
where = runtime_pin.write_pin(sys.argv[2], python_version=running,
                              artefact_sha256=build["wheel_sha256"], commit=build["commit"])
print("   pin        : %s" % where)
print("   interpreter: %s" % running)
print("   built from : %s%s" % (build["commit"][:12],
                                "" if build.get("tree_was_clean", True) else "  (tree not clean)"))
PINEOF
  chmod 0644 "$CONF/runtime-pin.json"
  ok "written from the artefact's own BUILD.json"
fi
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

# Guarded like every other step in this script. It was the one that was not, and a partial
# install could therefore never be resumed: `set -e` plus a floor that already exists ended the
# run, every time, with no way forward but wiping the machine.
if [ ! -f "$FLOOR/gateway.floor" ]; then
  "$PREFIX/venv/bin/agentnode" pki floor init --role gateway \
      --identity "agentnode://$DEPLOYMENT/gateway/$GATEWAY_INSTANCE"
else
  ok "the gateway's floor is already set up; left as it stands"
fi
install -m 0644 "$(unit_file agentnode-pki-tick.service)" /etc/systemd/system/
install -m 0644 "$(unit_file agentnode-pki-tick.timer)"   /etc/systemd/system/
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
# neither file is any use once the certificate exists.
#
# THIS DIRECTORY IS THE GATEWAY'S OWN and the product does clean it: cert.pem and key.pem both
# end up here, so `forget_the_enrolment`'s pair check passes. The earlier comment here said "the
# product does not remove them", which was true of the OTHER directory and not of this one --
# the worker identity issued at /var/lib/agentnode/enrolment/<instance>, where a key.pem never
# appears because the private key stays with the worker. That is where the plaintext survived
# every issuance, it was found by enumerating both disks in the acceptance run of 2026-09-30,
# and the issuer now clears it after committing the consumption.
#
# This line stays as belt-and-braces for the window before the pair is complete here.
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

# WHAT PHASE 1 CAN CHECK ABOUT THE ADDRESS, AND WHAT IT CANNOT.
#
# It can catch every mistake that does not need the worker to exist: the scheme, a literal
# private address, a port, an address that is this machine's own, and whether the kernel would
# route it out of a private interface rather than the default one. It CANNOT tell a correct
# address from a syntactically valid address belonging to some other host on the same network --
# that needs the worker's identity, which does not exist yet. Phase 2 does that, and this says
# so rather than implying the check is stronger than it is.
say "the address, as far as it can be judged before the worker exists"

python3 - "$WORKER_ADDRESS" <<'PY' || die "the worker address is not one this gateway can use."
import ipaddress, sys
address = sys.argv[1][len("tcps://"):]
host, _, port = address.rpartition(":")
host = host.strip("[]")
try:
    ip = ipaddress.ip_address(host)
except ValueError:
    raise SystemExit("   %s is not a literal IP address. A name can move; the worker's address "
                     "is written down." % host)
if not (port.isdigit() and 0 < int(port) < 65536):
    raise SystemExit("   %r is not a port" % port)
for bad, why in ((ip.is_loopback, "loopback"), (ip.is_multicast, "multicast"),
                 (ip.is_unspecified, "the wildcard")):
    if bad:
        raise SystemExit("   %s is %s, which is not another machine" % (ip, why))
if not ip.is_private:
    raise SystemExit("   %s is a public address. The worker is reached over the private network; "
                     "a public one here is an open door with a certificate on it." % ip)
print("   %s is a literal private address on port %s" % (ip, port))
PY

WORKER_IP="${WORKER_ADDRESS#tcps://}"; WORKER_IP="${WORKER_IP%:*}"; WORKER_IP="${WORKER_IP#[}"
WORKER_IP="${WORKER_IP%]}"
if ip -4 -o addr show 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | grep -qx "$WORKER_IP"; then
  die "$WORKER_IP is an address of THIS machine. The worker is the other one."
fi
if command -v ip >/dev/null 2>&1; then
  route="$(ip route get "$WORKER_IP" 2>/dev/null || true)"
  if [ -z "$route" ]; then
    ok "no route to $WORKER_IP yet -- the private network may not be up on this host"
  else
    ok "routed as: $(printf '%s' "$route" | head -1)"
  fi
fi

# ---------------------------------------------------------------------------------------------
say "the service"

install -m 0644 "$(unit_file agentnode-gateway.service control-plane.service)" \
        /etc/systemd/system/agentnode-gateway.service
systemctl daemon-reload
systemctl enable agentnode-gateway.service >/dev/null
# NOT started. The gateway has no worker to reach until the worker host is installed and
# enrolled, and a control plane that spends its first minutes refusing every job teaches whoever
# is watching to ignore it. `--verify` starts it, once the worker answers.
ok "installed and enabled, NOT started"

# ---------------------------------------------------------------------------------------------
say "what the worker host needs from this machine"

printf '\n   Copy these to the worker host (they are signed; the copy does not have to be trusted,\n'
printf '   but it does have to be FRESH -- both expire, and a list too old to believe is a refusal):\n\n'
printf '     %s\n' "$TRUST/ca.pem" "$TRUST/revoked.crl" "$TRUST/revoked-identities.json" "$CONF/pair-keys.json"
printf '\n   Then, here, make the worker an identity and hand over the one-shot secret:\n\n'
printf '     install -d -m 0700 %s/%s\n' "$ENROL" "$WORKER_INSTANCE"
printf '     agentnode pki add --role worker --instance %s --account root --tls-dir %s/%s\n\n' \
       "$WORKER_INSTANCE" "$ENROL" "$WORKER_INSTANCE"
printf '   WHEN THE WORKER IS INSTALLED, ENROLLED AND RUNNING, come back here and run:\n\n'
printf '     %s --verify\n\n' "$0"
printf '   That is phase 2: it reaches the worker over mutual TLS, checks it is the identity this\n'
printf '   deployment expects, and only then starts the gateway. Until it passes, this machine is\n'
printf '   installed and silent -- which is the point: a control plane that cannot measure its\n'
printf '   worker does not serve.\n\n'
printf '   The client port is still closed. Open %s when a client should reach this, and not before.\n' "$PORT"
printf '   Topology: separate-worker-host. That two machines isolate anything is NOT MEASURED.\n\n'
