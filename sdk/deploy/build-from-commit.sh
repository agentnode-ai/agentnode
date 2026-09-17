#!/usr/bin/env bash
# Build a wheel that says which source it came from.
#
# The commit goes INTO the package before the wheel is made, so it is inside the artefact and
# covered by the artefact's digest. A deployment then reads it out of the wheel rather than being
# told it. `ALPHA-RUNTIME-PIN-0002`: "it accepts the supplied COMMIT as the artefact commit unless
# an optional environment value disagrees" -- which was true, and is what this closes.
set -uo pipefail

TREE="${1:?usage: build-from-commit.sh <tree-with-sdk> <commit>}"
COMMIT="${2:?usage: build-from-commit.sh <tree-with-sdk> <commit>}"
BUILDER="${AGENTNODE_BUILD_PYTHON:-/root/buildvenv/bin/python}"

[ "${#COMMIT}" -ge 7 ] || { echo "'$COMMIT' is too short to name a commit"; exit 1; }
cd "$TREE/sdk" || exit 1

"$BUILDER" - "$COMMIT" <<'PYEOF'
import sys
sys.path.insert(0, ".")
from agentnode_sdk import _provenance
print("   recorded in the source tree:", _provenance.record(sys.argv[1]))
PYEOF
[ $? -eq 0 ] || { echo "the commit could not be recorded"; exit 1; }

rm -rf dist
"$BUILDER" -m build --wheel > /tmp/build.log 2>&1 || { tail -6 /tmp/build.log; exit 1; }
WHEEL="$(ls -1 dist/*.whl | head -1)"
[ -n "$WHEEL" ] || { echo "no wheel came out"; exit 1; }

# READ BACK OUT OF THE WHEEL, not out of the tree. A build that recorded the commit and then did
# not carry it into the artefact would otherwise look like a build that did.
"$BUILDER" - "$WHEEL" "$COMMIT" <<'PYEOF'
import sys
sys.path.insert(0, ".")
from agentnode_sdk import _provenance
inside = _provenance.of_a_wheel(sys.argv[1])
if inside != sys.argv[2]:
    print("   the wheel says %r and the build was for %r" % (inside, sys.argv[2]))
    raise SystemExit(1)
print("   the wheel itself says it came from", inside)
PYEOF
[ $? -eq 0 ] || exit 1

printf '   wheel  : %s\n   digest : %s\n' "$(basename "$WHEEL")" \
  "$(sha256sum "$WHEEL" | cut -d' ' -f1)"
echo "$TREE/sdk/$WHEEL"
