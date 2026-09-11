#!/bin/bash
# Put a gateway and a sandbox worker on ONE machine, as two accounts that do not share.
#
# WHAT THIS IS
#
#   A closed development and test topology, labelled `single-host-development` everywhere it
#   appears in a record. The control plane and the worker are separate ACCOUNTS on one kernel.
#   That is not a tenancy boundary, it is not escape-proof between the two, and nothing built on
#   it may be described as production-ready, multi-tenant, or isolated between control plane and
#   worker. An escape from the sandbox reaches this host, and this host is where the control
#   plane's pairing state, signing identity and client tokens live.
#
#   The architecture for live operation puts the worker on its own machine. This script exists so
#   that everything EXCEPT that separation can be built and tested first, and so the move is a
#   change to two configuration files rather than a change to the product.
#
# WHAT IT DELIBERATELY DOES NOT DO
#
#   It does not put any account in the docker group. That group is root-equivalent, and the whole
#   point of a worker account is that an escape from the sandbox does not get the machine. The
#   worker drives podman ROOTLESS, as itself.
#
#   It opens no inbound port. The gateway's port is opened by whoever runs this, deliberately,
#   afterwards, with `--port`.
#
# Re-runnable: every step checks before it acts.

set -euo pipefail

GATEWAY_USER=agentnode-gateway
WORKER_USER=agentnode-worker
BRIDGE_GROUP=agentnode-bridge
PREFIX=/opt/agentnode
CONF=/etc/agentnode
STATE=/var/lib/agentnode
WORKER_HOME=/var/lib/agentnode-worker
SOCKET=/run/agentnode/worker.sock
PORT="${AGENTNODE_PORT:-8099}"
WHEEL="${AGENTNODE_WHEEL:-}"

say() { printf '\n== %s\n' "$1"; }
ok()  { printf '   %s\n' "$1"; }
die() { printf '\n!! %s\n' "$1" >&2; exit 1; }

[ "$(id -u)" = "0" ] || die "this sets up system accounts and units, so it needs root"
command -v systemctl >/dev/null || die "there is no systemd here"
command -v podman   >/dev/null || die "podman is not installed, and the worker runs rootless podman"
command -v python3  >/dev/null || die "python3 is not installed"

# This runs things as the two unprivileged accounts, and a process started from a directory they
# cannot read fails before it begins ("cannot chdir to /root"). Root's home is exactly where an
# administrator runs this from, so step out of it first.
HERE="$(cd "$(dirname "$0")" && pwd)"
cd /

# ---------------------------------------------------------------------------------------------
say "two accounts, and a group whose whole purpose is one socket"

getent group "$BRIDGE_GROUP" >/dev/null || groupadd --system "$BRIDGE_GROUP"
ok "group $BRIDGE_GROUP"

id "$WORKER_USER" >/dev/null 2>&1 || useradd --system --create-home --home-dir "$WORKER_HOME" \
    --shell /usr/sbin/nologin --comment "AgentNode sandbox worker" "$WORKER_USER"
id "$GATEWAY_USER" >/dev/null 2>&1 || useradd --system --create-home --home-dir "$STATE" \
    --shell /usr/sbin/nologin --comment "AgentNode gateway (control plane)" "$GATEWAY_USER"
ok "$WORKER_USER  uid $(id -u "$WORKER_USER")"
ok "$GATEWAY_USER uid $(id -u "$GATEWAY_USER")"

usermod --append --groups "$BRIDGE_GROUP" "$GATEWAY_USER"
usermod --append --groups "$BRIDGE_GROUP" "$WORKER_USER"

# The one property worth checking rather than intending. A control-plane account in a
# root-equivalent group would make every other boundary here decorative.
for account in "$GATEWAY_USER" "$WORKER_USER"; do
  for dangerous in docker wheel sudo root adm; do
    if id -nG "$account" 2>/dev/null | tr ' ' '\n' | grep -qx "$dangerous"; then
      die "$account is in the $dangerous group. Nothing below is worth doing until it is not."
    fi
  done
done
ok "neither account is in docker, wheel, sudo, root or adm"

# ---------------------------------------------------------------------------------------------
say "what the worker needs to run containers as itself"

