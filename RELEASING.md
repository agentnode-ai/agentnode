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

## Frontend Deploy

The frontend is not auto-deployed via CI. Manual deploy via SSH:

```bash
ssh -i ~/.ssh/agentnode root@91.98.142.165 bash -s <<'DEPLOY'
cd /opt/agentnode-repo && git pull origin main
rsync -av --delete \
  --exclude='.next' --exclude='node_modules' --exclude='.env.local' \
  /opt/agentnode-repo/web/ /opt/agentnode/web/
cd /opt/agentnode/web && npm ci && npm run build
systemctl restart agentnode-web
DEPLOY
```

Health check:
```bash
curl -s -o /dev/null -w "%{http_code}" https://agentnode.net/docs       # expect 200
curl -s -o /dev/null -w "%{http_code}" https://agentnode.net/builder    # expect 200
```

## Backend Deploy

The backend is not auto-deployed via CI. Manual deploy via SSH:

```bash
ssh -i ~/.ssh/agentnode root@91.98.142.165 bash -s <<'DEPLOY'
# 1. Backup affected tables before migrations
docker exec agentnode-postgres-1 pg_dump -U agentnode agentnode > /tmp/agentnode_backup_$(date +%Y%m%d).sql

# 2. Sync code
cd /opt/agentnode-repo && git pull origin main
rsync -av --delete \
  --exclude='.venv' --exclude='__pycache__' --exclude='.env' --exclude='*.pyc' \
  /opt/agentnode-repo/backend/ /opt/agentnode/backend/

# 3. Run migrations
cd /opt/agentnode/backend && .venv/bin/python -m alembic upgrade head

# 4. Restart
systemctl restart agentnode-api
DEPLOY
```

Health check:
```bash
curl -s -o /dev/null -w "%{http_code}" https://api.agentnode.net/health  # expect 200
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
