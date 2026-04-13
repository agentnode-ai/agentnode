# Releasing

This document describes how to publish new versions of the SDK, adapters, and deploy frontend/backend.

## SDK (agentnode-sdk on PyPI)

**Workflow**: `.github/workflows/publish-sdk.yml`
**Trigger**: GitHub Release with tag `sdk-v*`

### Steps

1. Bump `version` in `sdk/pyproject.toml`
2. Run tests locally: `cd sdk && pip install -e ".[dev]" && pytest -v`
3. Commit and push to `main`
4. Create a GitHub Release:
   ```bash
   gh release create sdk-v0.3.0 --target main --title "SDK v0.3.0" --notes "Release notes here"
   ```
5. The workflow will: checkout → install → test → build → upload to PyPI
6. Verify: `pip install agentnode-sdk==0.3.0`

### What the workflow does

- Runs only when the release tag starts with `sdk-v`
- Runs `pytest -v` before publishing (build fails if tests fail)
- Uses `twine upload --skip-existing` as a safety net

## LangChain Adapter (agentnode-langchain on PyPI)

**Workflow**: `.github/workflows/publish-adapter-langchain.yml`
**Trigger**: GitHub Release with tag `adapter-langchain-v*`

### Steps

1. Bump `version` in `adapter-langchain/pyproject.toml`
2. Commit and push to `main`
3. Create a GitHub Release:
   ```bash
   gh release create adapter-langchain-v0.2.0 --target main --title "LangChain Adapter v0.2.0" --notes "Release notes here"
   ```
4. The workflow will: checkout → build → upload to PyPI
5. Verify: `pip install agentnode-langchain==0.2.0`

### Important

- SDK and adapter are **version-independent** — releasing the SDK does not release the adapter
- Only release the adapter when its code actually changed
- Uses `twine upload --skip-existing` as a safety net

## Tag Conventions

| Component         | Tag pattern              | Example                    |
|-------------------|--------------------------|----------------------------|
| SDK               | `sdk-v<semver>`          | `sdk-v0.2.0`              |
| LangChain Adapter | `adapter-langchain-v<semver>` | `adapter-langchain-v0.1.1` |

Future adapters follow the same pattern: `adapter-<name>-v<semver>`.

## Deploy Paths

Two supported paths — pick one per release, don't mix them on the same change:

1. **`./deploy.sh [web|api|all]`** (recommended)
   Runs from your local machine, uploads via `scp`, installs pinned deps from
   `backend/requirements.lock`, runs `alembic upgrade head`, restarts services,
   and health-checks `/healthz` + `/readyz`. This is the same script CI uses
   conceptually and is the path documented in `deploy.sh` itself.

2. **GitHub Actions `.github/workflows/deploy.yml`**
   Triggered on push to `main`. Builds the Next.js standalone bundle, runs
   backend tests against Postgres + Redis, packages tarballs, uploads via
   `ssh-action`, runs migrations, restarts, and health-checks.

**Do not use `rsync` for production deploys.** `rsync --delete` against a live
`/opt/agentnode/backend` is fragile: a network blip mid-transfer can leave the
server in an inconsistent state where `.pyc` files exist for deleted `.py`
files, and you cannot atomically swap. Use `tar` + `scp` + in-place extract
instead (which is what `deploy.sh` and the workflow already do).

### Frontend: local script path

```bash
./deploy.sh web
```

Health check:
```bash
curl -s -o /dev/null -w "%{http_code}" https://agentnode.net/docs       # expect 200
curl -s -o /dev/null -w "%{http_code}" https://agentnode.net/builder    # expect 200
```

### Backend: local script path

Always snapshot Postgres before running a release that contains a migration.

```bash
# 1. Backup first
ssh -i ~/.ssh/agentnode root@91.98.142.165 \
  "docker exec agentnode-postgres-1 pg_dump -U agentnode agentnode" \
  > /tmp/agentnode_backup_$(date +%Y%m%d_%H%M%S).sql

# 2. Deploy — uploads app/, alembic/, alembic.ini, pyproject.toml, requirements.lock,
#    installs pinned deps, runs migrations, restarts, health-checks.
./deploy.sh api
```

Health check:
```bash
curl -s -o /dev/null -w "%{http_code}" https://api.agentnode.net/health  # expect 200
curl -s -o /dev/null -w "%{http_code}" https://api.agentnode.net/readyz  # expect 200
```

## Deploy Order

When releasing multiple components, follow this order:

1. All local tests green (SDK, frontend build, backend import)
2. Commit and push to `main`
3. SDK release to PyPI (if SDK changed)
4. Frontend deploy (if frontend changed)
5. Backend deploy (if backend changed) — always backup DB before migrations
6. Post-deploy verification

Never deploy in parallel. Each step must be green before the next starts.

## Secrets

| Secret       | Where        | Used by                |
|--------------|--------------|------------------------|
| `PYPI_TOKEN` | GitHub Repo  | SDK + adapter workflows |

## Infrastructure

| Component  | Host              | Service                  | Port |
|------------|-------------------|--------------------------|------|
| Frontend   | 91.98.142.165     | `agentnode-web.service`  | 3000 |
| Backend    | 91.98.142.165     | `agentnode-api.service`  | 8001 |
| PostgreSQL | Docker on same    | `agentnode-postgres-1`   | 5432 |
| Meilisearch| Docker on same    | `agentnode-meilisearch-1`| 7700 |
| MinIO      | Docker on same    | `agentnode-minio-1`      | 9000 |
| Nginx      | same              | reverse proxy            | 80/443 |
