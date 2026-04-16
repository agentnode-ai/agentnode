#!/usr/bin/env bash
# AgentNode Daily Error Digest
# Summarizes ERROR/WARNING from backend + healthcheck failures from last 24h.
# Runs via systemd timer once per day.
set -uo pipefail

DIGEST_DIR="/var/log/agentnode-digests"
mkdir -p "$DIGEST_DIR"

DATE=$(date -Is | cut -dT -f1)
SINCE="$(date -d '24 hours ago' '+%Y-%m-%d %H:%M:%S')"
DIGEST_FILE="$DIGEST_DIR/digest-$DATE.txt"

{
    echo "=== AgentNode Daily Digest — $DATE ==="
    echo ""

    # Backend errors/warnings (exclude noisy access logs)
    echo "--- Backend Errors/Warnings (last 24h) ---"
    errors=$(journalctl -u agentnode-api --since "$SINCE" --no-pager -o cat 2>/dev/null | grep -iE 'ERROR|WARNING|CRITICAL|Traceback|exception' | grep -v 'InsecureKeyLengthWarning' | head -100)
    if [ -n "$errors" ]; then
        echo "$errors"
        error_count=$(echo "$errors" | wc -l)
        echo ""
        echo "Total: $error_count entries"
    else
        echo "(none)"
    fi
    echo ""

    # Frontend errors
    echo "--- Frontend Errors (last 24h) ---"
    web_errors=$(journalctl -u agentnode-web --since "$SINCE" --no-pager -o cat 2>/dev/null | grep -iE 'error|ECONNREFUSED|TypeError|unhandled' | head -50)
    if [ -n "$web_errors" ]; then
        echo "$web_errors"
        web_count=$(echo "$web_errors" | wc -l)
        echo ""
        echo "Total: $web_count entries"
    else
        echo "(none)"
    fi
    echo ""

    # Health check failures
    echo "--- Health Check Failures (last 24h) ---"
    hc_fails=$(journalctl -u agentnode-healthcheck --since "$SINCE" --no-pager -o cat 2>/dev/null | grep '\[FAIL\]' | head -50)
    if [ -n "$hc_fails" ]; then
        echo "$hc_fails"
    else
        echo "(none)"
    fi
    echo ""

    # Resource snapshot
    echo "--- Current Resources ---"
    echo "Disk:   $(df / --output=pcent | tail -1 | tr -d ' ')"
    echo "Memory: $(awk '/MemAvailable/ {printf "%dMB available", $2/1024}' /proc/meminfo)"
    echo "Load:   $(cat /proc/loadavg | cut -d' ' -f1-3)"
    echo "Uptime: $(uptime -p)"
    echo ""

    # Request volume (rough count from access logs)
    echo "--- Request Volume (last 24h) ---"
    req_count=$(journalctl -u agentnode-api --since "$SINCE" --no-pager -o cat 2>/dev/null | grep -c 'agentnode.access:' || echo "0")
    echo "Total API requests: ~$req_count"
    echo ""

    echo "=== End of Digest ==="
} > "$DIGEST_FILE" 2>&1

echo "Digest written to $DIGEST_FILE"
cat "$DIGEST_FILE"

# Cleanup digests older than 30 days
find "$DIGEST_DIR" -name "digest-*.txt" -mtime +30 -delete 2>/dev/null || true