if ! grep -q "^${WORKER_USER}:" /etc/subuid 2>/dev/null; then
  usermod --add-subuids 400000-465535 --add-subgids 400000-465535 "$WORKER_USER"
fi
ok "subordinate ids: $(grep "^${WORKER_USER}:" /etc/subuid)"

# Without a session for this account, podman has no systemd cgroup manager to delegate through,
# falls back to cgroupfs, and SILENTLY DOES NOT APPLY the memory ceiling -- the flag is accepted
# and an allocation walks straight past the limit. Measured on this host, not assumed. The worker
# re-checks by hitting a ceiling before it serves, so a mistake here fails closed.
# If this account has a session already but no working runtime state, it is usually one left by
# an earlier failed start -- and it survives restarts, so the worker keeps failing for a reason
# that is no longer there. A re-run recycles it rather than inheriting it.
if [ -d "/run/user/$(id -u "$WORKER_USER")" ]    && ! runuser -u "$WORKER_USER" -- podman info >/dev/null 2>&1; then
  ok "the worker's session exists but its runtime does not answer; recycling it"
  systemctl stop agentnode-worker.service 2>/dev/null || true
  loginctl disable-linger "$WORKER_USER"; sleep 2
  pkill -u "$WORKER_USER" 2>/dev/null || true
  rm -rf "/run/user/$(id -u "$WORKER_USER")" "$WORKER_HOME/.local/share/containers"          "$WORKER_HOME/.config/containers" "$WORKER_HOME/.cache"
fi
loginctl enable-linger "$WORKER_USER"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  [ -d "/run/user/$(id -u "$WORKER_USER")" ] && break
  sleep 1
done
WORKER_UID="$(id -u "$WORKER_USER")"
[ -d "/run/user/$WORKER_UID" ] || die "no session directory for $WORKER_USER; cgroup limits would not bind"
ok "session at /run/user/$WORKER_UID with controllers: $(cat "/sys/fs/cgroup/user.slice/user-$WORKER_UID.slice/cgroup.controllers" 2>/dev/null || echo "none -- limits will not bind")"

# ---------------------------------------------------------------------------------------------
say "the code"

if [ -n "$WHEEL" ]; then
  [ -f "$WHEEL" ] || die "no wheel at $WHEEL"
  [ -d "$PREFIX/venv" ] || python3 -m venv "$PREFIX/venv"
  # --force-reinstall because a development wheel keeps its version number while its contents
  # change, and "already satisfied" would quietly leave the previous code running. A deploy that
  # silently installs nothing is worse than one that fails.
  "$PREFIX/venv/bin/pip" install --quiet --force-reinstall --no-deps "$WHEEL"
  "$PREFIX/venv/bin/pip" install --quiet "$WHEEL"
  ok "installed $(basename "$WHEEL")"
else
  [ -x "$PREFIX/venv/bin/agentnode" ] || die "no wheel given and nothing installed at $PREFIX"
  ok "keeping what is already installed"
fi
ok "version: $("$PREFIX/venv/bin/agentnode" --version 2>/dev/null || echo unknown)"
chmod 0755 "$PREFIX" "$PREFIX/venv"

# ---------------------------------------------------------------------------------------------
say "the image jobs run in"

# Pulled as the WORKER, into the worker's own rootless storage. Root's copy is no use here: a
# rootless runtime keeps its images under the account's home, and the account is the only one
# that will ever start a container.
IMAGE="$("$PREFIX/venv/bin/python3" -c 'from agentnode_sdk.sandbox.container_backend import _BASE_IMAGE; print(_BASE_IMAGE)')"
ok "pinned by digest: ${IMAGE#*@}"
if runuser -u "$WORKER_USER" -- env XDG_RUNTIME_DIR="/run/user/$WORKER_UID" HOME="$WORKER_HOME"      TMPDIR="$WORKER_HOME/tmp" podman image exists "$IMAGE" 2>/dev/null; then
  ok "already in the worker's storage"
