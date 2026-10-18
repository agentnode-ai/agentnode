#!/bin/sh
# Install ONE customer worker on a worker host, from the enrolment bundle `targets_cli invite` wrote.
# Target-router (decision Q-TGT-0001 D6/R7). Run as root on the worker host:
#
#   install-customer-worker.sh <instance> <listen tcps://IP:PORT> <bundle dir> <control private IP> <subid start>
#
# What it creates is the customer worker's OWN everything, so that it shares nothing a worker's
# security rests on with any other worker on this machine:
#   * an unprivileged system account agentnode-cw-<instance>, its own subordinate id range, linger;
#   * /var/lib/agentnode-cw-<instance> (0700, that account): tls/ (cert, key, its OWN copies of the
#     anchor, revocation list and tombstones, its pair-key file), journal/, floor/, tmp/;
#   * its own rootless podman store, runtime-rebuild unit and worker unit, with every other worker's
#     state made inaccessible to it;
#   * its own nft table: the port answers the control plane's private address and nobody else.
# It is two identities and two services on ONE host. It is never physical separation, and nothing
# here says otherwise.
#
# The worker's key is made HERE, by the service account, and never leaves this machine: the script
# prints the enrolment request for the operator to carry back (`targets_cli enroll`).
set -eu
INSTANCE="$1"; LISTEN="$2"; BUNDLE="$3"; CONTROL_IP="$4"; SUBID_START="$5"
USER_NAME="agentnode-cw-$INSTANCE"
HOME_DIR="/var/lib/agentnode-cw-$INSTANCE"
UNIT="agentnode-cw-$INSTANCE"
PORT="${LISTEN##*:}"
VENV=/opt/agentnode/venv
die() { echo "  FAILED: $*" >&2; exit 1; }
case "$INSTANCE" in *[!a-z0-9-]*|"") die "instance must be [a-z0-9-]";; esac
test -f "$BUNDLE/target.json" || die "no enrolment bundle at $BUNDLE"
test "$(id -u)" = 0 || die "run as root"

id "$USER_NAME" >/dev/null 2>&1 && die "$USER_NAME exists already; a customer worker is installed once"
useradd --system --create-home --home-dir "$HOME_DIR" --shell /sbin/nologin "$USER_NAME"
END=$((SUBID_START + 65535))
usermod --add-subuids "$SUBID_START-$END" --add-subgids "$SUBID_START-$END" "$USER_NAME"
loginctl enable-linger "$USER_NAME"
UID_N="$(id -u "$USER_NAME")"

install -d -o "$USER_NAME" -g "$USER_NAME" -m 0700 "$HOME_DIR" "$HOME_DIR/tls" "$HOME_DIR/journal" \
    "$HOME_DIR/floor" "$HOME_DIR/tmp" "$HOME_DIR/.config" "$HOME_DIR/.config/containers" \
    "$HOME_DIR/.local" "$HOME_DIR/.local/share"
printf '[containers]\ndefault_sysctls = []\n' > "$HOME_DIR/.config/containers/containers.conf"
for f in ca.pem revoked.crl revoked-identities.json; do
    install -o "$USER_NAME" -g "$USER_NAME" -m 0644 "$BUNDLE/$f" "$HOME_DIR/tls/$f"
done
install -o "$USER_NAME" -g "$USER_NAME" -m 0600 "$BUNDLE/pair-keys.json" "$HOME_DIR/tls/pair-keys.json"
install -o "$USER_NAME" -g "$USER_NAME" -m 0400 "$BUNDLE/enrolment-secret" "$HOME_DIR/tls/secret"
chown -R "$USER_NAME:$USER_NAME" "$HOME_DIR"

DEPLOYMENT="$("$VENV/bin/python" -c "import json,sys;print(json.load(open(sys.argv[1]))['deployment'])" "$BUNDLE/target.json")"
GATEWAY="$("$VENV/bin/python" -c "import json,sys;print(json.load(open(sys.argv[1]))['accept_gateway'])" "$BUNDLE/target.json")"
cat > "/etc/agentnode/$UNIT.env" <<EOF
AGENTNODE_LISTEN=$LISTEN
AGENTNODE_KEYRING=$HOME_DIR/tls/pair-keys.json
AGENTNODE_JOURNAL=$HOME_DIR/journal
AGENTNODE_TLS_DIR=$HOME_DIR/tls
AGENTNODE_TRUST=$HOME_DIR/tls/ca.pem
AGENTNODE_DEPLOYMENT=$DEPLOYMENT
AGENTNODE_GATEWAY_INSTANCE=$GATEWAY
AGENTNODE_REVOCATION_LIST=$HOME_DIR/tls/revoked.crl
AGENTNODE_TOMBSTONES=$HOME_DIR/tls/revoked-identities.json
AGENTNODE_FLOOR=$HOME_DIR/floor/worker.floor
XDG_RUNTIME_DIR=/run/user/$UID_N
TMPDIR=$HOME_DIR/tmp
HOME=$HOME_DIR
EOF
chmod 0644 "/etc/agentnode/$UNIT.env"

sed -e "s#@UNIT@#$UNIT#g" -e "s#@USER@#$USER_NAME#g" -e "s#@HOME@#$HOME_DIR#g" -e "s#@UID@#$UID_N#g" \
    "$(dirname "$0")/customer-worker.service.in" > "/etc/systemd/system/$UNIT.service"
sed -e "s#@UNIT@#$UNIT#g" -e "s#@USER@#$USER_NAME#g" -e "s#@HOME@#$HOME_DIR#g" \
    "$(dirname "$0")/customer-worker-runtime.service.in" > "/etc/systemd/system/$UNIT-runtime.service"

nft delete table inet "agentnode_cw_$INSTANCE" 2>/dev/null || true
printf 'table inet agentnode_cw_%s {\n chain input {\n  type filter hook input priority 0; policy accept;\n  tcp dport %s ip saddr %s accept\n  tcp dport %s drop\n }\n}\n' \
    "$INSTANCE" "$PORT" "$CONTROL_IP" "$PORT" | nft -f -

systemctl daemon-reload
systemctl start "user@$UID_N.service"
# the key and the request, made by the service's own account (the key never leaves this host)
runuser -u "$USER_NAME" -- env HOME="$HOME_DIR" "$VENV/bin/agentnode" pki request --tls-dir "$HOME_DIR/tls" >/dev/null
echo "  installed $UNIT for $LISTEN as $USER_NAME (uid $UID_N); request: $HOME_DIR/tls/request.json"
