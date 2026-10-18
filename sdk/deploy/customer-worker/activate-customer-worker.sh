#!/bin/sh
# Second step of a customer worker install (see install-customer-worker.sh). Run as root on the worker
# host AFTER `targets_cli enroll` issued the certificate:
#
#   activate-customer-worker.sh <instance> <cert.pem>
#
# Installs the issued certificate for the service account, sets up its time floor (root-owned: the
# worker reads it and cannot move it), starts the floor timer, the rootless runtime and the worker.
# The target becomes usable only after `targets_cli probe` observes it from the control plane.
set -eu
INSTANCE="$1"; CERT="$2"
USER_NAME="agentnode-cw-$INSTANCE"
HOME_DIR="/var/lib/agentnode-cw-$INSTANCE"
UNIT="agentnode-cw-$INSTANCE"
PY=/opt/agentnode/venv/bin/python
die() { echo "  FAILED: $*" >&2; exit 1; }
case "$INSTANCE" in *[!a-z0-9-]*|"") die "instance must be [a-z0-9-]";; esac
test "$(id -u)" = 0 || die "run as root"
id "$USER_NAME" >/dev/null 2>&1 || die "$USER_NAME is not installed"
test -f "$CERT" || die "no certificate at $CERT"
. "/etc/agentnode/$UNIT.env"

install -o "$USER_NAME" -g "$USER_NAME" -m 0644 "$CERT" "$HOME_DIR/tls/cert.pem"
rm -f "$HOME_DIR/tls/secret"                          # spent at enrolment
PYTHONPATH="$PYTHONPATH" "$PY" -m agentnode_sdk.cli pki floor init --role worker \
    --certificate "$HOME_DIR/tls/cert.pem" --anchor "$HOME_DIR/tls/ca.pem" --floor-dir "$AGENTNODE_FLOOR_DIR" >/dev/null
systemctl start "$UNIT-floor.service"
systemctl enable --now "$UNIT-floor.timer" "$UNIT-runtime.service" >/dev/null 2>&1
systemctl enable --now "$UNIT.service" >/dev/null 2>&1
for i in $(seq 1 30); do ss -ltnH | grep -q "${AGENTNODE_LISTEN#tcps://}" && break; sleep 1; done
echo "  $UNIT: $(systemctl is-active "$UNIT.service"), listening on ${AGENTNODE_LISTEN#tcps://}:" \
     "$(ss -ltnH | grep -c "${AGENTNODE_LISTEN#tcps://}")"
