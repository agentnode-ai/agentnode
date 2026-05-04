#!/usr/bin/env bash
# Ensure verifier Docker images exist. Rebuilds if missing.
# Run via cron or systemd timer to survive Docker prune.
#
# Usage: ./scripts/ensure_verifier_images.sh
# Cron:  */30 * * * * /opt/agentnode/scripts/ensure_verifier_images.sh >> /var/log/agentnode-verifier-images.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/../backend"

IMAGES=(
    "agentnode-verifier:latest|Dockerfile.verifier"
    "agentnode-verifier-browser:latest|Dockerfile.verifier-browser"
)

for entry in "${IMAGES[@]}"; do
    IMAGE="${entry%%|*}"
    DOCKERFILE="${entry##*|}"

    if docker image inspect "$IMAGE" >/dev/null 2>&1; then
        continue
    fi

    echo "$(date -Iseconds) Image $IMAGE missing — rebuilding from $DOCKERFILE"
    docker build -f "$BACKEND_DIR/$DOCKERFILE" -t "$IMAGE" "$BACKEND_DIR"
    echo "$(date -Iseconds) Image $IMAGE rebuilt successfully"
done
