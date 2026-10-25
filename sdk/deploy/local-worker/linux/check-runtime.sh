#!/bin/sh
# AgentNode local worker -- the runtime identity and enforcement check, run as root BEFORE EVERY START of the agent
# (ExecStartPre=+) and by the installer and updater (local-worker slice 2, Codex D8 as amended by Q-LW2-0006/0007).
#
#   sh check-runtime.sh <release-dir>      exit 0, or a named refusal on stderr and exit 2
#
# Refuses (`runtime_identity_unsafe`, `cgroup_controllers_missing`, `subid_unavailable`, `runtime-absent`) unless:
#   * /usr/bin/newuidmap and /usr/bin/newgidmap are non-symlink regular files, root:root 0755, carrying exactly
#     cap_setuid=ep / cap_setgid=ep, owned by EXACTLY the shadow-utils package the signed manifest names (the only
#     privilege the unit's bounding set lets through);
#   * the account agentnode-agent has no login shell, no password, lingering on, an active user@<uid>.service;
#   * /run/user/<uid> is a tmpfs owned by agentnode-agent, mode 0700;
#   * its user manager delegates cpu, memory and pids, and a probe container actually gets the memory, CPU and PID
#     limits it asks for (a limit that is only requested is not a limit).
set -u
PATH=/usr/sbin:/usr/bin:/sbin:/bin
REL="${1:?release directory}"
U=agentnode-agent
refuse() { echo "REFUSED $1${2:+: $2}" >&2; exit 2; }
id "$U" >/dev/null 2>&1 || refuse runtime_identity_unsafe "no $U account"
UIDN=$(id -u "$U")
NEVRA=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["compat"]["shadow_utils"])' "$REL/manifest.json") \
    || refuse runtime_identity_unsafe "the release names no shadow-utils package"
IMAGE=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["compat"]["sandbox_image"])' "$REL/manifest.json")
command -v getcap >/dev/null 2>&1 || refuse runtime_identity_unsafe "getcap is missing"
for pair in newuidmap:cap_setuid newgidmap:cap_setgid; do
    h=${pair%%:*}; cap=${pair#*:}; P=/usr/bin/$h
    [ -f "$P" ] && [ ! -L "$P" ] || refuse subid_unavailable "$P is not a regular file"
    [ "$(stat -c '%U:%G %a' "$P")" = "root:root 755" ] || refuse runtime_identity_unsafe "$P is not root:root 0755"
    [ "$(getcap "$P" | sed 's/^[^ ]* //')" = "$cap=ep" ] || refuse runtime_identity_unsafe "$P does not carry exactly $cap=ep"
    [ "$(rpm -qf "$P" 2>/dev/null)" = "$NEVRA" ] || refuse runtime_identity_unsafe "$P is not owned by $NEVRA"
    rpm -V "$NEVRA" 2>/dev/null | grep -q " $P\$" && refuse runtime_identity_unsafe "$P differs from $NEVRA"
done
[ "$(getent passwd "$U" | cut -d: -f7)" = "/sbin/nologin" ] || refuse runtime_identity_unsafe "$U has a login shell"
case "$(getent shadow "$U" | cut -d: -f2)" in '!'*|'*'*|'!!') ;; *) refuse runtime_identity_unsafe "$U has a password";; esac
grep -q "^$U:" /etc/subuid && grep -q "^$U:" /etc/subgid || refuse subid_unavailable "no subordinate ids for $U"
[ -e "/var/lib/systemd/linger/$U" ] || refuse runtime_identity_unsafe "lingering is off for $U"
systemctl is-active --quiet "user@$UIDN.service" || systemctl start "user@$UIDN.service" || refuse runtime_identity_unsafe "no user manager for $U"
RD=/run/user/$UIDN
[ "$(stat -f -c '%T' "$RD" 2>/dev/null)" = tmpfs ] || refuse runtime_identity_unsafe "$RD is not a tmpfs"
[ "$(stat -c '%U %a' "$RD")" = "$U 700" ] || refuse runtime_identity_unsafe "$RD is not $U's, 0700"
CG=/sys/fs/cgroup/user.slice/user-$UIDN.slice/user@$UIDN.service/cgroup.controllers
for c in cpu memory pids; do grep -qw "$c" "$CG" 2>/dev/null || refuse cgroup_controllers_missing "$c is not delegated to $U"; done
got=$(cd /var/lib/agentnode-agent && runuser -u "$U" -- env HOME=/var/lib/agentnode-agent XDG_RUNTIME_DIR="$RD" \
      /usr/bin/podman run --rm --network none --memory 64m --cpus 1 --pids-limit 32 "$IMAGE" \
      sh -c 'cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/cpu.max /sys/fs/cgroup/pids.max' 2>/dev/null | tr '\n' ' ')
[ "$got" = "67108864 100000 100000 32 " ] || refuse runtime-absent "the runtime does not enforce the limits it is asked for ($got)"
echo "  runtime check: helpers, account, user manager, $RD, delegation and enforced limits are as required"
