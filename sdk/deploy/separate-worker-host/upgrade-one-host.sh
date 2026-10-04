#!/usr/bin/env bash
# Upgrade THE HOST THIS IS RUN ON, and nothing else.
#
# The two hosts are deliberately not in lockstep. A security update that cannot be applied to one
# without the other is a worse failure than the skew it would prevent, so this upgrades one side
# and leaves the other alone. What keeps that safe is not coordination, it is the wire version:
# `describe` reports each side's TESTED range on every connection, and no common version is a
# refusal that names both ranges and both builds. There is no downgrade to whatever both happen
# to understand.
#
# What it keeps first, so that rollback-one-host.sh has something to put back:
#
#   the installed package  -- the CODE as bytes. A version number is not an identity.
#   the unit file          -- how it is started, and as which account.
#   the runtime pin        -- WHICH build this host is allowed to run.
#   the build id           -- what the old code called itself, so a rollback can be checked.
#   what was running       -- so "it came back" can be checked against what was there.
#
# It does not touch the state, the journal, the keyring or the certificates. An upgrade that
# rewrote those would be a migration, and a migration is not this.
#
# TWO THINGS THIS DID NOT DO, AND HAD TO.
#
# It reinstalled the wheel and left `AGENTNODE_ARTEFACT` -- which lives inside the dist-info
# pip had just replaced -- gone, and `runtime-pin.json` naming the OLD wheel. The pin check
# then refuses at the next start, because the pin names one artefact and the installation
# records none. An upgrade that cannot be followed by a start is not an upgrade; it was never
# run across two machines, so nobody found out.
#
# And it proved "it is the new code" with a file timestamp: started-after-the-files-were-
# written. That cannot tell two builds apart, only two orders of events. The product already
# prints its own identity on startup -- `Running as managed-<commit>+<artefact>` -- and that
# is what is compared now.

set -uo pipefail

WHEEL="${1:-}"
PREFIX=/opt/agentnode
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
KEEP="/root/agentnode-upgrade-${STAMP}"

step() { printf '\n=== %s\n' "$*"; }
died() { printf '\n!!! FAILED: %s\n' "$*"; exit 1; }

#: How long to wait for a service to say which build it is. WAITED FOR, NOT SLEPT THROUGH:
#: measured on the pair, a gateway took 71 seconds between systemd starting it and printing
#: `Running as managed-...`, because it settles its state and reaches its worker first. A
#: fixed three-second sleep read the journal before the line existed and reported "it never
#: said which build it is" about a service that was perfectly fine -- which is a false
#: failure on the one check that is supposed to be the proof.
BUILD_ID_PATIENCE=180

wait_for_the_build_id() {
    # $1 unit, $2 the epoch the restart was ordered at. Prints the id, or nothing.
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
[ -n "$WHEEL" ] || died "usage: upgrade-one-host.sh <wheel>"
[ -f "$WHEEL" ] || died "no wheel at $WHEEL"

# WHICH HOST AM I? Read from what is installed here rather than given as an argument: an argument
# is a thing somebody can get wrong at 3am, and both units are never present on one machine in
# this topology.
UNIT=""
for candidate in agentnode-worker agentnode-gateway; do
  systemctl list-unit-files "$candidate.service" --no-legend 2>/dev/null | grep -q . && UNIT="$candidate"
done
[ -n "$UNIT" ] || died "neither agentnode-worker nor agentnode-gateway is installed here"
if systemctl list-unit-files agentnode-worker.service --no-legend 2>/dev/null | grep -q . && \
   systemctl list-unit-files agentnode-gateway.service --no-legend 2>/dev/null | grep -q .; then
  died "both units are installed on this machine, so this is not a separate-worker-host
  deployment. Use deploy/deploy-pinned.sh, which knows about the pair."
fi
step "this host runs $UNIT"

mkdir -p "$KEEP" && chmod 700 "$KEEP" || died "could not make $KEEP"

step "1. what is running right now"
{
  systemctl is-active "$UNIT"
  systemctl show "$UNIT" -p User -p ExecStart
  "$PREFIX/venv/bin/agentnode" --version 2>/dev/null
} > "$KEEP/what-was-running.txt" 2>&1
sed 's/^/   /' "$KEEP/what-was-running.txt"

step "2. the package that is installed now, as bytes"
SITE="$(ls -d "$PREFIX"/venv/lib/python3*/site-packages 2>/dev/null | head -1)"
[ -n "$SITE" ] || died "no site-packages under $PREFIX/venv"
DIST="$(cd "$SITE" && ls -d agentnode_sdk-*.dist-info 2>/dev/null | head -1)"
[ -n "$DIST" ] || died "no agentnode_sdk dist-info under $SITE"
tar -C "$SITE" -cf "$KEEP/installed-package.tar" agentnode_sdk "$DIST" \
  || died "could not archive the installed package"
cp /etc/systemd/system/"$UNIT".service "$KEEP/" || died "no unit file to keep"
# THE PIN, AND WHAT THE OLD CODE CALLS ITSELF. Without the first a rollback restores code the
# pin then refuses; without the second a rollback has nothing to check itself against.
cp /etc/agentnode/runtime-pin.json "$KEEP/" 2>/dev/null \
  || died "there is no /etc/agentnode/runtime-pin.json to keep, so this host was not installed
  by the installer that writes one. Roll forward by installing, not by upgrading."
WAS_BUILD_ID="$("$PREFIX/venv/bin/python3" - "$KEEP/runtime-pin.json" <<'WASEOF'
import json, sys
said = json.load(open(sys.argv[1], encoding="utf-8"))
print(said.get("build_id") or "")
WASEOF
)"
printf '%s\n' "$WAS_BUILD_ID" > "$KEEP/build-id.txt"
echo "   $DIST, $(stat -c%s "$KEEP/installed-package.tar") bytes"
echo "   was running build $WAS_BUILD_ID"

