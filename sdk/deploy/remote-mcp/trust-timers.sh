#!/bin/sh
# Trust refresh, trust cycle, control floor, trust watchdog and attestation-renewal timers on the control
# host (root). See agentnode_sdk/remote_mcp/trust_timers.py.
#
#   trust-timers.sh install <release-dir>   validate, bootstrap the renewal state, import a legacy
#                                           predecessor, back up the floor units, install atomically,
#                                           enable + restart, REQUIRE a finite NEXT for every timer
#   trust-timers.sh check                   every timer loaded, enabled, active, finite NEXT within bound
#   trust-timers.sh preflight               rollback quiescence only (renewal/cycle/refresh stopped, no
#                                           lock held, no open head-only operation or publication record,
#                                           no target refused on trust, >= 48 h on the managed default)
#   trust-timers.sh remove                  preflight, then legacy predecessor files, stop, disable, delete
#                                           units + drop-in + stamps + state files, restore the floor units
#                                           byte-exact, prove it
#
# The module is run from the release being installed (install) or the live one (check/remove), so a
# rollback removes the timers with the code that installed them. TRF_STAND overrides the stand.
set -eu
case "${1:-}" in
install) REL="$2"; PYTHONPATH="$REL/sdk" exec /opt/agentnode/venv/bin/python -m agentnode_sdk.remote_mcp.trust_timers install "$REL" ;;
check|remove|preflight) PYTHONPATH="${TRF_SDK:-/opt/agentnode/sdk}" exec /opt/agentnode/venv/bin/python -m agentnode_sdk.remote_mcp.trust_timers "$1" ;;
*) echo "usage: trust-timers.sh install <release-dir> | check | preflight | remove" >&2; exit 2 ;;
esac
