# AgentNode MCP sandbox runner image — IMAGE PENDING.
#
# This is the SPEC for the container image that community MCP servers run inside
# (P0.2). It is NOT yet built/published. Until a real image is built and pinned:
#   - container_backend._BASE_IMAGE keeps a PLACEHOLDER digest,
#   - check_available() stays False in real environments,
#   - community MCP execution is fully fail-closed (blocked, never host).
#
# To activate: build this image, then pin its DIGEST (never a tag) into
# container_backend._BASE_IMAGE — in the SAME change that has active routing in
# MCPServerProcess.start(), so availability never flips True without routing.
#
# Must contain: node+npx (npm MCPs), uv+uvx (PyPI MCPs), python. Runs non-root
# (the run flags also force --user 1000:1000).
FROM node:20-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates curl \
 && rm -rf /var/lib/apt/lists/* \
 && curl -LsSf https://astral.sh/uv/install.sh | sh \
 && cp /root/.local/bin/uv /usr/local/bin/uv \
 && cp /root/.local/bin/uvx /usr/local/bin/uvx \
 && ln -sf /usr/bin/python3 /usr/local/bin/python

RUN useradd -u 1000 -m sandbox || true
USER 1000:1000
ENV HOME=/sandbox-home
