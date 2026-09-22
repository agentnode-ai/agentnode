#!/usr/bin/env bash
# The counter-checks for mtls-loopback-identity-r1 (profile M16), from sdk/.
# Each one: control green -> mutation lands (digest moves, text present) -> a named test fails
# for the predicted reason -> the named siblings stay green -> byte-exact restore, by digest.
# The work is in mtls_counter_checks.py; this is the entry point the profile names.
set -euo pipefail
cd "$(dirname "$0")/.."
exec "${PYTHON:-python}" scripts/mtls_counter_checks.py "$@"
