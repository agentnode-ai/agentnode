#!/usr/bin/env bash
# Install a managed-service build, and refuse before touching anything if it is not the one.
#
# ## Why the refusals come first
#
# The R2 deployment installed a wheel whose version -- 0.24.1 -- was the same before and after,
# onto a machine whose interpreter nobody had tested. Both were noticed afterwards, by looking.
# This script is what makes both a refusal rather than an observation, and every check happens
# BEFORE the running service is stopped, so a deployment that is going to fail leaves the
# previous one serving.
#
# ## The three, separately
#
#   the interpreter  -- must be the tested family, and must be the one the venv actually is
#   the artefact     -- by digest. A version number cannot identify a build
#   the commit       -- which source the artefact came from
#
# Each names itself when it fails. "Something does not match" is not an answer anybody can act on.
#
# ## What this does not do
#
# It does not make the pinned interpreter correct, and it cannot stop an operator with root from
# editing the pin afterwards. What it stops is a service arriving on an untested interpreter, or
# an unidentifiable artefact, without anybody choosing it.
set -uo pipefail

WHEEL="${1:?usage: deploy-pinned.sh <wheel> <commit> [venv]}"
COMMIT="${2:?usage: deploy-pinned.sh <wheel> <commit> [venv]}"
VENV="${3:-/opt/agentnode/venv}"
STATE="${AGENTNODE_STATE:-/var/lib/agentnode/state}"
WORKER_PIN_DIR="${AGENTNODE_WORKER_PIN_DIR:-/etc/agentnode}"
WANT_PY="3.12"

step() { printf '\n=== %s\n' "$*"; }
died() { printf '\n!!! REFUSED (%s): %s\n' "$1" "$2"; exit 1; }

step "0. what is being asked for"
[ -f "$WHEEL" ] || died "artefact" "there is no wheel at $WHEEL"
DIGEST="$(sha256sum "$WHEEL" | cut -d' ' -f1)"
printf '   wheel  : %s\n   digest : %s\n   commit : %s\n   venv   : %s\n' \
  "$(basename "$WHEEL")" "$DIGEST" "$COMMIT" "$VENV"

step "1. the interpreter, BEFORE anything is touched"
[ -x "$VENV/bin/python" ] || died "interpreter" "$VENV has no python; make it with python$WANT_PY -m venv"
RUNNING="$("$VENV/bin/python" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
FAMILY="${RUNNING%.*}"
[ "$FAMILY" = "$WANT_PY" ] || died "interpreter" \
  "$VENV is python $RUNNING and this service is tested on $WANT_PY. Build the environment with python$WANT_PY -m venv, or change what is tested."
echo "   $VENV is python $RUNNING"

REAL="$(readlink -f "$VENV/bin/python")"
case "$REAL" in
  *"$WANT_PY"*) echo "   and it resolves to $REAL" ;;
  *) died "interpreter" "$VENV/bin/python resolves to $REAL, which is not a $WANT_PY interpreter. A venv whose base interpreter moved is a venv that is no longer what it says." ;;
esac

step "2. the commit, READ OUT OF THE ARTEFACT rather than taken from the caller"
[ "${#COMMIT}" -ge 7 ] || died "commit" "'$COMMIT' is too short to name a commit"

# UNCONDITIONAL, AND BEFORE ANYTHING IS INSTALLED. This used to compare the commit only when an
# optional environment variable said what to expect, so a deployment handed any commit at all
# proceeded -- the artefact and the commit were two independent claims by the same caller.
# `ALPHA-RUNTIME-PIN-0002` named it. The wheel now carries the commit it was built from, inside
# the package and therefore inside the digest, and this reads it back out of the ZIP.
INSIDE="$("$VENV/bin/python" - "$WHEEL" <<'PYEOF'
import json, sys, zipfile
try:
    with zipfile.ZipFile(sys.argv[1]) as z:
        names = [n for n in z.namelist() if n.endswith("agentnode_sdk/_provenance.json")]
        print(json.loads(z.read(names[0]).decode("utf-8")).get("commit", "") if names else "")
except Exception:
    print("")
PYEOF
)"
if [ -z "$INSIDE" ]; then
  died "commit" "this wheel does not say which source it was built from, so nothing here can check that it is the one named. Build it with deploy/build-from-commit.sh, which records the commit inside the artefact."
fi
if [ "$INSIDE" != "$COMMIT" ]; then
  died "commit" "the wheel says it was built from $INSIDE and this deployment was told $COMMIT"
fi
echo "   the wheel itself says it came from $INSIDE"

# The expectations stay, and stay optional: they are a SECOND, out-of-band statement of what was
# meant, useful when an operator wants the deployment to refuse a wheel that is internally
# consistent but not the one they intended. They are no longer the only thing that checks.
if [ -n "${AGENTNODE_EXPECT_COMMIT:-}" ] && [ "$AGENTNODE_EXPECT_COMMIT" != "$COMMIT" ]; then
  died "commit" "this deployment was told to expect $AGENTNODE_EXPECT_COMMIT and was handed $COMMIT"
