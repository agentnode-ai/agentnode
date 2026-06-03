# AgentNode sandbox runner image — build/push/pin procedure: sdk/sandbox-image/README.md
#
# Runs BOTH community MCP servers (npx/uvx, P0.2) and community Python toolpacks
# (build into a volume + run from it, P0.3) under the SDK's hardened container
# flags (--read-only, --cap-drop=ALL, --security-opt=no-new-privileges,
# --user 1000:1000, --network none by default).
#
# IMAGE STILL PENDING until it is built, pushed to ghcr.io/agentnode-ai/sandbox
# and its DIGEST pinned into container_backend._BASE_IMAGE. Until then
# check_available() stays False and community execution is fully fail-closed.
#
# Must contain: node+npx (npm MCPs), uv+uvx (PyPI MCPs), python+pip (toolpack
# builds). Runs non-root; /install is pre-owned by uid 1000 so a fresh named
# build-volume mounted there is writable by the hardened --user 1000:1000
# (otherwise the volume root is owned by root and the build fails with EACCES).

# Pin the base by a concrete version. The operator MAY pin by @sha256 for full
# reproducibility (see README). Never use a floating tag in production.
FROM node:20.18.1-bookworm-slim

LABEL agentnode.component="sandbox"
LABEL agentnode.keep="true"

# uv version pin (PyPI). Bump deliberately, never floating.
ARG UV_VERSION=0.5.11

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 # Throwaway sandbox image: drop the PEP 668 "externally-managed" marker so the
 # toolpack build's `python -m pip install --target /install /src` works cleanly
 # without --break-system-packages quirks. Safe here — the container is locked
 # down by the runtime hardened flags, and this image installs nothing else.
 && rm -f /usr/lib/python3*/EXTERNALLY-MANAGED \
 # uv + uvx from PyPI (both entry points), pinned. Replaces the brittle
 # network-dependent `curl https://astral.sh/uv/install.sh | sh`.
 && python3 -m pip install --no-cache-dir "uv==${UV_VERSION}" \
 && ln -sf /usr/bin/python3 /usr/local/bin/python

# Non-root sandbox user + a writable target for the toolpack build volume.
RUN useradd -u 1000 -m sandbox || true \
 && mkdir -p /install \
 && chown 1000:1000 /install

USER 1000:1000
ENV HOME=/sandbox-home