else
  ok "pulling it (this is the only network the setup needs)"
  runuser -u "$WORKER_USER" -- env XDG_RUNTIME_DIR="/run/user/$WORKER_UID" HOME="$WORKER_HOME"     TMPDIR="$WORKER_HOME/tmp" podman pull --quiet "$IMAGE" >/dev/null     || die "could not pull the sandbox image as $WORKER_USER"
  ok "pulled"
fi

# ---------------------------------------------------------------------------------------------
say "directories, and who cannot read them"

install -d -o root -g "$BRIDGE_GROUP" -m 0750 "$CONF"
install -d -o "$GATEWAY_USER" -g "$GATEWAY_USER" -m 0700 "$STATE" "$STATE/state"
install -d -o "$WORKER_USER"  -g "$WORKER_USER"  -m 0700 "$WORKER_HOME"
ok "$STATE is 0700 to $GATEWAY_USER -- the worker cannot read the pairing state, the signing"
ok "   identity, any client's token, or the ledger"
ok "$WORKER_HOME is 0700 to $WORKER_USER"

# The key the two share to authenticate messages on the socket. Root owns it so neither account
# can change it; both read it because both ends of a MAC need the same key.
if [ ! -f "$CONF/worker.key" ]; then
  "$PREFIX/venv/bin/agentnode" worker key --at "$CONF/worker.key"
fi
chown root:"$BRIDGE_GROUP" "$CONF/worker.key"
chmod 0640 "$CONF/worker.key"
ok "worker.key is 0640 root:$BRIDGE_GROUP -- readable by both, writable by neither"

# The account's uid is a fact about this machine, so it lives in deployment configuration. A unit
# cannot expand it: %U in a system unit is the manager's uid, not the one in User=.
install -d -o "$WORKER_USER" -g "$WORKER_USER" -m 0700 "$WORKER_HOME/tmp"
cat > "$CONF/worker.env" <<EOF
# Written by single-host-development.sh. The worker's session directory, where the cgroups that
# actually hold its ceilings live.
XDG_RUNTIME_DIR=/run/user/$WORKER_UID
HOME=$WORKER_HOME
# Pulling an image needs somewhere to unpack it. ProtectSystem=strict leaves /var/tmp read-only,
# and PrivateTmp does not cover what the runtime reaches for here, so it gets a directory of its
# own inside the only tree it may write.
TMPDIR=$WORKER_HOME/tmp
EOF
chmod 0644 "$CONF/worker.env"
ok "worker.env points at /run/user/$WORKER_UID"

cat > "$CONF/gateway.env" <<EOF
# Written by single-host-development.sh.
AGENTNODE_PORT=$PORT
# 127.0.0.1 unless the operator says otherwise. Letting another machine reach this is a decision
# somebody makes on purpose: change this, restart the service, and open the port -- three
# deliberate steps, none of which happen because something was installed.
AGENTNODE_HOST=${AGENTNODE_HOST:-127.0.0.1}
EOF
chmod 0644 "$CONF/gateway.env"

# ---------------------------------------------------------------------------------------------
say "where the gateway looks for a worker"

# This file is the whole of "moving the worker is deployment configuration". Nothing in the
# product names a socket, a host or an account; the gateway reads an address, and the topology in
# every record it writes is derived from that address rather than declared beside it. Putting the
# worker on its own machine needs a transport this build does not have: `from_address` speaks
# unix sockets and refuses everything else, in as many words. What exists is the seam -- when a
# remote transport is written, this value is what changes, and no product code does.
python3 - "$STATE/state/config.json" "unix://$SOCKET" "$CONF/worker.key" <<'PY'
import json, os, sys
path, address, key = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(path, encoding="utf-8") as fh:
        body = json.load(fh)
except (OSError, ValueError):
    body = {}
body["worker_address"] = address
body["worker_key"] = key
tmp = path + ".new"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(body, fh, indent=2, sort_keys=True)
os.replace(tmp, path)
print("   worker_address = " + address)
PY
chown "$GATEWAY_USER":"$GATEWAY_USER" "$STATE/state/config.json"
chmod 0600 "$STATE/state/config.json"

# ---------------------------------------------------------------------------------------------
say "the two services"

