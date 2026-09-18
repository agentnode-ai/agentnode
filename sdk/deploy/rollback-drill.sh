#!/usr/bin/env bash
# Roll back to an artefact, prove it took, and roll forward again.
#
# ## A rollback is a deployment
#
# The first version of this put the old package back with a bare `pip install` and the gateway
# refused to start: the pin expects an artefact digest, and a bare install records none. The
# refusal was right -- a service that cannot confirm where its code came from should not run it
# -- and the lesson is that a rollback goes through the same door as any other install. The pin
# comes out of the artefact; the forward step goes through `deploy-pinned.sh`.
#
# ## What is drilled here
#
# The CODE and the pin. The state is drilled separately and destructively by
# `encrypted-restore-drill.sh`, which destroys the store and rebuilds it from a sealed archive.
# Doing that here as well would throw away the devices paired since and would be drilling one
# thing twice while calling it two.
#
# Both directions are proved by the digests of files on disk, never by a version number. The
# version was 0.24.1 on both sides of a deployment that changed the code, which is exactly why.
set -uo pipefail

ART="${1:?usage: rollback-drill.sh /root/rollback-<stamp> [wheel] [commit]}"
FORWARD_WHEEL="${2:-}"
FORWARD_COMMIT="${3:-}"
VENV="${AGENTNODE_VENV:-/opt/agentnode/venv312}"
SITE="$(ls -d "$VENV"/lib/python3*/site-packages | head -1)"
PIN_DIR="${AGENTNODE_PIN_DIR:-/etc/agentnode}"
HERE="$(cd "$(dirname "$0")" && pwd)"
WATCH="gateway/retention.py gateway/backup.py access/enrolment.py gateway/securedir.py"

step() { printf '\n=== %s\n' "$*"; }
died() { printf '\n!!! FAILED: %s\n' "$*"; exit 1; }

prints() { for f in $WATCH; do printf '   %-28s %s\n' "$f" \
             "$(sha256sum "$SITE/agentnode_sdk/$f" 2>/dev/null | cut -c1-16)"; done; }

# The comparison is over EVERY file of the installed package, not over the four printed above.
# Four hand-picked files can agree while the rest of the tree differs, and then a drill that
# proves nothing reports that it proved something.
whole() { find "$SITE/agentnode_sdk" -type f -name '*.py' -print0 | sort -z \
            | xargs -0 sha256sum | sha256sum | cut -c1-16; }

serving() {
  "$VENV/bin/python" - <<'PYEOF'
import json, ssl, sys, urllib.request
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
try:
    with urllib.request.urlopen("https://127.0.0.1:8099/v1/health", context=ctx, timeout=25) as a:
        said = json.loads(a.read())
    print("   serving=%s taking_work=%s version=%s"
          % (said.get("serving"), said.get("taking_work"), said["gateway"]["version"]))
    sys.exit(0 if said.get("serving") else 1)
except Exception as exc:                                              # noqa: BLE001
    print("   the gateway did not answer: %s" % exc)
    sys.exit(1)
PYEOF
}

[ -d "$ART" ] || died "no artefact at $ART"
for f in installed-package.tar agentnode-gateway.service agentnode-worker.service \
         runtime-pin.json; do
  [ -f "$ART/$f" ] || died "the artefact has no $f"
  grep -q "$(sha256sum "$ART/$f" | cut -d' ' -f1)" "$ART/SHA256SUMS" \
    || died "$f does not match the digest recorded when the artefact was made"
done
echo "the artefact's own files match the digests recorded when it was made"

step "1. what is installed now, before rolling back"
prints
NOW="$(whole)"
echo "   the whole installed tree: $NOW"
NOW_PIN="$(cat "$PIN_DIR/runtime-pin.json")"
if [ -z "$FORWARD_WHEEL" ]; then
  FORWARD_WHEEL="$(ls -1t /root/build-*/sdk/dist/*.whl 2>/dev/null | head -1)"
fi
[ -n "$FORWARD_WHEEL" ] && [ -f "$FORWARD_WHEEL" ] \
  || died "no wheel to roll forward to; pass one as the second argument"
[ -n "$FORWARD_COMMIT" ] || FORWARD_COMMIT="$(python3 -c "
import json,sys;print(json.loads(sys.argv[1])['commit'])" "$NOW_PIN")"
echo "   forward is $(basename "$FORWARD_WHEEL") at ${FORWARD_COMMIT:0:12}"

step "2. roll BACK: the package AND the pin from the artefact"
systemctl stop agentnode-gateway agentnode-worker || died "could not stop the services"
rm -rf "$SITE/agentnode_sdk" "$SITE"/agentnode_sdk-*.dist-info
tar -C "$SITE" -xf "$ART/installed-package.tar" || died "could not unpack the artefact"
cp "$ART/runtime-pin.json" "$PIN_DIR/runtime-pin.json"
chmod 644 "$PIN_DIR/runtime-pin.json"
cp "$ART/agentnode-gateway.service" "$ART/agentnode-worker.service" /etc/systemd/system/
systemctl daemon-reload
systemctl start agentnode-worker && sleep 4 && systemctl start agentnode-gateway && sleep 6
prints
BACK="$(whole)"
echo "   the whole installed tree: $BACK"
[ "$BACK" != "$NOW" ] || died "the rollback changed nothing -- it did not land"
echo "   the running code is DIFFERENT from a moment ago, so the rollback landed"

step "3. and the rolled-back gateway serves"
serving || died "the gateway does not serve after the rollback"
systemctl is-active agentnode-gateway agentnode-worker | tr '\n' ' '; echo

step "4. roll FORWARD, through the deployment door rather than around it"
bash "$HERE/deploy-pinned.sh" "$FORWARD_WHEEL" "$FORWARD_COMMIT" "$VENV" > /tmp/forward.log 2>&1 \
  || { tail -8 /tmp/forward.log; died "rolling forward through the deployment failed"; }
prints
FWD="$(whole)"
echo "   the whole installed tree: $FWD"
[ "$FWD" = "$NOW" ] \
  || died "rolling forward did not restore what was installed before the drill"
echo "   byte-identical to what was installed before this drill began"

step "5. and the rolled-forward gateway serves"
serving || died "the gateway does not serve after rolling forward"

step "6. nothing of the state is owned by anyone but the gateway"
find /var/lib/agentnode/state -maxdepth 1 ! -user agentnode-gateway -printf '   %u %p\n' | head
echo "   (no lines above means every file belongs to the gateway)"

printf '\n=== the rollback drill finished: back, served, forward, served.\n'
