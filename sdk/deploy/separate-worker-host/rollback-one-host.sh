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

step "4. the pin that goes with it"
# The code and the pin are one thing. The tar carries `AGENTNODE_ARTEFACT` back with the
# dist-info; without the pin beside it the two disagree and the next start is refused for
# naming an artefact the installation does not record. A rollback that restores the bytes
# and not the permission to run them is not a rollback.
[ -f "$KEEP/runtime-pin.json" ] || died "$KEEP holds no runtime-pin.json. It was made by an
  upgrade that did not keep one, so the code can be put back but its pin cannot. Reinstall
  from the artefact instead."
install -m 0644 "$KEEP/runtime-pin.json" /etc/agentnode/runtime-pin.json
WAS_BUILD_ID="$(cat "$KEEP/build-id.txt" 2>/dev/null || echo "")"
[ -n "$WAS_BUILD_ID" ] || died "$KEEP does not say which build it came from, so a rollback to
  it cannot be checked. Reinstall from the artefact instead."
echo "   pin restored; expecting $WAS_BUILD_ID"

step "5. the unit it was started by"
install -m 0644 "$KEEP/$UNIT.service" /etc/systemd/system/"$UNIT".service
systemctl daemon-reload

step "6. start it, and check it is the OLD code that came up"
STARTED_AT="$(date -u +%s)"
systemctl start "$UNIT".service || died "it did not start"
sleep 3
systemctl is-active --quiet "$UNIT".service || {
  journalctl -u "$UNIT".service -n 40 --no-pager | sed 's/^/   /'
  died "it did not come back"; }

# COMPARED, not printed side by side. This used to print the running version string and the
# kept one and leave the reader to notice -- and a version string is explicitly not an
# identity: a development wheel keeps its number while its contents change, which is the
# reason the upgrade force-reinstalls. The build id is derived from the commit and the
# artefact digest, so it cannot agree by coincidence.
SAID_IT_IS="$(journalctl -u "$UNIT".service --since "@$STARTED_AT" --no-pager 2>/dev/null \
  | grep -o 'managed-[0-9a-f]\{1,\}+[0-9a-f]\{1,\}' | tail -1)"
[ -n "$SAID_IT_IS" ] || died "$UNIT came up but never said which build it is, so this
  rollback cannot be confirmed."
[ "$SAID_IT_IS" = "$WAS_BUILD_ID" ] || died "$UNIT is serving $SAID_IT_IS and the kept build
  was $WAS_BUILD_ID. The rollback put files back and the running process is not them."
echo "   $UNIT is up and says it is $SAID_IT_IS, which is the build that was kept"

printf '\n=== %s is back on the kept build. The other host was NOT touched.\n' "$UNIT"
printf '=== Re-measure before admission is reopened. A rollback that was not measured after is\n'
printf '=== a rollback nobody can say worked.\n'
if [ "$UNIT" = "agentnode-worker" ]; then
  printf '\n=== The journal survived, so runs in flight across this are still answerable:\n'
  printf '===   agentnode worker preflight ... reports how many are unsettled.\n'
fi
