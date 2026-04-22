#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# publish_agents.sh — Batch-publish all agent starter packs to AgentNode
#
# Usage:
#   AUTH_TOKEN=<token> ./scripts/publish_agents.sh
#   ./scripts/publish_agents.sh                      # will prompt for token
#
# Requires: bash, curl, tar, python3 (for YAML→JSON conversion)
# ---------------------------------------------------------------------------

API_BASE="https://api.agentnode.net"
PUBLISH_URL="${API_BASE}/v1/packages/publish"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
STARTER_PACKS_DIR="${REPO_ROOT}/starter-packs"

# ── Auth token ───────────────────────────────────────────────────────────────
if [[ -z "${AUTH_TOKEN:-}" ]]; then
  printf "AUTH_TOKEN not set. Enter your AgentNode API token: "
  read -r AUTH_TOKEN
  if [[ -z "${AUTH_TOKEN}" ]]; then
    echo "ERROR: No token provided. Aborting."
    exit 1
  fi
fi

# ── Temp directory for artifacts ─────────────────────────────────────────────
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

# ── Counters ─────────────────────────────────────────────────────────────────
total=0
published=0
skipped=0
failed=0
declare -a failures=()

# ── YAML → JSON helper (uses python3, available everywhere we target) ────────
yaml_to_json() {
  python3 -c "
import sys, json
try:
    import yaml
except ImportError:
    # PyYAML not installed — fall back to a minimal parser that handles
    # the flat-ish manifests we need.  This should not happen in practice
    # because the backend venv always has PyYAML.
    print('ERROR: PyYAML is required (pip install pyyaml)', file=sys.stderr)
    sys.exit(1)

with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
print(json.dumps(data))
" "$1"
}

# ── Main loop ────────────────────────────────────────────────────────────────
echo "==========================================="
echo " AgentNode Agent Batch Publish"
echo "==========================================="
echo "API:    ${PUBLISH_URL}"
echo "Packs:  ${STARTER_PACKS_DIR}"
echo ""

for pack_dir in "${STARTER_PACKS_DIR}"/*/; do
  manifest="${pack_dir}agentnode.yaml"

  # Skip directories without a manifest
  [[ -f "${manifest}" ]] || continue

  # Only publish agents (not toolpacks)
  pkg_type=$(python3 -c "
import yaml, sys
with open(sys.argv[1]) as f:
    d = yaml.safe_load(f)
print(d.get('package_type', ''))
" "${manifest}")

  [[ "${pkg_type}" == "agent" ]] || continue

  # Extract slug from package_id
  slug=$(python3 -c "
import yaml, sys
with open(sys.argv[1]) as f:
    d = yaml.safe_load(f)
print(d.get('package_id', ''))
" "${manifest}")

  if [[ -z "${slug}" ]]; then
    echo "WARN: No package_id in ${manifest}, skipping."
    continue
  fi

  total=$((total + 1))

  echo "-------------------------------------------"
  echo "[${total}] Publishing: ${slug}"

  # ── Convert manifest YAML → JSON ────────────────────────────────────────
  manifest_json="${WORK_DIR}/${slug}_manifest.json"
  if ! yaml_to_json "${manifest}" > "${manifest_json}"; then
    echo "  FAIL  Could not convert manifest to JSON"
    failed=$((failed + 1))
    failures+=("${slug}: manifest conversion failed")
    continue
  fi

  # ── Build tar.gz artifact ───────────────────────────────────────────────
  artifact="${WORK_DIR}/${slug}.tar.gz"
  if ! tar czf "${artifact}" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.git' \
    --exclude='node_modules' \
    --exclude='.venv' \
    --exclude='venv' \
    --exclude='env' \
    --exclude='dist' \
    --exclude='build' \
    --exclude='*.egg-info' \
    --exclude='.pytest_cache' \
    --exclude='.mypy_cache' \
    --exclude='.ruff_cache' \
    --exclude='.DS_Store' \
    --exclude='.env' \
    --exclude='.env.local' \
    -C "$(dirname "${pack_dir%/}")" \
    "$(basename "${pack_dir%/}")"; then
    echo "  FAIL  Could not build artifact"
    failed=$((failed + 1))
    failures+=("${slug}: artifact build failed")
    continue
  fi

  artifact_size=$(du -k "${artifact}" | cut -f1)
  echo "  Artifact: ${artifact_size} KB"

  # Rate-limit guard: wait 7s between publishes
  if [[ ${total} -gt 1 ]]; then
    sleep 7
  fi

  # ── POST to publish endpoint ────────────────────────────────────────────
  http_response="${WORK_DIR}/${slug}_response.json"
  http_code=$(curl -s -o "${http_response}" -w "%{http_code}" \
    -X POST "${PUBLISH_URL}" \
    -H "Authorization: Bearer ${AUTH_TOKEN}" \
    -F "manifest=<${manifest_json}" \
    -F "artifact=@${artifact};type=application/gzip" \
    --max-time 120)

  if [[ "${http_code}" == "201" ]]; then
    version=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('version','?'))" "${http_response}" 2>/dev/null || echo "?")
    echo "  OK    Published ${slug}@${version}"
    published=$((published + 1))

  elif [[ "${http_code}" == "409" ]]; then
    echo "  SKIP  Version already exists (409)"
    skipped=$((skipped + 1))

  elif [[ "${http_code}" == "429" ]]; then
    echo "  RATE LIMITED — waiting 60s and retrying..."
    sleep 60
    http_code=$(curl -s -o "${http_response}" -w "%{http_code}" \
      -X POST "${PUBLISH_URL}" \
      -H "Authorization: Bearer ${AUTH_TOKEN}" \
      -F "manifest=<${manifest_json}" \
      -F "artifact=@${artifact};type=application/gzip" \
      --max-time 120)
    if [[ "${http_code}" == "201" ]]; then
      echo "  OK    Published ${slug} (retry)"
      published=$((published + 1))
    elif [[ "${http_code}" == "409" ]]; then
      echo "  SKIP  Version already exists (409)"
      skipped=$((skipped + 1))
    else
      echo "  FAIL  HTTP ${http_code} (retry)"
      failed=$((failed + 1))
      failures+=("${slug}: HTTP ${http_code} on retry")
    fi

  else
    error_msg=$(python3 -c "
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    e = d.get('error', d.get('detail', d))
    if isinstance(e, dict):
        print(f\"[{e.get('code','?')}] {e.get('message','unknown')}\")
    else:
        print(e)
except Exception:
    print('(could not parse response)')
" "${http_response}" 2>/dev/null || echo "(no response body)")
    echo "  FAIL  HTTP ${http_code}: ${error_msg}"
    failed=$((failed + 1))
    failures+=("${slug}: HTTP ${http_code} — ${error_msg}")
  fi
done

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "==========================================="
echo " Summary"
echo "==========================================="
echo "  Total agents:  ${total}"
echo "  Published:     ${published}"
echo "  Skipped (409): ${skipped}"
echo "  Failed:        ${failed}"

if [[ ${#failures[@]} -gt 0 ]]; then
  echo ""
  echo "Failures:"
  for f in "${failures[@]}"; do
    echo "  - ${f}"
  done
fi

echo "==========================================="

# Exit non-zero if anything failed (but not for skips)
if [[ "${failed}" -gt 0 ]]; then
  exit 1
fi
