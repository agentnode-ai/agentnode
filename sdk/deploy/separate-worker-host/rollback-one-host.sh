#!/usr/bin/env bash
# Put THIS HOST back to the build in an upgrade's keep-directory. One host, one direction.
#
# THE ORDER ACROSS THE PAIR, which this script cannot enforce and states instead:
#
#   1. stop admission on the control plane:
#        agentnode gateway stop --reason "rolling back"
#      which also ends the runs that are going -- the reason to stop at once is usually the code
#      running right now;
#   2. roll the WORKER back first, so incompatible execution code is gone while the gateway is
#      fail-closed;
#   3. then the control plane;
#   4. re-measure before reopening.
#
# If the incident is demonstrably gateway-only, the gateway may go first. What may not happen is
# work running during an unverified mismatch.
#
# What survives a rollback, and should: the worker's journal, so a run that was in flight across
# it is still answerable afterwards instead of being lost. What does not, and should not: the
# lease. The gateway takes a new one with a higher epoch, and anything issued under the old one
# stops counting at the worker.

set -uo pipefail

KEEP="${1:-}"
PREFIX=/opt/agentnode

step() { printf '\n=== %s\n' "$*"; }
died() { printf '\n!!! FAILED: %s\n' "$*"; exit 1; }

[ "$(id -u)" = "0" ] || died "this restarts a system service, so it needs root"
if [ -z "$KEEP" ]; then
  printf 'usage: rollback-one-host.sh <keep-directory>\n\nAvailable here:\n'
  ls -1dt /root/agentnode-upgrade-*/ 2>/dev/null | sed 's/^/  /' || echo "  (none)"
  exit 2
fi
[ -d "$KEEP" ] || died "no such directory: $KEEP"
[ -f "$KEEP/installed-package.tar" ] || died "$KEEP holds no package to put back"

UNIT=""
for candidate in agentnode-worker agentnode-gateway; do
  [ -f "$KEEP/$candidate.service" ] && UNIT="$candidate"
done
[ -n "$UNIT" ] || died "$KEEP holds no unit file, so it does not say which role it is from"
systemctl list-unit-files "$UNIT.service" --no-legend 2>/dev/null | grep -q . \
  || died "$KEEP is from a $UNIT host and this machine does not run one. A rollback artefact
  from the OTHER machine must not be applied here."
step "this host runs $UNIT; rolling back to $KEEP"

step "1. what is running now, before it is replaced"
{ systemctl is-active "$UNIT"; "$PREFIX/venv/bin/agentnode" --version 2>/dev/null; } | sed 's/^/   /'

step "2. stop it"
# Stopped rather than restarted-into: replacing the code under a running process leaves it
# serving what it loaded at start, which is the failure that looks exactly like success.
systemctl stop "$UNIT".service || died "could not stop $UNIT"

step "3. the code that was there"
SITE="$(ls -d "$PREFIX"/venv/lib/python3*/site-packages 2>/dev/null | head -1)"
[ -n "$SITE" ] || died "no site-packages under $PREFIX/venv"
rm -rf "$SITE/agentnode_sdk" "$SITE"/agentnode_sdk-*.dist-info
tar -C "$SITE" -xf "$KEEP/installed-package.tar" || died "could not unpack the kept package"
echo "   back to: $("$PREFIX/venv/bin/agentnode" --version 2>/dev/null || echo unknown)"

step "4. the unit it was started by"
install -m 0644 "$KEEP/$UNIT.service" /etc/systemd/system/"$UNIT".service
systemctl daemon-reload

step "5. start it, and check it is the OLD code that came up"
systemctl start "$UNIT".service || died "it did not start"
sleep 3
systemctl is-active --quiet "$UNIT".service || {
  journalctl -u "$UNIT".service -n 40 --no-pager | sed 's/^/   /'
  died "it did not come back"; }
NOW="$("$PREFIX/venv/bin/agentnode" --version 2>/dev/null || echo unknown)"
WAS="$(grep -o 'agentnode.*' "$KEEP/what-was-running.txt" | tail -1)"
printf '   running: %s\n   the keep says it was: %s\n' "$NOW" "$WAS"

printf '\n=== %s is back on the kept build. The other host was NOT touched.\n' "$UNIT"
printf '=== Re-measure before admission is reopened. A rollback that was not measured after is\n'
printf '=== a rollback nobody can say worked.\n'
if [ "$UNIT" = "agentnode-worker" ]; then
  printf '\n=== The journal survived, so runs in flight across this are still answerable:\n'
  printf '===   agentnode worker preflight ... reports how many are unsettled.\n'
fi
