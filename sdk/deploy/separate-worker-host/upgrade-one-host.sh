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
#   what was running       -- so "it came back" can be checked against what was there.
#
# It does not touch the state, the journal, the keyring or the certificates. An upgrade that
# rewrote those would be a migration, and a migration is not this.

set -uo pipefail

WHEEL="${1:-}"
PREFIX=/opt/agentnode
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
KEEP="/root/agentnode-upgrade-${STAMP}"

step() { printf '\n=== %s\n' "$*"; }
died() { printf '\n!!! FAILED: %s\n' "$*"; exit 1; }

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
echo "   $DIST, $(stat -c%s "$KEEP/installed-package.tar") bytes"

step "3. the new code"
# --force-reinstall because a development wheel keeps its version number while its contents
# change, and "already satisfied" would leave the previous code running while every check said
# the upgrade had happened.
"$PREFIX/venv/bin/pip" install --quiet --force-reinstall --no-deps "$WHEEL" || died "pip refused the wheel"
"$PREFIX/venv/bin/pip" install --quiet "$WHEEL" || died "pip could not resolve its dependencies"
echo "   now: $("$PREFIX/venv/bin/agentnode" --version 2>/dev/null || echo unknown)"

step "4. would it still start?"
# On the worker, the same checks the unit runs, before the service is restarted. There is no
# equivalent on the control plane that can be run without its state, so `gateway doctor` stands
# in: both answer "would this configuration serve", and neither opens anything.
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
    || died "the new build refuses this configuration. NOTHING WAS RESTARTED -- the old code is
  still serving. Roll back with: rollback-one-host.sh $KEEP"
else
  runuser -u agentnode-gateway -- "$PREFIX/venv/bin/agentnode" gateway doctor \
      --dir /var/lib/agentnode/state \
    || died "the new build does not accept this configuration. Nothing was restarted."
fi

step "5. restart, and check it is THIS code that came up"
systemctl restart "$UNIT".service || died "the service did not restart"
sleep 3
systemctl is-active --quiet "$UNIT".service || {
  journalctl -u "$UNIT".service -n 40 --no-pager | sed 's/^/   /'
  died "it did not come back. Roll back with: rollback-one-host.sh $KEEP"; }

# Running is not the same as running THIS code.
BUILT_AT=$(stat -c %Y "$("$PREFIX/venv/bin/python3" -c 'import agentnode_sdk; print(agentnode_sdk.__file__)')")
STARTED=$(date -d "$(systemctl show "$UNIT" -p ActiveEnterTimestamp --value)" +%s 2>/dev/null || echo 0)
[ "$STARTED" -ge "$BUILT_AT" ] || died "$UNIT has been running since before this code was
  installed, so it is serving the old build."
echo "   $UNIT is up, started after the code it runs was written"

printf '\n=== upgraded. What it was is kept at %s\n' "$KEEP"
printf '=== The OTHER host was not touched. If the wire version moved, it will say so on the\n'
printf '=== next connection, naming both ranges -- it will not quietly agree on an older one.\n'
