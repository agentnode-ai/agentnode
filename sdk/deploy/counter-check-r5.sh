#!/usr/bin/env bash
# Would the R5 evidence fail without the mechanism?
#
# Each check removes ONE mechanism, names a DIFFERENT test from the one that defines it, requires
# a non-zero exit, proves the mutation actually landed, and proves the file came back byte for
# byte. A counter-check that cannot show the mutation landed is a counter-check that may have
# proved nothing.
set -uo pipefail
cd "$(dirname "$0")/.."
PY="${COUNTER_PYTHON:-/root/v312verify/bin/python}"
FAILED=0

one() {
  local name="$1" file="$2" test="$3" before after
  shift 3
  before="$(sha256sum "$file" | cut -d' ' -f1)"
  printf '\n=== %s\n    file %s\n    test %s\n' "$name" "$file" "$test"
  "$@" || { echo "    !! the mutation command itself failed"; FAILED=1; return; }
  after="$(sha256sum "$file" | cut -d' ' -f1)"
  if [ "$before" = "$after" ]; then
    echo "    !! THE MUTATION DID NOT LAND -- this counter-check proves nothing"; FAILED=1
    return
  fi
  echo "    mutated : ${before:0:16} -> ${after:0:16}"
  "$PY" -m pytest "$test" -q -p no:randomly > /tmp/cc.log 2>&1
  local code=$?
  if [ "$code" -eq 0 ]; then
    echo "    !! THE TEST STILL PASSED WITHOUT THE MECHANISM (exit 0)"; FAILED=1
  else
    echo "    the test FAILED with exit $code, as it must:"
    grep -E "^(FAILED|E  )" /tmp/cc.log | head -2 | sed 's/^/      /'
  fi
  git checkout -- "$file" 2>/dev/null || cp "/tmp/$(basename "$file").keep" "$file"
  local back; back="$(sha256sum "$file" | cut -d' ' -f1)"
  if [ "$back" = "$before" ]; then
    echo "    restored: byte for byte (${back:0:16})"
  else
    echo "    !! THE FILE DID NOT COME BACK ($back != $before)"; FAILED=1
  fi
}

for f in agentnode_sdk/gateway/identity.py pyproject.toml agentnode_sdk/_agent_pip.py \
         agentnode_sdk/cli/gateway_commands.py agentnode_sdk/gateway/runtime_pin.py \
         agentnode_sdk/signing_key.py agentnode_sdk/gateway/meter.py \
         tests/test_what_is_kept.py; do
  cp "$f" "/tmp/$(basename "$f").keep"
done

one "R5-a the build id is actually in what the gateway says it is" \
    agentnode_sdk/gateway/identity.py \
    tests/test_runtime_pin.py::TestWhichBuildIsAnswering::test_two_builds_of_one_version_are_told_apart \
    sed -i 's/"build_id": self.build_id}/"build_id": ""}/' agentnode_sdk/gateway/identity.py

one "R5-b the fingerprint does not move with the build" \
    agentnode_sdk/gateway/identity.py \
    tests/test_runtime_pin.py::TestWhichBuildIsAnswering::test_a_new_build_does_not_unpair_every_device \
    sed -i 's|{self.version}".encode()|{self.version}{self.build_id}".encode()|' \
      agentnode_sdk/gateway/identity.py

one "R5-c there is only one copy of the version" \
    pyproject.toml \
    tests/test_runtime_pin.py::TestThePublishedVersionIsNotSilentlyReplaced::test_and_there_is_only_one_copy_of_the_version \
    sed -i 's/^dynamic = \["version"\]$/version = "0.24.1"/' pyproject.toml

one "R5-d one installation seen down two paths is one" \
    agentnode_sdk/_agent_pip.py \
    tests/test_runtime_pin.py::TestOneInstallationSeenDownTwoPathsIsOne \
    sed -i 's/^    if where in seen:$/    if False:/' agentnode_sdk/_agent_pip.py

one "R2-e starting refuses when the pin does not match" \
    agentnode_sdk/cli/gateway_commands.py \
    tests/test_runtime_pin.py::TestStartingRefuses \
    sed -i 's/^def _refuse_unless_pinned(/def _refuse_unless_pinned_disabled(/' \
      agentnode_sdk/cli/gateway_commands.py

one "R1-f an empty version is UNKNOWN, not running" \
    agentnode_sdk/gateway/runtime_pin.py \
    tests/test_runtime_pin.py::TestWhatCountsAsTheTestedInterpreter \
    sed -i 's/^SUPPORTED = (3, 12)$/SUPPORTED = (3, 11)/' agentnode_sdk/gateway/runtime_pin.py

one "R2-g a pin that names no artefact is refused, like one that names no interpreter" \
    agentnode_sdk/gateway/runtime_pin.py \
    tests/test_runtime_pin.py::TestAPinThatNamesNothingPinsNothing \
    sed -i 's|    if not expected:|    if False:|' agentnode_sdk/gateway/runtime_pin.py

one "R2-h and neither is one that names no commit" \
    agentnode_sdk/gateway/runtime_pin.py \
    tests/test_runtime_pin.py::TestAPinThatNamesNothingPinsNothing \
    sed -i 's|    if not pinned_commit:|    if False:|' agentnode_sdk/gateway/runtime_pin.py

one "KEY-i the private key is written whole, not in place" \
    agentnode_sdk/signing_key.py \
    tests/test_the_meter_key_is_written_whole.py::TestThePrivateKeyIsNeverReadableByAnybodyElse \
    sed -i 's#^    atomically(path.*#    path.write_bytes(pem_bytes)#' agentnode_sdk/signing_key.py

one "KEY-j and only one key is ever made" \
    agentnode_sdk/gateway/meter.py \
    tests/test_the_meter_key_is_written_whole.py::TestOneKeyEvenWhenEverybodyAsksAtOnce \
    sed -i 's|^    with ProcessLock(path):$|    if True:|' agentnode_sdk/gateway/meter.py

one "KEY-k the collector still collects real key material" \
    tests/test_what_is_kept.py \
    tests/test_what_is_kept.py::TestEverySecretShapeAgainstEverySink \
    sed -i 's|^            if key_path.is_file():$|            if key_path.is_file() and False:|' tests/test_what_is_kept.py

printf '\n=== '
[ "$FAILED" -eq 0 ] && echo "every counter-check removed a mechanism and the evidence failed without it." \
                    || { echo "AT LEAST ONE COUNTER-CHECK DID NOT ESTABLISH WHAT IT CLAIMS."; exit 1; }