# The socket's directory, owned by the worker and grouped to the bridge, with the setgid bit that
# makes the socket inherit that group. See the file for why this is not RuntimeDirectory=.
install -m 0644 "$HERE/agentnode-worker.tmpfiles.conf" /etc/tmpfiles.d/agentnode-worker.conf
systemd-tmpfiles --create /etc/tmpfiles.d/agentnode-worker.conf
ok "socket directory: $(stat -c '%A %U:%G' /run/agentnode)"
case "$(stat -c '%A' /run/agentnode)" in
  *s*) : ;;
  *) die "/run/agentnode is not setgid, so the socket would not carry the shared group" ;;
esac

install -m 0644 "$HERE/agentnode-worker.service"  /etc/systemd/system/agentnode-worker.service
install -m 0644 "$HERE/agentnode-gateway.service" /etc/systemd/system/agentnode-gateway.service
# The worker must start after the account's user manager: that manager owns the cgroups its
# ceilings live in, and starting first means falling back to cgroupfs and refusing to serve. The
# uid is a fact about this machine, so this is written here rather than carried in the unit.
install -d -m 0755 /etc/systemd/system/agentnode-worker.service.d
cat > /etc/systemd/system/agentnode-worker.service.d/session.conf <<EOF
# Written by single-host-development.sh for uid $WORKER_UID.
[Unit]
After=user@$WORKER_UID.service
Wants=user@$WORKER_UID.service
EOF

systemctl daemon-reload
systemctl start "user@$WORKER_UID.service" 2>/dev/null || true
for _ in 1 2 3 4 5 6 7 8 9 10; do
  grep -q memory "/sys/fs/cgroup/user.slice/user-$WORKER_UID.slice/cgroup.controllers" 2>/dev/null && break
  sleep 1
done
grep -q memory "/sys/fs/cgroup/user.slice/user-$WORKER_UID.slice/cgroup.controllers" 2>/dev/null   || die "the worker's session has no delegated memory controller, so its ceilings would not bind"
ok "the session's memory controller is delegated, which is what makes a ceiling bind"
ok "units installed"

systemctl enable --now agentnode-worker.service
sleep 3
if ! systemctl is-active --quiet agentnode-worker.service; then
  printf '\n!! the worker did not start. What it said:\n\n'
  journalctl -u agentnode-worker.service -n 30 --no-pager | sed 's/^/   /'
  die "not starting the gateway while the worker is down"
fi
ok "worker is running, which means it hit a ceiling and the ceiling held"

systemctl enable --now agentnode-gateway.service
sleep 3
systemctl is-active --quiet agentnode-gateway.service || {
  journalctl -u agentnode-gateway.service -n 30 --no-pager | sed 's/^/   /'
  die "the gateway did not start"
}
ok "gateway is running"

# ---------------------------------------------------------------------------------------------
say "what is actually true now, read back rather than assumed"

printf '   socket      : %s\n' "$(stat -c '%A %U:%G' "$SOCKET" 2>/dev/null || echo 'MISSING')"
printf '   its parent  : %s\n' "$(stat -c '%A %U:%G' /run/agentnode 2>/dev/null || echo 'MISSING')"
printf '   key         : %s\n' "$(stat -c '%A %U:%G' "$CONF/worker.key")"
printf '   gateway dir : %s\n' "$(stat -c '%A %U:%G' "$STATE/state")"

printf '   can the control plane reach the socket? '
if runuser -u "$GATEWAY_USER" -- test -r "$SOCKET" 2>/dev/null; then printf 'yes
'
else printf 'NO -- the gateway cannot talk to its worker
'; fi

printf '   can the control plane run a container? '
if runuser -u "$GATEWAY_USER" -- podman run --rm docker.io/library/alpine:3.20 true >/dev/null 2>&1; then
  printf 'YES -- that is a finding, not a feature\n'
else
  printf 'no\n'
fi

printf '   can the worker read the gateway state? '
if runuser -u "$WORKER_USER" -- test -r "$STATE/state/config.json" 2>/dev/null; then
  printf 'YES -- that is a finding, not a feature\n'
else
  printf 'no\n'
fi

printf '\n   The inbound port is still closed. Open %s when a client should reach this.\n' "$PORT"
printf '   Topology: single-host-development. Two accounts on one kernel are not isolation.\n\n'
