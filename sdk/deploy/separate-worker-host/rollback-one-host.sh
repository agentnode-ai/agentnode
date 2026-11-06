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
# ONE CASE REVERSES STEPS 2 AND 3, and it is not an exception to the rule above so much as a
# consequence of it. Rolling the worker back to a build from before the refusal codes were
# fixed needs the control plane on the same generation, and the worker checks that itself from
# a statement the control plane SIGNS after its own rollback -- not from an operator's word
# (that was `--control-plane-already-rolled-back`, and it is refused now). The order is:
#
#   1. stop admission (unchanged);
#   2. on the worker:        rollback-one-host.sh <keep> --challenge <acceptance run id>
#      which only writes a single-use challenge and changes nothing else;
#   3. roll the CONTROL PLANE back first, and have it answer:
#      on the control plane: rollback-one-host.sh <keep> --sign-for <that challenge file>
#      which rolls the control plane back and, once the running process named the kept build,
#      signs what it now runs with the gateway's key;
#   4. on the worker:        rollback-one-host.sh <keep> --control-plane-statement <that file>
#      which verifies it (CA, gateway identity, signature, this worker, this challenge, this
#      run, this keep's build and generation, freshness), uses the challenge up, and goes on;
#   5. re-measure before reopening (unchanged, and see below).
#
# The reason step 2 normally puts the worker first -- get incompatible execution code out
# while the gateway is fail-closed -- is satisfied either way here, because admission is
# stopped for the whole window and nothing runs during the mismatch.
#
# EXPECT THE GATEWAY TO SAY "Not protecting" FOR SOME MINUTES after its own rollback, until it
# is re-measured. Measured on the pair: 300 seconds. It is not broken and it is not slow to
# start; it will not trade on a measurement that a different build took, so it refuses work
# until it has taken its own. Plan the window around that rather than being surprised by it.
#
# What survives a rollback, and should: the worker's journal, so a run that was in flight across
# it is still answerable afterwards instead of being lost. What does not, and should not: the
# lease. The gateway takes a new one with a higher epoch, and anything issued under the old one
# stops counting at the worker.

set -uo pipefail

KEEP="${1:-}"
PREFIX=/opt/agentnode

# WHAT THE SECOND ARGUMENT MAY BE (see the order above). The waiver of the worker-side
# refusal-code check below is set ONLY from a statement that verified, never from a word on the
# command line. It deliberately does NOT relax the lease-counter check, which runs first and is
# unsafe in both directions no matter what the other host is running.
step() { printf '\n=== %s\n' "$*"; }
died() { printf '\n!!! FAILED: %s\n' "$*"; exit 1; }

PAIRED=""
MODE="${2:-}"
MODE_ARG="${3:-}"
case "$MODE" in
  ""|--challenge|--sign-for|--control-plane-statement) ;;
  "--control-plane-already-rolled-back")
    died "--control-plane-already-rolled-back is no longer accepted: it was an assertion this
  script could not check. The worker checks the control plane's own signed statement instead:

      on the worker:         rollback-one-host.sh <keep> --challenge <acceptance run id>
      on the control plane:  rollback-one-host.sh <keep> --sign-for <the challenge file>
      on the worker:         rollback-one-host.sh <keep> --control-plane-statement <the statement>" ;;
  *) died "unknown option: $MODE" ;;
esac
if [ -n "$MODE" ] && [ -z "$MODE_ARG" ]; then
  died "$MODE needs a value"
