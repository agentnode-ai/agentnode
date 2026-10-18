#!/bin/sh
# Remove ONE customer worker from a worker host completely (target-router cleanup, Codex D11).
# Run as root AFTER `targets_cli revoke` on the control plane (the identity is withdrawn for good
# there; this removes everything that could still be used here):
#
#   remove-customer-worker.sh <instance>
#
# Stops and removes the units, the account's containers and storage, the account itself (with its
# subordinate ids and linger), its state directory (certificate, key, pair key, journal, floor),
# its env file and its nft table. Prints what is left (must be nothing).
set -u
INSTANCE="$1"
USER_NAME="agentnode-cw-$INSTANCE"
HOME_DIR="/var/lib/agentnode-cw-$INSTANCE"
UNIT="agentnode-cw-$INSTANCE"
case "$INSTANCE" in *[!a-z0-9-]*|"") echo "instance must be [a-z0-9-]" >&2; exit 2;; esac
systemctl disable --now "$UNIT.service" "$UNIT-runtime.service" 2>/dev/null
if id "$USER_NAME" >/dev/null 2>&1; then
    UID_N="$(id -u "$USER_NAME")"
    runuser -u "$USER_NAME" -- env HOME="$HOME_DIR" XDG_RUNTIME_DIR="/run/user/$UID_N" podman system reset --force >/dev/null 2>&1
    loginctl disable-linger "$USER_NAME" 2>/dev/null
    systemctl stop "user@$UID_N.service" 2>/dev/null
    pkill -u "$USER_NAME" 2>/dev/null; sleep 1
    userdel -r "$USER_NAME" 2>/dev/null
    sed -i "/^$USER_NAME:/d" /etc/subuid /etc/subgid
fi
rm -rf "$HOME_DIR"
rm -f "/etc/systemd/system/$UNIT.service" "/etc/systemd/system/$UNIT-runtime.service" "/etc/agentnode/$UNIT.env"
nft delete table inet "agentnode_cw_$INSTANCE" 2>/dev/null
systemctl daemon-reload
echo "left: account=$(id "$USER_NAME" >/dev/null 2>&1 && echo yes || echo no)" \
     "state=$(test -e "$HOME_DIR" && echo yes || echo no)" \
     "unit=$(test -e "/etc/systemd/system/$UNIT.service" && echo yes || echo no)" \
     "nft=$(nft list table inet "agentnode_cw_$INSTANCE" >/dev/null 2>&1 && echo yes || echo no)" \
     "subids=$(grep -c "^$USER_NAME:" /etc/subuid 2>/dev/null)"
