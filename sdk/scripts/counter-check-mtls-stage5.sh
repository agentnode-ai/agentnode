#!/usr/bin/env bash
# The counter-checks for mtls-revocation-time-r1 (profile R14), from sdk/.
# Each one: control green AND RUN (a skipped control is "no test ran", reported NOT RUN) ->
# mutation lands (digest moves, text present) -> a named test fails for the predicted reason ->
# the named siblings stay green -> byte-exact restore, by digest.
# (j1) exists only as root on Linux: run it there, e.g.
#   sudo AGENTNODE_FLOOR_TEST_ACCOUNT=agentnode-worker ./scripts/counter-check-mtls-stage5.sh \
#        --only floor-given-to-a-service
# The work is in mtls_stage5_counter_checks.py; this is the entry point the profile names.
set -euo pipefail
cd "$(dirname "$0")/.."
exec "${PYTHON:-python}" scripts/mtls_stage5_counter_checks.py "$@"