fi

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
# THE PAIRED-ROLLBACK EVIDENCE (see the order at the top). Everything here runs with the code
# installed NOW -- the newer build, which has it -- and before anything is stopped.
PY="$PREFIX/venv/bin/python"
case "$MODE" in
  --challenge)
    [ "$UNIT" = "agentnode-worker" ] || died "--challenge is the worker's half. On the control
  plane the answer is: rollback-one-host.sh <keep> --sign-for <the worker's challenge file>"
    step "a single-use challenge for the control plane (nothing is stopped or changed)"
    if ! "$PY" -m agentnode_sdk.worker.rollback_proof challenge --keep "$KEEP" \
           --run-id "$MODE_ARG" --out "$KEEP/rollback-challenge.json" | sed 's/^/   /'; then
      died "no challenge was issued"
    fi
    printf '\n=== Copy %s to the control plane and run there:\n' "$KEEP/rollback-challenge.json"
    printf '===   rollback-one-host.sh <its keep> --sign-for <that file>\n'
    printf '=== then bring its control-plane-statement.json back here. Nothing here was stopped.\n'
    exit 0 ;;
  --control-plane-statement)
    [ "$UNIT" = "agentnode-worker" ] || died "--control-plane-statement is checked by the worker"
    step "the control plane's signed statement"
    if ! "$PY" -m agentnode_sdk.worker.rollback_proof verify --keep "$KEEP" \
           --statement "$MODE_ARG" | sed 's/^/   /'; then
      died "the control plane's statement did not verify, so nothing is waived and nothing was
  stopped. Issue a new challenge (--challenge) and have the control plane answer it after its
  own rollback."
    fi
    PAIRED=yes ;;
  --sign-for)
    [ "$UNIT" = "agentnode-gateway" ] || died "--sign-for is the control plane's half"
    [ -f "$MODE_ARG" ] || died "no such challenge file: $MODE_ARG"
    # The signing code is copied out of the CURRENT installation now, because the build being
    # restored may be one that does not have it; the statement is signed only after that build
    # is confirmed running (step 7). The copy stays in the keep, as the record of what signed.
    SIGN_SITE="$(ls -d "$PREFIX"/venv/lib/python3*/site-packages 2>/dev/null | head -1)"
    [ -f "$SIGN_SITE/agentnode_sdk/worker/rollback_proof.py" ] || died "the build installed now
  cannot sign a statement, so the worker could not be rolled back with this control plane"
    NEW_CODE="$KEEP/signing-code-$(date -u +%s)"
    mkdir "$NEW_CODE" || died "could not make $NEW_CODE"
    cp -a "$SIGN_SITE/agentnode_sdk" "$NEW_CODE/" || died "could not keep the signing code aside"
    GW_TLS="$("$PY" -c 'import json,sys; t=json.load(open(sys.argv[1]))["worker_tls"]; print(t["certificate"]); print(t["key"])' /var/lib/agentnode/state/config.json 2>/dev/null)"
    GW_CERT="$(printf '%s\n' "$GW_TLS" | sed -n 1p)"; GW_KEY="$(printf '%s\n' "$GW_TLS" | sed -n 2p)"
    [ -f "$GW_CERT" ] && [ -f "$GW_KEY" ] || died "the gateway's certificate and key are not where
  its config says, so it cannot sign" ;;
esac

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