step "3. the new code"
# --force-reinstall because a development wheel keeps its version number while its contents
# change, and "already satisfied" would leave the previous code running while every check said
# the upgrade had happened.
"$PREFIX/venv/bin/pip" install --quiet --no-compile --force-reinstall --no-deps "$WHEEL" || died "pip refused the wheel"
"$PREFIX/venv/bin/pip" install --quiet --no-compile "$WHEEL" || died "pip could not resolve its dependencies"
# BYTECODE THAT DOES NOT MOVE. Running this installer twice used to change 188 files and nothing
# else: every .pyc under the virtualenv, because pip compiles with the default mtime-and-size
# invalidation and a reinstall gives every source file a new mtime. The code was identical; the
# derived bytecode was not, so a second run of the whole path could not be shown to change
# nothing. --no-compile plus checked-hash invalidation makes a .pyc a function of its source's
# CONTENT alone, so the second run writes the same bytes -- and the files stay verifiable, which
# an interpreter silently recompiling at runtime as root would not be.
"$PREFIX/venv/bin/python" -m compileall -q -f --invalidation-mode checked-hash \
  "$PREFIX/venv/lib" > /dev/null 2>&1 \
  || died "the installed code could not be compiled, so this host would compile it at run time"
echo "   now: $("$PREFIX/venv/bin/agentnode" --version 2>/dev/null || echo unknown)"

# AND THE TWO THINGS PIP JUST INVALIDATED. `AGENTNODE_ARTEFACT` lives inside the dist-info
# that was replaced, so it is gone; the pin still names the wheel that is no longer here. A
# start now would be refused for the pin naming one artefact while the installation records
# none -- correctly, and fatally for an upgrade nobody had ever run.
ARTEFACT_DIGEST="$(sha256sum "$WHEEL" | cut -d' ' -f1)"
NEW_DIST="$(cd "$SITE" && ls -d agentnode_sdk-*.dist-info 2>/dev/null | head -1)"
[ -n "$NEW_DIST" ] || died "the reinstalled distribution has no dist-info"
printf '%s\n' "$ARTEFACT_DIGEST" > "$SITE/$NEW_DIST/AGENTNODE_ARTEFACT"

# The commit comes from the artefact this wheel was shipped in, the same place the installer
# reads it. A wheel handed over on its own cannot say which commit it is, and a pin that
# cannot name one is refused by the check that reads it -- so this refuses first, and says so.
BUILD_JSON="$(cd "$(dirname "$WHEEL")/.." 2>/dev/null && pwd)/BUILD.json"
[ -f "$BUILD_JSON" ] || died "this wheel did not come out of an artefact: no BUILD.json beside
  it at $BUILD_JSON, so the pin could not name the commit it was built from. Upgrade from an
  artefact built by build_artefacts.py."
"$PREFIX/venv/bin/python3" - "$BUILD_JSON" "$ARTEFACT_DIGEST" <<'PINEOF' || died "could not write the runtime pin"
import json, sys
from agentnode_sdk.gateway import runtime_pin

