#!/usr/bin/env bash
# The encrypted restore drill, executed on the closed alpha.
#
# `P6-BACKUP-AND-RESTORE-WERE-EXECUTED-NOT-DESIGNED` asks for an executed round trip, not a
# described one, and the founder direction adds what it has to prove: encrypt, DESTROY the
# original state, decrypt onto a fresh store, start the gateway, and successfully use a
# credential that was issued BEFORE the backup was taken.
#
# Every step prints what it did and what it read back. Nothing here is asserted by this script's
# own success: the credential check at the end is a real HTTPS call with a real token.
set -uo pipefail

STATE=/var/lib/agentnode/state
SECRET=/var/lib/agentnode/state.secret
BACKUPS=/root/agentnode-backups
KEY=/root/.agentnode-backup.key
# WHICH ENVIRONMENT. Overridable, and it has to be: the interpreter this service runs from is
# pinned and can move, and a drill that hardcodes one path asks the OLD installation whether the
# NEW one is complete. That is exactly what happened once -- the drill reported `runtime-pin.json`
# as an unaccounted file because it was asking a build that did not know about it yet.
VENV="${AGENTNODE_VENV:-/opt/agentnode/venv312}"
PY="${AGENTNODE_PYTHON:-$VENV/bin/python}"
AN="${AGENTNODE_AGENTNODE:-$VENV/bin/agentnode}"

step() { printf '\n=== %s\n' "$*"; }
died() { printf '\n!!! FAILED: %s\n' "$*"; exit 1; }

step "0. what this gateway is, before anything"
systemctl is-active agentnode-gateway agentnode-worker || died "the gateway is not running"
$PY -c "import json;d=json.load(open('$STATE/identity.json'));print('   gateway_id:', d['gateway_id'])"

step "1. a credential issued BEFORE the backup"
# The code travels inside the invitation blob rather than on its own line -- the invitation
# carries the address, the certificate to expect and when it expires, which is the point of it.
INVITE="$(runuser -u agentnode-gateway -- env HOME=/var/lib/agentnode $AN gateway pair --dir $STATE 2>/dev/null | grep -oE 'agentnode-invite-1\.[A-Za-z0-9_-]+' | head -1)"
CODE="$($PY -c "
import base64, json, sys
blob = sys.argv[1].split('.', 1)[1]
blob += '=' * (-len(blob) % 4)
print(json.loads(base64.urlsafe_b64decode(blob))['code'])
" "$INVITE" 2>/dev/null)"
[ -n "$CODE" ] || died "could not get a pairing code"
echo "   an invitation was issued"
TOKEN="$($PY - <<PYEOF
import json, ssl, urllib.request
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
body = json.dumps({"code": "$CODE", "client_name": "the drill's own device"}).encode()
req = urllib.request.Request("https://127.0.0.1:8099/v1/pair", data=body, method="POST")
req.add_header("Content-Type", "application/json")
with urllib.request.urlopen(req, context=ctx, timeout=30) as answer:
    print(json.loads(answer.read())["token"])
PYEOF
)"
[ -n "$TOKEN" ] || died "pairing did not produce a token"
echo "   paired; the token is held only by this drill and is not printed"

step "2. and it WORKS before the backup"
$PY - <<PYEOF || died "the credential did not work before the backup, so the drill proves nothing"
import json, ssl, sys, urllib.request
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
req = urllib.request.Request("https://127.0.0.1:8099/v1/op/devices/list")
req.add_header("X-AgentNode-Token", "$TOKEN"); req.add_header("X-AgentNode-Protocol", "2")
with urllib.request.urlopen(req, context=ctx, timeout=30) as answer:
    said = json.loads(answer.read())
print("   devices this credential can see:", len(said.get("devices", [])))
sys.exit(0 if said.get("devices") else 1)
PYEOF

step "3. a key, kept where the archives are not"
rm -f "$KEY"
bash /root/backup-and-restore.sh newkey --key "$KEY" || died "could not make a key"
ls -l "$KEY" | sed 's/^/   /'

step "2b. what this gateway REFUSES, recorded before the backup"
# `P6` verlangt, dass die wiederhergestellte Instanz dieselben Faelle ablehnt wie vorher. Ein
# Restore, nach dem etwas durchgeht, das vorher abgelehnt wurde, ist kein gelungener Restore --
# und ein Restore, nach dem etwas abgelehnt wird, das vorher ging, auch nicht. Beide Richtungen
# werden hier festgehalten und nach dem Hochfahren byteweise verglichen.
refusals_now() {
  $PY - "$1" <<PYEOF
import json, ssl, sys, urllib.request
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
out = {}

def ask(name, path, token, method="GET", body=None):
    req = urllib.request.Request("https://127.0.0.1:8099" + path, method=method,
                                 data=json.dumps(body).encode() if body else None)
    if body: req.add_header("Content-Type", "application/json")
    if token: req.add_header("X-AgentNode-Token", token)
    req.add_header("X-AgentNode-Protocol", "2")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as answer:
            out[name] = [answer.status, answer.read().decode("utf-8", "replace")]
    except urllib.error.HTTPError as refused:
        out[name] = [refused.code, refused.read().decode("utf-8", "replace")]
    except Exception as broke:
        out[name] = ["unreachable", str(broke)[:120]]

# Eine Kennung, die es nirgends gibt; eine Anfrage ohne Berechtigung; eine mit einer erfundenen.
ask("no_such_run", "/v1/op/status", "$TOKEN", "POST", {"run_id": "f" * 32})
ask("no_credential", "/v1/op/devices/list", "")
ask("bad_credential", "/v1/op/devices/list", "not-a-real-token")
ask("older_door_no_such_run", "/v1/jobs/" + "e" * 32, "$TOKEN")
open(sys.argv[1], "w", encoding="utf-8").write(json.dumps(out, indent=1, sort_keys=True))
print("   ", len(out), "Ablehnungen festgehalten")
PYEOF
}
refusals_now /root/refusals-before.json || died "konnte die Ablehnungen vorher nicht festhalten"
cat /root/refusals-before.json | head -6 | sed 's/^/     /'

