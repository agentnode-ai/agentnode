#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# AgentNode Production Deploy Script
# Usage: ./deploy.sh [web|api|all]   (default: all)
# ─────────────────────────────────────────────────────────────────────────────

# ── Config ───────────────────────────────────────────────────────────────────
SSH_KEY="/c/Users/User/.ssh/agentnode"
SSH_HOST="root@91.98.142.165"
SSH_CMD="ssh -i $SSH_KEY -o StrictHostKeyChecking=no $SSH_HOST"
SCP_CMD="scp -i $SSH_KEY -o StrictHostKeyChecking=no"

REMOTE_WEB="/opt/agentnode/web"
REMOTE_BACKEND="/opt/agentnode/backend"

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
WEB_DIR="$REPO_ROOT/web"
BACKEND_DIR="$REPO_ROOT/backend"

TARGET="${1:-all}"

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[FAIL]${NC}  $*"; }
step()    { echo -e "\n${CYAN}${BOLD}── $* ──${NC}"; }

# ── Validation ───────────────────────────────────────────────────────────────
if [[ "$TARGET" != "web" && "$TARGET" != "api" && "$TARGET" != "all" ]]; then
    error "Invalid target: $TARGET"
    echo "Usage: $0 [web|api|all]"
    exit 1
fi

if [[ ! -f "$SSH_KEY" ]]; then
    error "SSH key not found: $SSH_KEY"
    exit 1
fi

echo -e "${BOLD}${CYAN}"
echo "╔══════════════════════════════════════════╗"
echo "║       AgentNode Production Deploy        ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"
info "Target:  ${BOLD}$TARGET${NC}"
info "Server:  ${BOLD}91.98.142.165${NC}"
info "Repo:    ${BOLD}$REPO_ROOT${NC}"
echo ""

# ── Pre-flight: verify SSH connectivity ──────────────────────────────────────
step "Pre-flight checks"
info "Testing SSH connection..."
if ! $SSH_CMD "echo 'SSH OK'" > /dev/null 2>&1; then
    error "Cannot connect to server via SSH"
    exit 1
fi
success "SSH connection verified"

# ══════════════════════════════════════════════════════════════════════════════
# WEB DEPLOYMENT
# ══════════════════════════════════════════════════════════════════════════════
deploy_web() {
    step "Building Next.js (standalone)"
    cd "$WEB_DIR"
    info "Running next build..."
    npx next build
    success "Build completed"

    # Verify build output exists
    if [[ ! -f "$WEB_DIR/.next/standalone/server.js" ]]; then
        error "Build output missing: .next/standalone/server.js"
        exit 1
    fi

    step "Deploying web frontend"

    # 1. Upload standalone root files (server.js, package.json, node_modules)
    info "Uploading standalone files..."
    $SCP_CMD -r "$WEB_DIR/.next/standalone/server.js" "$SSH_HOST:$REMOTE_WEB/server.js"
    $SCP_CMD -r "$WEB_DIR/.next/standalone/package.json" "$SSH_HOST:$REMOTE_WEB/package.json"
    $SCP_CMD -r "$WEB_DIR/.next/standalone/node_modules" "$SSH_HOST:$REMOTE_WEB/"
    success "Standalone files uploaded"

    # 2. Upload .next/server (compiled pages, app routes, chunks)
    info "Uploading .next/server..."
    $SSH_CMD "mkdir -p $REMOTE_WEB/.next"
    $SCP_CMD -r "$WEB_DIR/.next/standalone/.next/server" "$SSH_HOST:$REMOTE_WEB/.next/"
    success ".next/server uploaded"

    # 3. Upload .next JSON manifests (routes-manifest, build-manifest, etc.)
    info "Uploading .next manifests..."
    for jsonfile in "$WEB_DIR/.next/standalone/.next/"*.json; do
        if [[ -f "$jsonfile" ]]; then
            $SCP_CMD "$jsonfile" "$SSH_HOST:$REMOTE_WEB/.next/"
        fi
    done
    # Also copy BUILD_ID
    if [[ -f "$WEB_DIR/.next/standalone/.next/BUILD_ID" ]]; then
        $SCP_CMD "$WEB_DIR/.next/standalone/.next/BUILD_ID" "$SSH_HOST:$REMOTE_WEB/.next/"
    fi
    success ".next manifests uploaded"

    # 4. Upload .next/static (hashed JS/CSS chunks)
    info "Uploading .next/static..."
    $SCP_CMD -r "$WEB_DIR/.next/static" "$SSH_HOST:$REMOTE_WEB/.next/"
    success ".next/static uploaded"

    # 5. Upload public/ assets
    if [[ -d "$WEB_DIR/public" ]]; then
        info "Uploading public/ assets..."
        $SCP_CMD -r "$WEB_DIR/public" "$SSH_HOST:$REMOTE_WEB/"
        success "public/ assets uploaded"
    fi

    # 6. Restart service
    step "Restarting agentnode-web"
    info "Restarting systemd service..."
    $SSH_CMD "systemctl restart agentnode-web"
    sleep 3

    # 7. Verify service is active
    WEB_STATUS=$($SSH_CMD "systemctl is-active agentnode-web" 2>/dev/null || true)
    if [[ "$WEB_STATUS" == "active" ]]; then
        success "agentnode-web is ${GREEN}active${NC}"
    else
        error "agentnode-web is ${RED}$WEB_STATUS${NC}"
        warn "Checking logs..."
        $SSH_CMD "journalctl -u agentnode-web --no-pager -n 20" || true
        exit 1
    fi
}