build = json.load(open(sys.argv[1], encoding="utf-8"))
if build["wheel_sha256"] != sys.argv[2]:
    raise SystemExit("the BUILD.json beside this wheel describes a different wheel")
runtime_pin.write_pin("/etc/agentnode", python_version="%d.%d.%d" % sys.version_info[:3],
                      artefact_sha256=sys.argv[2], commit=build["commit"])
PINEOF
chmod 0644 /etc/agentnode/runtime-pin.json
WILL_BE_BUILD_ID="$("$PREFIX/venv/bin/python3" - <<'IDEOF'
import json
print(json.load(open("/etc/agentnode/runtime-pin.json", encoding="utf-8"))["build_id"])
IDEOF
)"
echo "   artefact digest recorded, pin rewritten: $WILL_BE_BUILD_ID"

step "4. would it still start?"
# On the worker, the same checks the unit runs, before the service is restarted.
#
# ON THE CONTROL PLANE IT HAS TO BE THE MEASUREMENT, not the plain doctor. A gateway's
# measurement is bound to the build that took it -- artefact digest, commit and build id are
# all in it -- so the moment the code changes the stored one "says nothing about what is
# running here" and the plain doctor refuses. It refuses CORRECTLY, and it refuses every
# time, which made this gate unpassable after any real upgrade.
#
# `install-control-plane.sh --verify` learned exactly this during the previous repair and was
# changed to `--measure`; this script was not, and nobody found out because no upgrade had
# ever been run across two machines. Measured here on the pair: the plain doctor reported
# "the stored measurement describes something else (artefact_sha256, commit, build_id differ)"
# and the upgrade stopped.
# THE PREREQUISITE TABLE COMES WITH THE UPGRADE, and it has to arrive BEFORE the gate below.
#
# A gap found while preparing the acceptance run: the table was wired into install.sh only, so a host
# that was UPGRADED rather than installed afresh would never receive one -- and its preflight, which
# treats an absent table as "this host was not set up by the deploy path", would say so for ever
# without anybody noticing the check had stopped happening. The two hosts of this arc are exactly
# that case: both were installed before the table existed.
#
# ROLE-CORRECT, from $UNIT, which this script has already worked out from what is on the host. The
# worker gets the worker's table and the control plane gets the control plane's; neither is given the
# other's, which is the one thing about this file that must not become convenient.
if [ "$UNIT" = "agentnode-worker" ]; then THE_ROLE=worker; else THE_ROLE=control-plane; fi
# WHERE IT IS: beside this script, or beside the wheel it was given. This script takes a WHEEL and
# does not unpack anything itself, so the artefact it came from is already open somewhere and the
# table is in it. Both places are asked rather than one guessed.
THE_TABLE=""
for candidate in "$(cd "$(dirname "$0")" && pwd)/prerequisites.py"                  "$(cd "$(dirname "$WHEEL")" && pwd)/prerequisites.py"                  "$(cd "$(dirname "$WHEEL")/.." && pwd)/prerequisites.py"; do
  [ -f "$candidate" ] && { THE_TABLE="$candidate"; break; }
done
if [ -n "$THE_TABLE" ]; then
  install -d -o root -g root -m 0755 "$PREFIX/deploy"
  install -o root -g root -m 0644 "$THE_TABLE" "$PREFIX/deploy/prerequisites.py"
  echo "   the prerequisite table is at $PREFIX/deploy/prerequisites.py"
  # Reported, and it GATES: an upgrade onto a host that is missing something the new build reaches
  # for should stop here, with the one command that fixes it, rather than at the first job.
  python3 "$PREFIX/deploy/prerequisites.py" --role "$THE_ROLE" \
    || died "this host is missing something the $THE_ROLE role needs. The report above names each
  one and the single command that installs them from the repositories this host already has.
  The service was NOT restarted -- but the code on disk and the runtime pin are ALREADY the new
  ones, so a restart for any other reason would bring the new build up.
  Put it back with: rollback-one-host.sh $KEEP"
else
  # ABSENT FROM THE ARTEFACT is not the same as a host that is missing something. An artefact built
  # before the table existed carries none, and saying nothing about it would hide which checks ran.
  echo "   this artefact carries no prerequisite table, so nothing was checked about this host"
fi

