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

#: See upgrade-one-host.sh: a gateway took 71 seconds on the pair between being started and
#: printing which build it is. Waited for, not slept through.
BUILD_ID_PATIENCE=180

wait_for_the_build_id() {
    _unit="$1"; _since="$2"; _waited=0
    while [ "$_waited" -lt "$BUILD_ID_PATIENCE" ]; do
        _said="$(journalctl -u "$_unit".service --since "@$_since" --no-pager 2>/dev/null \
                 | grep -o 'managed-[0-9a-f]\{1,\}+[0-9a-f]\{1,\}' | tail -1)"
        if [ -n "$_said" ]; then printf '%s' "$_said"; return 0; fi
        sleep 3; _waited=$((_waited + 3))
    done
    return 1
}

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

# ---------------------------------------------------------------------------------------------
# ONE ROLLBACK THIS MUST REFUSE, and it is one this repair itself created.
#
# The lease epoch counter used to live INSIDE the run journal, where the journal read it as a
# record with no run id and the worker died at every start. It now lives beside the journal,
# and the number is carried across on first start.
#
# A build from before that move looks for the counter in the journal directory. After the move
# it is not there, `_read_counter` reads a missing file as zero, and the next lease is epoch 1
# AGAIN -- so every instruction a retired gateway still holds under epoch 1 becomes valid. That
# is precisely the property the counter exists to provide, and losing it silently during a
# rollback is worse than not being able to roll back.
#
# Putting the file back is not a way out: the old journal enumerates that directory, so the
# crash-loop this repair removed would return with it. The two layouts are not compatible in
# either direction, and the honest thing is to say so here rather than to do it quietly.
#
# Checked BEFORE anything is stopped or unpacked, and only when there is actually a number to
# lose: a host that has never issued a lease has nothing at stake and is let through.
if [ "$UNIT" = "agentnode-worker" ] && [ -f /var/lib/agentnode-worker/lease-epoch.json ]; then
  KEPT_SERVICE="$(tar -xOf "$KEEP/installed-package.tar" agentnode_sdk/worker/service.py 2>/dev/null || true)"
  if [ -n "$KEPT_SERVICE" ] && ! printf '%s' "$KEPT_SERVICE" | grep -q 'legacy=legacy'; then
    died "this keep is from a build that reads the lease counter from inside the journal
  directory, and this host now keeps it beside the journal at
  /var/lib/agentnode-worker/lease-epoch.json (currently $(cat /var/lib/agentnode-worker/lease-epoch.json)).

  Rolling back would make the old code find no counter, read that as zero, and hand out epoch
  1 again -- which makes any instruction a retired control plane still holds valid. Moving the
  file back is not a way out either: the old journal reads it as a run record and the worker
  will not start at all.

  WHAT TO DO INSTEAD. Roll forward, or if this build genuinely has to go back, retire this
  worker's identity first so that no gateway holds an epoch for it:

      on the control plane:  agentnode pki revoke --serial <this worker's serial>
                             agentnode pki add --role worker --instance <a NEW instance>
      then install the older artefact here with install.sh and enrol the new identity.

  A reissued epoch is harmless for an identity nobody holds a lease against."
  fi
fi

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
SAID_IT_IS="$(wait_for_the_build_id "$UNIT" "$STARTED_AT")"
if [ -z "$SAID_IT_IS" ]; then
  # WHY IT SAID NOTHING DECIDES WHAT THIS IS. A build from before the identity line was
  # flushed cannot announce itself while it runs: under systemd stdout is a pipe, Python
  # block-buffers it, and the line reaches the journal only when the process exits. Measured
  # on the pair -- every `Running as ...` arrived in the same second as the following
  # "Stopped".
  #
  # So a rollback TO such a build is not confirmable by anybody, and reporting that as a
  # failed rollback would be wrong: the code went back, and what is missing is the proof, not
  # the result. "Could not be established" is a different answer from "established false" and
  # they are not interchangeable.
  RESTORED_PIN_SOURCE="$SITE/agentnode_sdk/gateway/runtime_pin.py"
  if [ -f "$RESTORED_PIN_SOURCE" ] && ! grep -q 'flush=True' "$RESTORED_PIN_SOURCE"; then
    printf '\n!!! ROLLED BACK, NOT CONFIRMED\n'
    printf '    %s is running and the kept code and pin are in place, but the build it was\n' "$UNIT"
    printf '    rolled back to does not flush the line that says which build it is, so it\n'
    printf '    cannot announce itself while it runs. Nothing can read it from outside.\n\n'
    printf '    The rollback itself is done. What is missing is the proof, and it is missing\n'
    printf '    because of the build that was restored -- not because anything went wrong.\n\n'
    printf '    To see it for yourself, stop the service and read the last lines: the buffer\n'
    printf '    flushes on exit and the id appears then.\n'
    printf '      systemctl stop %s && journalctl -u %s -n 20 --no-pager\n' "$UNIT" "$UNIT"
    printf '    Expected: %s\n\n' "$WAS_BUILD_ID"
    exit 3
  fi
  died "$UNIT came up but never said which build it is within ${BUILD_ID_PATIENCE}s, and the
  build that was restored DOES flush that line -- so this is a real failure and not a build
  that cannot speak. Look at: journalctl -u $UNIT -n 40 --no-pager"
fi
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
