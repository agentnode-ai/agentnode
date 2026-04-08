#!/usr/bin/env bash
# Weekly compatibility retest — run all models, update API data.
# Cron: 0 3 * * 0  root  /opt/agentnode/sdk/scripts/weekly_retest.sh >> /var/log/agentnode-retest.log 2>&1
#
# Exit codes:
#   0 — success, data updated
#   1 — test or generation failed, no data written
#   2 — data updated but S-tier drift detected (>5 drop)

set -euo pipefail

REPO_DIR="/opt/agentnode"
SDK_DIR="${REPO_DIR}/sdk"
SCRIPTS_DIR="${SDK_DIR}/scripts"
ARTIFACTS_DIR="${SDK_DIR}/.artifacts/batch_reports"
STAGING_DIR="/tmp/compat_staging_$(date +%s)"
SUMMARY_FILE="${ARTIFACTS_DIR}/last_run_summary.json"

BATCH_VERIFY="${SCRIPTS_DIR}/batch_verify.py"
GENERATE="${SCRIPTS_DIR}/generate_compatibility_artifacts.py"
MERGED_MATRIX="${ARTIFACTS_DIR}/merged_matrix.json"

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
BATCH_ID="batch-$(date -u +"%Y-%m-%dT%H:%MZ")"

echo "============================================="
echo "AgentNode Weekly Compatibility Retest"
echo "Started: ${NOW}"
echo "============================================="

# Ensure staging dir
mkdir -p "${STAGING_DIR}"
trap 'rm -rf "${STAGING_DIR}"' EXIT

# --- Step 1: Run batch verification ---
echo ""
echo "[1/4] Running batch verification..."
cd "${SDK_DIR}"

if ! python "${BATCH_VERIFY}"; then
    echo "ERROR: batch_verify.py failed"
    exit 1
fi

# --- Step 2: Merge results ---
echo ""
echo "[2/4] Merging results..."

# batch_verify.py writes individual reports; the merge script combines them.
# If merged_matrix.json was already updated by batch_verify, skip.
if [ ! -f "${MERGED_MATRIX}" ]; then
    echo "ERROR: merged_matrix.json not found after batch verify"
    exit 1
fi

# Snapshot previous S-tier count for drift detection
PREV_S_TIER=0
if [ -f "${SUMMARY_FILE}" ]; then
    PREV_S_TIER=$(python -c "
import json, sys
try:
    d = json.load(open('${SUMMARY_FILE}'))
    print(d.get('s_tier_count', 0))
except: print(0)
" 2>/dev/null || echo 0)
fi

# --- Step 3: Generate backend artifact ---
echo ""
echo "[3/4] Generating backend compatibility data..."

python "${GENERATE}" --target backend --output-dir "${STAGING_DIR}/"

# Validate JSON
if ! python -c "import json; json.load(open('${STAGING_DIR}/compatibility_matrix.json'))"; then
    echo "ERROR: Generated JSON is invalid"
    exit 1
fi

# Atomic replace
mv "${STAGING_DIR}/compatibility_matrix.json" "${REPO_DIR}/backend/data/compatibility_matrix.json"
echo "Backend data updated (mtime-based reload will pick it up)"

# --- Step 4: Write summary ---
echo ""
echo "[4/4] Writing run summary..."

CURR_S_TIER=$(python -c "
import json
d = json.load(open('${REPO_DIR}/backend/data/compatibility_matrix.json'))
print(d.get('s_tier_count', 0))
")

TOTAL_MODELS=$(python -c "
import json
d = json.load(open('${REPO_DIR}/backend/data/compatibility_matrix.json'))
print(d.get('total_models', 0))
")

DELTA=$((CURR_S_TIER - PREV_S_TIER))

# Determine status
STATUS="ok"
if [ "${PREV_S_TIER}" -gt 0 ] && [ "${DELTA}" -lt -5 ]; then
    STATUS="drift_warning"
    echo "WARNING: S-tier count dropped by ${DELTA} (${PREV_S_TIER} -> ${CURR_S_TIER})"
fi

# Write machine-readable summary
python -c "
import json
summary = {
    'generated_at': '${NOW}',
    'source_version': '${BATCH_ID}',
    'total_models': ${TOTAL_MODELS},
    's_tier_count': ${CURR_S_TIER},
    'delta_s_tier': ${DELTA},
    'status': '${STATUS}',
}
with open('${SUMMARY_FILE}', 'w') as f:
    json.dump(summary, f, indent=2)
    f.write('\n')
print('Summary written to ${SUMMARY_FILE}')
"

echo ""
echo "============================================="
echo "Retest complete"
echo "  Total models:  ${TOTAL_MODELS}"
echo "  S-tier:        ${CURR_S_TIER} (delta: ${DELTA})"
echo "  Status:        ${STATUS}"
echo "============================================="

if [ "${STATUS}" = "drift_warning" ]; then
    exit 2
fi

exit 0