if [ "$UNIT" = "agentnode-worker" ]; then
  set -a; . /etc/agentnode/worker.env; set +a
  runuser -u agentnode-worker -- "$PREFIX/venv/bin/agentnode" worker preflight \
      --topology separate-worker-host --listen "$AGENTNODE_LISTEN" \
      --keyring /etc/agentnode/pair-keys.json \
      --journal /var/lib/agentnode-worker/journal \
      --tls-dir /var/lib/agentnode-worker/tls \
      --trust /etc/agentnode/trust/ca.pem \
      --deployment "$AGENTNODE_DEPLOYMENT" \
      --accept-gateway "$AGENTNODE_GATEWAY_INSTANCE" \
      --revocation-list /etc/agentnode/trust/revoked.crl \
      --tombstones /etc/agentnode/trust/revoked-identities.json \
      --floor /var/lib/agentnode-floor/worker.floor \
    || died "the new build refuses this configuration. The service was NOT restarted and is
  still running the old code -- but the code on disk and the runtime pin are ALREADY the new
  ones, so a restart for any other reason would bring the new build up.
  Put it back with: rollback-one-host.sh $KEEP"
else
  runuser -u agentnode-gateway -- "$PREFIX/venv/bin/agentnode" gateway doctor --measure \
      --dir /var/lib/agentnode/state \
    || died "the new build could not measure this worker, so it does not know what it would
  enforce and will not serve on that basis. The service was NOT restarted and is still
  running the old build -- but the code on disk and the runtime pin are ALREADY the new ones,
  so a restart for any other reason would bring the new build up unmeasured.
  Put it back with: rollback-one-host.sh $KEEP"
fi

step "5. restart, and check it is THIS code that came up"
# Noted before the restart so the journal can be read from exactly there: a build id from an
# earlier start would answer the question about the wrong process.
RESTARTED_AT="$(date -u +%s)"
systemctl restart "$UNIT".service || died "the service did not restart"
sleep 3
systemctl is-active --quiet "$UNIT".service || {
  journalctl -u "$UNIT".service -n 40 --no-pager | sed 's/^/   /'
  died "it did not come back. Roll back with: rollback-one-host.sh $KEEP"; }

# RUNNING IS NOT RUNNING THIS CODE, and a timestamp cannot tell two builds apart -- only two
# orders of events. This used to compare the service's start time against the mtime of the
# installed package, which is satisfied by any restart after any write.
#
# The process says who it is. `Running as managed-<commit>+<artefact> on python <x>` is
# printed by the pin check on the way up, and it is derived from the pin and the installed
# distribution rather than from a version string, so it cannot agree by coincidence.
SAID_IT_IS="$(wait_for_the_build_id "$UNIT" "$RESTARTED_AT")"
[ -n "$SAID_IT_IS" ] || died "$UNIT came up but never said which build it is within
  ${BUILD_ID_PATIENCE}s, so this upgrade cannot be confirmed.
  Roll back with: rollback-one-host.sh $KEEP"
[ "$SAID_IT_IS" = "$WILL_BE_BUILD_ID" ] || died "$UNIT is serving $SAID_IT_IS and this upgrade
  installed $WILL_BE_BUILD_ID. Roll back with: rollback-one-host.sh $KEEP"
# WHETHER THE BUILD MOVED IS REPORTED, AND IS NOT A FAILURE. This used to `died` when the running
# build equalled the one that was running before, as "nothing changed". It cost an upgrade: the first
# attempt on the control plane got as far as writing the new wheel and the new pin and then stopped at
# the measurement gate because its worker was parked and unreachable -- which is the product being
# right. The second attempt, on the host that now already had the new code on disk, found before and
# after equal and refused an upgrade that had in fact succeeded.
#
# The property worth protecting is that the restart brought up the build THIS RUN INSTALLED, and the
# two checks above are exactly that: it must say a build id, and that id must be the one installed. A
# restart that silently came up on the old code fails the second of them. "It is the same as before"
# adds nothing to those and is true of every resumed upgrade and every reinstall of one build.
if [ "$SAID_IT_IS" = "$WAS_BUILD_ID" ]; then
  echo "   the build did not change: it was already $WAS_BUILD_ID before this run."
  echo "   That is not a failure -- it is what a resumed upgrade, or installing one build twice,"
  echo "   looks like. What matters is that it is serving what THIS run installed, and it is."
fi
echo "   $UNIT is up and says it is $SAID_IT_IS, which is what was installed"

printf '\n=== upgraded. What it was is kept at %s\n' "$KEEP"
printf '=== The OTHER host was not touched. If the wire version moved, it will say so on the\n'
printf '=== next connection, naming both ranges -- it will not quietly agree on an older one.\n'