# THE SECOND ROLLBACK THIS MUST REFUSE, and this one was found by doing it.
#
# Six refusal codes used to be missing from `ERRORS`, so `refusal()` rewrote them to
# `internal` -- `NO_LEASE` among them. A worker from before that fix therefore cannot say
# "no lease" on the wire; it says "internal".
#
# That matters here and not only cosmetically. The re-acquisition this repair added keys on
# the CAUSE of the refusal. Against a worker that cannot name it, the gateway never re-takes
# the lease, and the pair does not recover on its own. Measured on the pair, worker rolled
# back to managed-f6996a45ef4a and gateway left at head:
#
#   refused the request (internal): this instruction names epoch 33 and the live lease is
#   epoch 34
#   refused the request (internal): this worker holds no lease, so nothing may give it work
#
# Four jobs in a row, spaced, nothing restarted, and every one of them refused. The pair was
# wedged until the worker was rolled forward again.
#
# The OTHER direction is fine and is not refused: a GATEWAY rolled back across the same
# change ran three jobs out of three, because the rewriting happens on the worker's side and
# an old gateway still receives correctly named codes. This guard is worker-only for that
# reason, and it was measured in both directions rather than assumed in either.
#
# HOW IT IS DETECTED, because the obvious check is wrong. `NO_LEASE = "no-lease"` has been
# DEFINED in protocol.py all along; the defect was that it was missing from the ERRORS tuple,
# which is what `refusal()` consults. Grepping the file for "no-lease" therefore matches every
# build ever shipped and would never refuse anything. The first version of this guard did
# exactly that, and the keep inventory it produced called a pre-fix build fine. What has to be
# read is the ERRORS tuple itself.
if [ "$UNIT" = "agentnode-worker" ] && [ -z "$PAIRED" ]; then
  KEPT_PROTOCOL="$(tar -xOf "$KEEP/installed-package.tar" agentnode_sdk/worker/protocol.py 2>/dev/null || true)"
  KEPT_ERRORS="$(printf '%s' "$KEPT_PROTOCOL" | awk '/^ERRORS[[:space:]]*=/,/\)/')"
  if [ -n "$KEPT_PROTOCOL" ] && ! printf '%s' "$KEPT_ERRORS" | grep -q 'NO_LEASE'; then
    died "this keep is from a build whose ERRORS table does not carry \"no-lease\", so every
  lease refusal it sends arrives at the control plane as \"internal\".

  The control plane re-takes a lapsed lease by looking at the CAUSE of the refusal. A worker
  that cannot name the cause never triggers it, so the pair stops recovering: the gateway
  keeps reissuing work under an epoch the worker has already moved past, and the worker keeps
  refusing it. Measured on the pair -- four jobs, spaced, nothing restarted, every one
  refused, until the worker was rolled forward again.

  WHAT TO DO INSTEAD. Roll forward. If this build genuinely has to go back, roll the CONTROL
  PLANE back with it, to a build from before the same change, so that the two agree about
  what a refusal is called:

      here:                  rollback-one-host.sh $KEEP --challenge <acceptance run id>
      on the control plane:  rollback-one-host.sh <a keep from the same generation> \\
                                 --sign-for <that challenge file>
      then here:             rollback-one-host.sh $KEEP --control-plane-statement <its statement>

  The statement is signed by the gateway after its own rollback was confirmed, and this worker
  checks it itself: this deployment's CA, an accepted gateway, the signature, this worker, this
  challenge, this run, this keep's build and generation, and its age. It is used up once it
  has waived this check.

  Rolling the control plane back on its own is safe and is not refused: an older gateway
  still understands a correctly named refusal from a newer worker."
  fi
fi

if [ -n "$PAIRED" ] && [ "$UNIT" = "agentnode-worker" ]; then
  step "the refusal-code check was waived by the control plane's verified statement"
  printf '   The gateway signed, after its own rollback, that it runs the same refusal-code\n'
  printf '   generation as this keep; this worker checked the signature, the identities, the\n'
  printf '   challenge, the run, the build and the age, and used the challenge up.\n'
  printf '   The lease-counter check, which ran before this, is NOT waived.\n'
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

if [ "$MODE" = "--sign-for" ]; then
  # Only now: the running process has named the kept build. The generation is read from the
  # ERRORS table that is installed here now; the build is the one the process announced.
  step "7. the statement the worker needs"
  STATEMENT_OUT="$KEEP/control-plane-statement.json"
  if ! PYTHONPATH="$NEW_CODE" "$PY" -m agentnode_sdk.worker.rollback_proof sign \
         --challenge "$MODE_ARG" --certificate "$GW_CERT" --key "$GW_KEY" --site "$SITE" \
         --build "$SAID_IT_IS" --out "$STATEMENT_OUT" | sed 's/^/   /'; then
    died "the control plane is rolled back but could not sign its statement, so the worker
  cannot use it. The gateway's rollback itself stands."
  fi
  printf '   copy %s to the worker and run there:\n' "$STATEMENT_OUT"
  printf '     rollback-one-host.sh <its keep> --control-plane-statement <that file>\n'
fi

printf '\n=== %s is back on the kept build. The other host was NOT touched.\n' "$UNIT"
printf '=== Re-measure before admission is reopened. A rollback that was not measured after is\n'
printf '=== a rollback nobody can say worked.\n'
if [ "$UNIT" = "agentnode-worker" ]; then
  printf '\n=== The journal survived, so runs in flight across this are still answerable:\n'
  printf '===   agentnode worker preflight ... reports how many are unsettled.\n'
fi
