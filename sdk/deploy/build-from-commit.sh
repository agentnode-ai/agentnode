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

# WRITTEN WITHOUT IMPORTING THE PACKAGE. The first version called `_provenance.record`, which
# imports `agentnode_sdk`, which imports the whole SDK -- and the build interpreter has only
# `build` and `hatchling` installed, so it stopped at `No module named httpx`. Recording where
# a build came from must not depend on the build being installable first.
"$BUILDER" - "$COMMIT" "agentnode_sdk/_provenance.json" <<'PYEOF'
import json, pathlib, sys

where = pathlib.Path(sys.argv[2])
where.write_text(json.dumps({"commit": sys.argv[1]}, indent=1) + "\n", encoding="utf-8")
print("   recorded in the source tree:", where)
PYEOF
[ $? -eq 0 ] || { echo "the commit could not be recorded"; exit 1; }

rm -rf dist
"$BUILDER" -m build --wheel > /tmp/build.log 2>&1 || { tail -6 /tmp/build.log; exit 1; }
WHEEL="$(ls -1 dist/*.whl | head -1)"
[ -n "$WHEEL" ] || { echo "no wheel came out"; exit 1; }

# READ BACK OUT OF THE WHEEL, not out of the tree. A build that recorded the commit and then did
# not carry it into the artefact would otherwise look like a build that did.
"$BUILDER" - "$WHEEL" "$COMMIT" <<'PYEOF'
import json, sys, zipfile

with zipfile.ZipFile(sys.argv[1]) as z:
    names = [n for n in z.namelist() if n.endswith("agentnode_sdk/_provenance.json")]
    inside = json.loads(z.read(names[0]).decode("utf-8")).get("commit", "") if names else ""
if inside != sys.argv[2]:
    print("   the wheel says %r and the build was for %r" % (inside, sys.argv[2]))
    raise SystemExit(1)
print("   the wheel itself says it came from", inside)
PYEOF
[ $? -eq 0 ] || exit 1

printf '   wheel  : %s\n   digest : %s\n' "$(basename "$WHEEL")" \
  "$(sha256sum "$WHEEL" | cut -d' ' -f1)"
echo "$TREE/sdk/$WHEEL"
