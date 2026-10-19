#!/bin/sh
# Trust refresh + trust watchdog timers on the control host (root). See agentnode_sdk/remote_mcp/trust_timers.py.
#
#   trust-timers.sh install <release-dir>   validate, install atomically, enable + restart, REQUIRE a finite NEXT
#   trust-timers.sh check                   both timers loaded, enabled, active, finite NEXT within bound
#   trust-timers.sh remove                  stop, disable, delete units + drop-in + stamps + state, prove absent
#
# The module is run from the release being installed (install) or the live one (check/remove), so a
# rollback removes the timers with the code that installed them.
set -eu
case "${1:-}" in
install) REL="$2"; PYTHONPATH="$REL/sdk" exec /opt/agentnode/venv/bin/python -m agentnode_sdk.remote_mcp.trust_timers install "$REL" ;;
check|remove) PYTHONPATH="${TRF_SDK:-/opt/agentnode/sdk}" exec /opt/agentnode/venv/bin/python -m agentnode_sdk.remote_mcp.trust_timers "$1" ;;
*) echo "usage: trust-timers.sh install <release-dir> | check | remove" >&2; exit 2 ;;
esac
