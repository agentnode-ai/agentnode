# AgentNode — Deployment Guide

## Architecture Overview

```
                    ┌─────────────┐
    Internet ──────►│  Traefik /   │
                    │  Nginx       │
                    └──┬──────┬───┘
                       │      │
              ┌────────┘      └────────┐
              ▼                        ▼
    ┌─────────────────┐    ┌──────────────────┐
    │ Backend (FastAPI)│    │ Frontend (Next.js)│
    │   Port 8001      │    │    Port 3000      │
    └──┬──┬──┬──┬─────┘    └──────────────────┘
       │  │  │  │
       ▼  ▼  ▼  ▼
    PG Redis Meili MinIO
```

## Prerequisites

- Linux server (Ubuntu 22.04+ recommended)
- Python 3.12+
- Node.js 20+
- PostgreSQL 16
- Redis 7
- Meilisearch 1.12+
- MinIO (or S3-compatible storage)

## Quick Start with Docker Compose (Development)

```bash
git clone https://github.com/agentnode-ai/agentnode.git
cd agentnode

# Start infrastructure
docker compose up -d

# Backend
cd backend
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
python scripts/seed_capabilities.py
uvicorn app.main:app --host 0.0.0.0 --port 8001

# Frontend (separate terminal)
cd web
cp .env.example .env.local
npm install
npm run dev
```

## Production Deployment

### 1. Infrastructure Setup

#### PostgreSQL

```bash
sudo apt install postgresql-16
sudo -u postgres createuser agentnode
sudo -u postgres createdb agentnode -O agentnode
sudo -u postgres psql -c "ALTER USER agentnode PASSWORD 'YOUR_STRONG_PASSWORD';"
```

#### Redis

```bash
sudo apt install redis-server
sudo systemctl enable redis-server
```

#### Meilisearch

```bash
curl -L https://install.meilisearch.com | sh
sudo mv meilisearch /usr/local/bin/

# Create systemd service
sudo tee /etc/systemd/system/meilisearch.service <<EOF
[Unit]
Description=Meilisearch
After=network.target

[Service]
ExecStart=/usr/local/bin/meilisearch --master-key YOUR_MEILI_KEY --db-path /var/lib/meilisearch
Restart=always
User=meilisearch

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now meilisearch
```

#### MinIO

```bash
wget https://dl.min.io/server/minio/release/linux-amd64/minio
chmod +x minio
sudo mv minio /usr/local/bin/

# Create systemd service
sudo tee /etc/systemd/system/minio.service <<EOF
[Unit]
Description=MinIO
After=network.target

[Service]
Environment=MINIO_ROOT_USER=YOUR_ACCESS_KEY
Environment=MINIO_ROOT_PASSWORD=YOUR_SECRET_KEY
ExecStart=/usr/local/bin/minio server /data/minio --console-address ":9001"
Restart=always
User=minio

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now minio
```

Create the artifacts bucket:
```bash
mc alias set local http://localhost:9000 YOUR_ACCESS_KEY YOUR_SECRET_KEY
mc mb local/agentnode-artifacts
```

### 2. Backend Deployment

```bash
# Clone and setup
cd /opt
git clone https://github.com/agentnode-ai/agentnode.git
cd agentnode/backend

# Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with production values:
#   DATABASE_URL=postgresql+asyncpg://agentnode:PASSWORD@localhost:5432/agentnode
#   JWT_SECRET=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
#   S3_ACCESS_KEY=YOUR_ACCESS_KEY
#   S3_SECRET_KEY=YOUR_SECRET_KEY
#   S3_PUBLIC_ENDPOINT=https://s3.yourdomain.com
#   ENVIRONMENT=production

# Run migrations and seed
alembic upgrade head
python scripts/seed_capabilities.py

# Create systemd service
sudo tee /etc/systemd/system/agentnode-api.service <<EOF
[Unit]
Description=AgentNode Backend API
After=network.target postgresql.service redis.service

[Service]
WorkingDirectory=/opt/agentnode/backend
ExecStart=/opt/agentnode/backend/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001
Restart=always
EnvironmentFile=/opt/agentnode/backend/.env

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now agentnode-api
```

### 3. Frontend Deployment

```bash
cd /opt/agentnode/web
cp .env.example .env.local
echo "BACKEND_URL=http://localhost:8001" > .env.local

npm install
npm run build

sudo tee /etc/systemd/system/agentnode-web.service <<EOF
[Unit]
Description=AgentNode Web Frontend
After=network.target

[Service]
WorkingDirectory=/opt/agentnode/web
ExecStart=/usr/bin/npm start
Restart=always
Environment=PORT=3000

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now agentnode-web
```

### 4. Reverse Proxy (Nginx)

```nginx
# /etc/nginx/sites-available/agentnode
server {
    listen 80;
    server_name yourdomain.com www.yourdomain.com;

    location / {
        proxy_pass http://localhost:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name api.yourdomain.com;

    location / {
        proxy_pass http://localhost:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name s3.yourdomain.com;

    location / {
        proxy_pass http://localhost:9000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Add SSL with certbot:
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com -d api.yourdomain.com -d s3.yourdomain.com
```

### 5. Create Admin User

```bash
cd /opt/agentnode/backend
source .venv/bin/activate
python -c "
import asyncio
from app.database import async_session_factory
from app.auth.models import User
from sqlalchemy import update

async def make_admin(email):
    async with async_session_factory() as session:
        await session.execute(update(User).where(User.email == email).values(is_admin=True))
        await session.commit()
        print(f'Admin granted to {email}')

asyncio.run(make_admin('admin@yourdomain.com'))
"
```

## Environment Variables Reference

See `backend/.env.example` for all available variables with descriptions.

## Monitoring

### Health Checks

- `GET /healthz` — Basic liveness check
- `GET /readyz` — Full readiness check (Postgres, Redis, Meilisearch)

### Logs

```bash
journalctl -u agentnode-api -f    # Backend logs
journalctl -u agentnode-web -f    # Frontend logs
```

## Updating

```bash
cd /opt/agentnode
git pull origin main

# Backend
cd backend && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
sudo systemctl restart agentnode-api

# Frontend
cd ../web && npm install && npm run build
sudo systemctl restart agentnode-web
```
