#!/bin/sh
# AgentNode local worker -- complete removal (local-worker slice 2, Codex Q-LW2-0001 D7).
#
#   sh uninstall.sh [--revoke]
#
# --revoke first asks the control plane, with this worker's own key, to retire it (its device is withdrawn and its
# identity tombstoned there). Without it -- or when that cannot be reached -- withdraw the device in your device list;
# until then nothing can run on this machine anyway, because nothing of the agent is left here.
#
# Removes: the units, every container/image/volume of the agent's account, the account (its home is the state:
# key, certificate, pair key, journal, logs of the runtime), its subordinate id range, its linger, /opt/agentnode-agent,
# /etc/agentnode-agent, root's time floor and release floor. Leaves nothing listening and nothing scheduled.
set -u
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
U=agentnode-agent
STATE=/var/lib/agentnode-agent
REVOKE=no
[ "${1:-}" = "--revoke" ] && REVOKE=yes
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 2; }
if id "$U" >/dev/null 2>&1; then UIDN=$(id -u "$U"); else UIDN=""; fi
asuser() { ( cd "$STATE" && runuser -u "$U" -- env HOME="$STATE" XDG_RUNTIME_DIR="/run/user/$UIDN" "$@" ); }
if [ "$REVOKE" = yes ] && [ -n "$UIDN" ] && [ -f "$STATE/tls/target.json" ]; then
    asuser /opt/agentnode-agent/current/venv/bin/agentnode worker retire --pins /etc/agentnode-agent/pins.json \
        --state-dir "$STATE/tls" || echo "  (not retired at the control plane: withdraw the device in your device list)"
elif [ -f "$STATE/tls/target.json" ]; then
    echo "  this machine was device $(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["device_id"])' "$STATE/tls/target.json") -- withdraw it in your device list"
fi
for unit in agentnode-agent.service agentnode-agent-floor.timer agentnode-agent-floor.service; do
    systemctl disable --now "$unit" >/dev/null 2>&1
done
rm -f /etc/systemd/system/agentnode-agent.service /etc/systemd/system/agentnode-agent-floor.service \
      /etc/systemd/system/agentnode-agent-floor.timer
rm -rf /etc/systemd/system/agentnode-agent.service.d
systemctl daemon-reload
if [ -n "$UIDN" ]; then
    asuser /usr/bin/podman rm -af >/dev/null 2>&1
    asuser /usr/bin/podman system reset -f >/dev/null 2>&1
    loginctl disable-linger "$U" 2>/dev/null
    systemctl stop "user@$UIDN.service" 2>/dev/null
    pkill -u "$U" 2>/dev/null; sleep 1; pkill -9 -u "$U" 2>/dev/null
    S=$(awk -F: -v u="$U" '$1==u {print $2"-"$2+$3-1}' /etc/subuid)
    G=$(awk -F: -v u="$U" '$1==u {print $2"-"$2+$3-1}' /etc/subgid)
    [ -n "$S" ] && usermod --del-subuids "$S" "$U" 2>/dev/null
    [ -n "$G" ] && usermod --del-subgids "$G" "$U" 2>/dev/null
    userdel -r "$U" 2>/dev/null
fi
rm -rf "$STATE" /opt/agentnode-agent /etc/agentnode-agent /var/lib/agentnode-agent-floor /var/lib/agentnode-agent-release \
       "/run/user/$UIDN" 2>/dev/null
left=""
id "$U" >/dev/null 2>&1 && left="$left user"
grep -q "^$U:" /etc/subuid /etc/subgid 2>/dev/null && left="$left subids"
for p in "$STATE" /opt/agentnode-agent /etc/agentnode-agent /var/lib/agentnode-agent-floor /var/lib/agentnode-agent-release \
         /etc/systemd/system/agentnode-agent.service; do [ -e "$p" ] && left="$left $p"; done
if [ -n "$left" ]; then echo "  NOT completely removed:$left" >&2; exit 1; fi
echo "  removed completely: no account, no files, no units, no subordinate ids."