# ══════════════════════════════════════════════════════════════════════════════
# API DEPLOYMENT
# ══════════════════════════════════════════════════════════════════════════════
deploy_api() {
    step "Deploying backend API"

    # Verify source exists
    if [[ ! -d "$BACKEND_DIR/app" ]]; then
        error "Backend app directory not found: $BACKEND_DIR/app"
        exit 1
    fi

    # 1. Upload backend/app
    info "Uploading backend/app..."
    $SCP_CMD -r "$BACKEND_DIR/app" "$SSH_HOST:$REMOTE_BACKEND/"
    success "backend/app uploaded"

    # 2. Restart service
    step "Restarting agentnode-api"
    info "Restarting systemd service..."
    $SSH_CMD "systemctl restart agentnode-api"
    sleep 3

    # 3. Verify service is active
    API_STATUS=$($SSH_CMD "systemctl is-active agentnode-api" 2>/dev/null || true)
    if [[ "$API_STATUS" == "active" ]]; then
        success "agentnode-api is ${GREEN}active${NC}"
    else
        error "agentnode-api is ${RED}$API_STATUS${NC}"
        warn "Checking logs..."
        $SSH_CMD "journalctl -u agentnode-api --no-pager -n 20" || true
        exit 1
    fi
}

# ══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECKS
# ══════════════════════════════════════════════════════════════════════════════
health_check() {
    step "Health checks"

    local ALL_OK=true

    if [[ "$TARGET" == "web" || "$TARGET" == "all" ]]; then
        info "Checking web frontend (port 3000)..."
        WEB_HTTP=$($SSH_CMD "curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://localhost:3000/" 2>/dev/null || echo "000")
        if [[ "$WEB_HTTP" == "200" || "$WEB_HTTP" == "308" || "$WEB_HTTP" == "301" || "$WEB_HTTP" == "302" ]]; then
            success "Web frontend responded with HTTP $WEB_HTTP"
        else
            error "Web frontend returned HTTP $WEB_HTTP"
            ALL_OK=false
        fi

        info "Checking nginx (port 3080)..."
        NGINX_HTTP=$($SSH_CMD "curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://localhost:3080/" 2>/dev/null || echo "000")
        if [[ "$NGINX_HTTP" == "200" || "$NGINX_HTTP" == "308" || "$NGINX_HTTP" == "301" || "$NGINX_HTTP" == "302" ]]; then
            success "Nginx responded with HTTP $NGINX_HTTP"
        else
            warn "Nginx returned HTTP $NGINX_HTTP (may be expected if nginx proxies differently)"
        fi
    fi

    if [[ "$TARGET" == "api" || "$TARGET" == "all" ]]; then
        info "Checking API healthz (port 8001)..."
        API_HTTP=$($SSH_CMD "curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://localhost:8001/healthz" 2>/dev/null || echo "000")
        if [[ "$API_HTTP" == "200" ]]; then
            success "API /healthz responded with HTTP $API_HTTP"
        else
            error "API /healthz returned HTTP $API_HTTP"
            ALL_OK=false
        fi

        info "Checking API readyz (port 8001)..."
        READYZ_HTTP=$($SSH_CMD "curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://localhost:8001/readyz" 2>/dev/null || echo "000")
        if [[ "$READYZ_HTTP" == "200" ]]; then
            success "API /readyz responded with HTTP $READYZ_HTTP"
        else
            warn "API /readyz returned HTTP $READYZ_HTTP (dependency may be down)"
        fi
    fi

    if [[ "$ALL_OK" != "true" ]]; then
        echo ""
        error "One or more health checks failed!"
        exit 1
    fi
}

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
DEPLOY_START=$(date +%s)

case "$TARGET" in
    web)
        deploy_web
        ;;
    api)
        deploy_api
        ;;
    all)
        deploy_web
        deploy_api
        ;;
esac

health_check

DEPLOY_END=$(date +%s)
ELAPSED=$((DEPLOY_END - DEPLOY_START))

echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║         Deploy completed (${ELAPSED}s)            ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════╝${NC}"
echo ""
info "Deployed: ${BOLD}$TARGET${NC}"
info "Server:   ${BOLD}91.98.142.165${NC}"
info "Duration: ${BOLD}${ELAPSED}s${NC}"
