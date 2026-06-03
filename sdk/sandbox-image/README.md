# AgentNode Sandbox Image — Build, Push & Digest-Pin

This image is the isolation boundary for **community code**: community MCP servers
(`npx`/`uvx`, P0.2) and community Python toolpacks (build-into-volume + run, P0.3)
run inside it under the SDK's hardened flags. Until a real image is built, pushed
and its **digest** pinned, `check_available()` stays `False` and community
execution is **fully fail-closed** (blocked, never run on the host).

> **Status: PENDING.** `container_backend._BASE_IMAGE` holds a placeholder
> all-zero digest. This is the documented, reproducible procedure to activate it.
> The first build is done manually on the Hetzner host (it has Docker); this is a
> **transition** until CI / a reproducible build exists — do not treat a
> "built somewhere by hand" image as the long-term supply-chain story.

## Non-negotiables
- **GHCR only:** `ghcr.io/agentnode-ai/sandbox`.
- **Pin by DIGEST**, never a tag, never `:latest`.
- **No auto-pull** — the image is acquired by an explicit `agentnode sandbox pull`.
- **Routing must already be active** before pinning (P0.2/P0.3 are merged ✔), so
  availability never flips `True` without isolation actually wired up.
- A missing/unpinned image stays **fail-closed**.

## 1. Build (on the Docker host)
```bash
cd /opt/agentnode   # repo root on the host
docker build -f sdk/sandbox-image/Dockerfile \
  -t ghcr.io/agentnode-ai/sandbox:<version> \
  sdk/sandbox-image/
```
`<version>` = a deliberate version, e.g. `2026.06.0`. The base (`node:…-slim`) and
`UV_VERSION` are pinned in the Dockerfile — bump them consciously.

## 2. Smoke (verify the two real P0.3 gotchas are fixed)
```bash
# node/npx, uvx, python present; runs as uid 1000; /install writable by 1000.
docker run --rm --user 1000:1000 \
  ghcr.io/agentnode-ai/sandbox:<version> \
  sh -c 'node -v && npx --version && uvx --version && python -V && \
         touch /install/.probe && echo "/install writable OK"'
```
If `/install` is not writable, the `chown 1000:1000 /install` in the Dockerfile
is missing or the base changed — fix before pushing.

## 3. Push
```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u <user> --password-stdin
docker push ghcr.io/agentnode-ai/sandbox:<version>
```

## 4. Get the DIGEST
```bash
docker buildx imagetools inspect ghcr.io/agentnode-ai/sandbox:<version> \
  --format '{{.Manifest.Digest}}'
# or, after push:
docker inspect --format '{{index .RepoDigests 0}}' \
  ghcr.io/agentnode-ai/sandbox:<version>
```

## 5. Pin the digest (small reviewed code change)
Set `_BASE_IMAGE` in `sdk/agentnode_sdk/sandbox/container_backend.py` to:
```
ghcr.io/agentnode-ai/sandbox@sha256:<digest>
```
**Who:** the operator who built+pushed records the digest; the pin lands in the
same PR as the Sprint-A pre-activation fixes (or a tiny follow-up), reviewed.
This is the moment the bow goes live — only valid because routing is already
active.

## 6. Acquire on the target (no auto-pull)
```bash
agentnode sandbox pull     # explicit, pulls exactly the pinned digest
```
After the pull, `check_available()` is `True` on that host. Without it, execution
stays fail-closed. The full guided setup/repair UX is **Sprint B**.

## 7. Verify end-to-end
On the Docker host, run the gated E2E suite (see `sdk/tests/test_sandbox_e2e.py`):
```bash
AGENTNODE_SANDBOX_E2E=1 python -m pytest sdk/tests/test_sandbox_e2e.py -v
```
The key test: a **verified toolpack** is mounted container-readable at `/src`,
`pip install` runs as uid 1000 into the `/install` volume, the run mounts the
volume read-only and round-trips JSON over stdin/stdout — with **no host
`Popen`**. Plus a **verified MCP** that really starts in the container.

## Long-term
Replace this manual Hetzner build with a CI / reproducible build that produces a
verifiable digest, so the security-critical image has a full provenance trail.
