#!/usr/bin/env bash
# AgentNode Production Health Check Watchdog
# Runs via systemd timer every 2 min.
# Logs to journalctl (agentnode-healthcheck) + failure log.
set -uo pipefail

API_URL="http://localhost:8001"
FAIL_LOG="/var/log/agentnode-healthcheck-failures.log"

log_ok()   { echo "[OK]    $(date -Is) $1"; }
log_fail() { echo "[FAIL]  $(date -Is) $1"; echo "$(date -Is) $1" >> "$FAIL_LOG"; }

failures=0

# 1. /healthz — process alive
status=$(curl -sf -o /dev/null -w "%{http_code}" --max-time 5 "$API_URL/healthz" 2>/dev/null || echo "000")
if [ "$status" = "200" ]; then
    log_ok "healthz: $status"
else
    log_fail "healthz: HTTP $status (expected 200)"
    ((failures++))
fi

# 2. /readyz — postgres + redis + meili
readyz=$(curl -sf --max-time 10 "$API_URL/readyz" 2>/dev/null || echo "UNREACHABLE")
if [ "$readyz" = "UNREACHABLE" ]; then
    log_fail "readyz: unreachable"
    ((failures++))
else
    readyz_status=$(echo "$readyz" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "parse_error")
    if [ "$readyz_status" = "ready" ]; then
        log_ok "readyz: $readyz_status"
    else
        log_fail "readyz: $readyz_status"
        ((failures++))
    fi
fi

# 3. Disk usage (>85% = warning)
disk_pct=$(df / --output=pcent | tail -1 | tr -d ' %')
if [ "$disk_pct" -lt 85 ]; then
    log_ok "disk: ${disk_pct}%"
else
    log_fail "disk: ${disk_pct}% (threshold 85%)"
    ((failures++))
fi

# 4. Memory pressure (available < 200MB)
mem_avail=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
if [ "$mem_avail" -gt 200 ]; then
    log_ok "memory: ${mem_avail}MB available"
else
    log_fail "memory: only ${mem_avail}MB available (threshold 200MB)"
    ((failures++))
fi

# 5. Services running
if systemctl is-active --quiet agentnode-api; then
    log_ok "agentnode-api: running"
else
    log_fail "agentnode-api: not running"
    ((failures++))
fi

if systemctl is-active --quiet agentnode-web; then
    log_ok "agentnode-web: running"
else
    log_fail "agentnode-web: not running"
    ((failures++))
fi

# Summary
if [ "$failures" -gt 0 ]; then
    log_fail "SUMMARY: $failures check(s) failed"
    exit 1
else
    log_ok "SUMMARY: all checks passed"
fi