step "3b. a leftover this gateway holds that the drill does not know about"
# The drill reported `use-log.jsonl.before-the-chain-20260911-154110` as unaccounted, and it is
# right: a stale copy of the metering log left by a migration in September. It is NOT given a
# class to silence the warning -- inventing one would make the completeness check meaningless.
# An operator moves it out of the state directory, which is what happens here, in the open.
for stale in "$STATE"/use-log.jsonl.before-the-chain-*; do
  [ -e "$stale" ] || continue
  mv "$stale" /root/
  echo "   moved $(basename "$stale") out of the state directory. It is a migration leftover,"
  echo "   not a class this product keeps, and it is kept rather than deleted."
done
$PY -m agentnode_sdk.gateway.backup unaccounted "$STATE"   && echo "   nothing else is stored here that the drill cannot check"   || died "something else is unaccounted for; look at the PROBLEM line above"

step "4. the backup, sealed"
rm -rf "$BACKUPS"
bash /root/backup-and-restore.sh backup --dir "$STATE" --to "$BACKUPS" --key "$KEY" || died "backup"
WHERE="$(ls -d $BACKUPS/*/ | head -1)"
echo "   what was written:"
ls -l "$WHERE" | sed 's/^/     /'
echo "   the sealed header, which is readable without the key:"
$PY -m agentnode_sdk.gateway.archive header --in "$WHERE/state.tar.sealed" | sed 's/^/     /'
echo "   and the archive does NOT contain the key:"
if grep -qa "$(cat $KEY | tr -d '\n')" "$WHERE/state.tar.sealed"; then
  died "the key is inside the archive"
else
  echo "     confirmed: the key does not appear in the sealed bytes"
fi

step "5. it will not open with the WRONG key"
head -c 32 /dev/urandom | xxd -p -c 64 > /root/.wrong.key
if $PY -m agentnode_sdk.gateway.archive open --in "$WHERE/state.tar.sealed" \
     --out /tmp/should-not-exist --key /root/.wrong.key; then
  died "it opened with a key it was not sealed with"
fi
[ -f /tmp/should-not-exist ] && died "something was written despite the refusal"
echo "   confirmed: refused, and nothing was written"

step "6. nor when a byte of it is changed"
cp "$WHERE/state.tar.sealed" /tmp/altered.sealed
SIZE=$(stat -c %s /tmp/altered.sealed); AT=$((SIZE/2))
printf '\\x00' | dd of=/tmp/altered.sealed bs=1 seek=$AT count=1 conv=notrunc status=none
if $PY -m agentnode_sdk.gateway.archive open --in /tmp/altered.sealed \
     --out /tmp/should-not-exist --key "$KEY"; then
  died "an altered archive opened"
fi
echo "   confirmed: refused"

step "7. nor when it is cut short"
head -c $((SIZE - 64)) "$WHERE/state.tar.sealed" > /tmp/short.sealed
if $PY -m agentnode_sdk.gateway.archive open --in /tmp/short.sealed \
     --out /tmp/should-not-exist --key "$KEY"; then
  died "a truncated archive opened"
fi
echo "   confirmed: refused"

step "8. DESTROY the original state"
systemctl stop agentnode-gateway
rm -rf "$STATE" "$SECRET"
[ -d "$STATE" ] && died "the state is still there"
echo "   $STATE and $SECRET are gone. Nothing below reads them."

step "9. restore onto a fresh store, from the sealed archive"
bash /root/backup-and-restore.sh restore --from "$WHERE" --dir "$STATE" --key "$KEY" \
  || died "the restore did not check out"

step "10. start the gateway on it"
systemctl start agentnode-gateway
sleep 4
systemctl is-active agentnode-gateway || died "the gateway did not start on the restored state"

step "11. the credential issued BEFORE the backup still works"
$PY - <<PYEOF || died "the pre-backup credential does NOT work after the restore"
import json, ssl, sys, urllib.request
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
req = urllib.request.Request("https://127.0.0.1:8099/v1/op/devices/list")
req.add_header("X-AgentNode-Token", "$TOKEN"); req.add_header("X-AgentNode-Protocol", "2")
with urllib.request.urlopen(req, context=ctx, timeout=30) as answer:
    said = json.loads(answer.read())
print("   the credential works, and sees", len(said.get("devices", [])), "device(s)")
sys.exit(0 if said.get("devices") else 1)
PYEOF

step "11b. and it REFUSES exactly what it refused before"
refusals_now /root/refusals-after.json || died "konnte die Ablehnungen nachher nicht festhalten"
if diff -u /root/refusals-before.json /root/refusals-after.json > /root/refusals.diff; then
  echo "   identisch: jeder Fall wird nach der Wiederherstellung genauso abgelehnt wie vorher"
else
  echo "   UNTERSCHIED:"
  sed 's/^/     /' /root/refusals.diff
  died "die wiederhergestellte Instanz antwortet anders als die, von der das Backup stammt"
fi

step "12. and the metering chain still verifies"
runuser -u agentnode-gateway -- env HOME=/var/lib/agentnode $PY - <<PYEOF
from agentnode_sdk.gateway import meter
held = meter.verify("$STATE")
print("   metering:", held)
PYEOF

rm -f /root/.wrong.key /tmp/altered.sealed /tmp/short.sealed
printf '\n=== the drill finished. Every step above ran on this machine.\n'