fi
if [ -n "${AGENTNODE_EXPECT_DIGEST:-}" ] && [ "$AGENTNODE_EXPECT_DIGEST" != "$DIGEST" ]; then
  died "artefact" "this deployment was told to expect $AGENTNODE_EXPECT_DIGEST and the wheel is $DIGEST. The version number is the same either way, which is exactly why the digest is what is compared."
fi

step "3. the previous installation stays up until the checks are done"
systemctl is-active agentnode-gateway agentnode-worker 2>/dev/null | tr '\n' ' '; echo

step "4. install"
systemctl stop agentnode-gateway agentnode-worker 2>/dev/null
"$VENV/bin/pip" install -q --force-reinstall --no-deps "$WHEEL" || died "artefact" "pip refused the wheel"

# The digest is recorded INSIDE the installed distribution, so a later start can read what it was
# installed from. Recomputing it from unpacked files would be inventing a number; this is the one
# that was actually installed.
DIST="$(cd "$(ls -d "$VENV"/lib/python3*/site-packages | head -1)" && ls -d agentnode_sdk-*.dist-info | head -1)"
[ -n "$DIST" ] || died "artefact" "the installed distribution has no dist-info"
printf '%s\n' "$DIGEST" > "$(ls -d "$VENV"/lib/python3*/site-packages | head -1)/$DIST/AGENTNODE_ARTEFACT"
echo "   installed, and the artefact digest recorded in $DIST"

step "5. write the pin -- ONE of them, and outside the state"
# ONE pin, in a directory both service accounts can read and no restore rewrites. There used to
# be two: one in the state directory for the gateway, one beside the worker's key. A restore
# drill destroys the state and rebuilds it, so the gateway's came back describing the PREVIOUS
# build while the worker's stayed right -- worker at 3cf3cb9, gateway at b672384, installed
# artefact f039d767. The start refused, correctly, which is how it was found. Two pins are two
# chances to disagree, and the state directory is the one place a restore can overwrite.
mkdir -p "$WORKER_PIN_DIR"
"$VENV/bin/python" - "$WORKER_PIN_DIR" "$RUNNING" "$DIGEST" "$COMMIT" <<'PINEOF'
import sys
from agentnode_sdk.gateway import runtime_pin
where = runtime_pin.write_pin(sys.argv[1], python_version=sys.argv[2],
                              artefact_sha256=sys.argv[3], commit=sys.argv[4])
print("   pin        :", where)
print("   build id   :", runtime_pin.build_id(sys.argv[4], sys.argv[3]))
PINEOF
[ $? -eq 0 ] || died "pin" "the pin could not be written"
# Readable by both service accounts, written by an operator. A service reads its pin; it does
# not get to decide what it says.
chmod 644 "$WORKER_PIN_DIR/runtime-pin.json"
rm -f "$STATE/runtime-pin.json"

step "6. start, which checks the pin for itself"
systemctl start agentnode-worker && sleep 3
systemctl start agentnode-gateway && sleep 6
systemctl is-active agentnode-worker agentnode-gateway | tr '\n' ' '; echo

step "7. measure again, because the old measurement is about the old build"
# NOT OPTIONAL, and found by leaving it out. The report binding names the interpreter, the
# artefact and the commit, so a measurement taken before this deployment describes something
# that is no longer running -- and the gateway correctly refuses to pair anybody to a service
# whose enforcement it cannot vouch for. Without this step every pinned deployment ends with a
# service that is up, healthy, and refusing work until somebody reads the error and runs it by
# hand. A deployment that leaves that behind is not finished.
runuser -u agentnode-gateway -- env HOME=/var/lib/agentnode   "$VENV/bin/agentnode" gateway doctor --measure --dir "$STATE" > /tmp/measure.log 2>&1   || { tail -6 /tmp/measure.log; died "measure" "the gateway could not measure itself after the deployment"; }
grep -E "measured|PASS|conformant" /tmp/measure.log | tail -3 | sed 's/^/   /'

step "8. and it says what it is"
"$VENV/bin/python" - <<'PYEOF'
import json, ssl, sys, urllib.request
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
try:
    with urllib.request.urlopen("https://127.0.0.1:8099/v1/health", context=ctx, timeout=25) as a:
        said = json.loads(a.read())
    print("   serving=%s taking_work=%s" % (said.get("serving"), said.get("taking_work")))
except Exception as exc:                                              # noqa: BLE001
    print("   the gateway did not answer: %s" % exc); sys.exit(1)
PYEOF
[ $? -eq 0 ] || died "start" "the gateway did not come back up"

printf '\n=== deployed. The pin is what a start checks itself against from here on.\n'
