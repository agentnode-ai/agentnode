# Restart Blocker: Verification Sandbox Image Missing

Date: 2026-05-26
Status: RESOLVED — image built, service restarted successfully

## Current State

| Fact | Value |
|------|-------|
| Service running since | 2026-05-11 08:49:54 UTC |
| Service HEAD when started | Before `check_verification_sandbox()` existed |
| `.env` VERIFICATION_SANDBOX_MODE | `container` |
| `.env` VERIFICATION_CONTAINER_IMAGE | `agentnode-verifier:latest` |
| Docker image `agentnode-verifier:latest` | **Missing** |
| Container runtime | Docker (`/usr/bin/docker`) |
| `check_verification_sandbox()` | Calls `sys.exit(1)` if mode=container and image missing |

## Impact

**Any `systemctl restart agentnode-api` will fail.** The service
imports `app.config` at startup, which runs `check_verification_sandbox()`,
which exits if the container image is not found.

The currently running process was started before this guard existed,
so it works fine. But a restart picks up the new code including the
guard.

This blocks:
- Registry signing deployment (Sprint 1 code is pulled but not active)
- Any backend hotfix that requires a service restart
- Any config change that requires a restart

## Investigation Results (2026-05-26)

1. **When was `container` set in `.env`?**
   - Unknown — the service was last restarted 2026-05-11, before the
     `check_verification_sandbox()` guard existed. The `.env` value
     was likely set speculatively. The image was never built on this
     server (no `agentnode-verifier` in `docker images`).

2. **Dockerfile exists and is trivial:**
   - `Dockerfile.verifier` — Python 3.12-slim + vcrpy. ~10 second build.
   - `Dockerfile.verifier-browser` — separate browser image.
   - Build: `docker build -f Dockerfile.verifier -t agentnode-verifier:latest .`

3. **What does `container` mode do?**
   - Used ONLY by the verification pipeline (import, smoke test steps)
   - `verification/sandbox.py` runs publisher code in container with
     `--network=none` for isolation
   - `verification/steps.py` selects container vs subprocess execution
   - **Not used by API serving, signing, or any trust-critical path**
   - Subprocess mode is functionally equivalent but without network
     isolation for publisher code

4. **Safest fix: build the image.**
   - It's a 10-second build of a trivial Dockerfile
   - No `.env` change needed
   - Container isolation for verification is the intended production mode

## Resolution Options

| Option | Change | Risk |
|--------|--------|------|
| A: Build image | `docker build -t agentnode-verifier:latest .` | Unknown — Dockerfile may not build cleanly |
| B: Switch to subprocess | `.env`: `VERIFICATION_SANDBOX_MODE=subprocess` | Verification runs without isolation — acceptable if it was subprocess before |
| C: Make guard warn-only | Code change in `config.py` | Weakens the fail-closed design |

**Recommendation: Option A — build the image.** It's a trivial
Dockerfile (Python 3.12-slim + vcrpy, ~10 seconds). No config change
needed. Container isolation is the intended production mode.

```bash
cd /opt/agentnode/backend
docker build -f Dockerfile.verifier -t agentnode-verifier:latest .
```

After build succeeds, the service can restart cleanly.

## Signing Deploy Status

The Sprint 1 code (`git pull`) is on the server at `f9e51b9` but NOT
active. The running process is still on `3eb8c41`. Once the sandbox
blocker is resolved and the service restarts, signing middleware will
activate in degraded mode (no signing key set yet).
