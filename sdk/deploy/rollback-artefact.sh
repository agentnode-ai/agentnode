#!/usr/bin/env bash
# Everything needed to put this machine back exactly as it is right now.
#
# Made BEFORE anything is touched, because an artefact made afterwards is a record of the new
# state wearing the old state's name. Five things go in, and each answers a different way a
# rollback goes wrong:
#
#   the sealed state      -- the data. Encrypted, with the key kept elsewhere.
#   the installed package -- the CODE that is running now, as bytes rather than as a version
#                            number. "0.24.1" is not an identity: two builds carried it.
#   the pin               -- what this installation is allowed to run as. Without it a rollback
#                            puts old code back under a new pin, and the service refuses to
#                            start -- correctly, and for a reason nobody chose. Found by
#                            leaving it out.
#   the unit files        -- how it is started, and as which user.
#   what was running      -- so "it came back" can be checked against what was there.
set -uo pipefail

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="/root/rollback-${STAMP}"
# THE ENVIRONMENT IS NOT HARDCODED. It used to be, and after the service moved to a pinned
# interpreter the artefact captured the PREVIOUS installation -- so a rollback drill restored a
# package from the environment nobody was running any more.
VENV="${AGENTNODE_VENV:-/opt/agentnode/venv312}"
SITE="$(ls -d "$VENV"/lib/python3*/site-packages 2>/dev/null | head -1)"
PIN_DIR="${AGENTNODE_PIN_DIR:-/etc/agentnode}"
KEY="${AGENTNODE_BACKUP_KEY:-/root/.agentnode-backup.key}"

step() { printf '\n=== %s\n' "$*"; }
died() { printf '\n!!! FAILED: %s\n' "$*"; exit 1; }

mkdir -p "$OUT" || died "could not make $OUT"
chmod 700 "$OUT"

step "1. what is running right now, and as whom"
{
  systemctl is-active agentnode-gateway agentnode-worker
  echo "---"
  systemctl show agentnode-gateway -p User -p ExecStart
  systemctl show agentnode-worker  -p User -p ExecStart
  echo "--- listening"
  ss -tlnp | grep -E '8099|agentnode' || true
} > "$OUT/what-was-running.txt" 2>&1
sed 's/^/   /' "$OUT/what-was-running.txt" | head -6

step "2. the unit files and the pin, byte for byte"
cp /etc/systemd/system/agentnode-gateway.service "$OUT/" || died "no gateway unit"
cp /etc/systemd/system/agentnode-worker.service  "$OUT/" || died "no worker unit"
[ -f "$PIN_DIR/runtime-pin.json" ] || died "no pin at $PIN_DIR -- an artefact without one cannot be rolled back to"
cp "$PIN_DIR/runtime-pin.json" "$OUT/" || died "the pin could not be copied"
echo "   2 unit files and the pin: $(python3 -c "
import json;print(json.load(open('$OUT/runtime-pin.json'))['build_id'])")"

step "3. the package that is installed now, as bytes"
[ -n "$SITE" ] || died "no site-packages under $VENV"
DIST="$(cd "$SITE" && ls -d agentnode_sdk-*.dist-info 2>/dev/null | head -1)"
[ -n "$DIST" ] || died "no agentnode_sdk dist-info under $SITE"
tar -C "$SITE" -cf "$OUT/installed-package.tar" agentnode_sdk "$DIST" \
  || died "could not archive the installed package"
"$VENV/bin/python" -m pip freeze > "$OUT/pip-freeze.txt" 2>/dev/null || true
echo "   $DIST from $VENV, $(stat -c%s "$OUT/installed-package.tar") bytes"

step "4. the state, sealed"
[ -f "$KEY" ] || died "no backup key at $KEY -- refusing to make an artefact with no data in it"
bash "$(dirname "$0")/backup-and-restore.sh" backup > "$OUT/backup.log" 2>&1 || {
  tail -20 "$OUT/backup.log"; died "the backup step failed"; }
NEWEST="$(ls -1dt /root/agentnode-backups/*/ 2>/dev/null | head -1)"
[ -n "$NEWEST" ] || died "the backup produced no directory"
echo "$NEWEST" > "$OUT/which-backup.txt"
echo "   sealed backup: $NEWEST"

step "5. one digest over the whole artefact"
( cd "$OUT" && sha256sum what-was-running.txt agentnode-gateway.service \
    agentnode-worker.service runtime-pin.json installed-package.tar pip-freeze.txt \
    which-backup.txt > SHA256SUMS )
sed 's/^/   /' "$OUT/SHA256SUMS"

printf '\n=== the rollback artefact is at %s\n' "$OUT"
printf '=== the key that opens its data is NOT in it, and is not backed up with it.\n'
echo "$OUT"
