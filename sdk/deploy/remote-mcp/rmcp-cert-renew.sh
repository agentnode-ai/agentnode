#!/bin/bash
# rmcp-cert-renew -- unattended ACME DNS-01 renewal for the remote MCP origin (profile R12).
#
# Run by rmcp-cert-renew.timer, never by hand in production. One pass:
#   1. obtain (first run) or renew-if-due the certificate with lego over DNS-01. The DNS
#      provider is lego's maintained All-Inkl/KAS provider ("allinkl"); it creates only the
#      _acme-challenge TXT record, waits for propagation, and removes it again (also on failure).
#      No port 80 is ever opened.
#   2. if the certificate changed: verify it (key matches, SAN names the domain, not expired),
#      install it ATOMICALLY as a new release behind the `live` symlink, validate nginx
#      (`nginx -t`), reload nginx, and confirm nginx now SERVES the new certificate.
#      Any failure after the swap rolls the symlink back to the previous release and reloads.
#   3. record the success stamp the independent monitor reads.
#
# Secrets: the KAS login/password are systemd credentials (LoadCredential=, root-only files),
# handed to lego as *_FILE paths -- never on argv, never in the environment, never logged.
# Settings (non-secret) come from /etc/agentnode-mcp/cert.env (see cert.env.example).
set -euo pipefail
umask 077

CONF=${RMCP_CERT_CONF:-/etc/agentnode-mcp/cert.env}
# shellcheck disable=SC1090
. "$CONF"
: "${DOMAIN:?}" "${EMAIL:?}" "${ACME_SERVER:?}" "${LEGO_BIN:?}" "${LEGO_PATH:?}" "${TLS_DIR:?}"
# the address nginx actually listens on (nginx-mcp.conf LISTEN_ADDR): the post-install check
# must connect THERE, or a correct install would be "not served" and rolled back. No default.
: "${SERVE_CHECK_ADDR:?SERVE_CHECK_ADDR must equal nginx LISTEN_ADDR}"
: "${DNS_PROVIDER:=allinkl}" "${RENEW_DAYS:=30}" "${ATTEMPTS:=3}" "${KEEP_RELEASES:=5}"
: "${STATE_DIR:=/var/lib/agentnode-mcp/cert}" "${NGINX_BIN:=nginx}"
: "${PYTHON:=python3}"

log() { echo "rmcp-cert-renew: $*"; }
die() { log "FAILED: $*"; exit 1; }

exec 9>"/run/rmcp-cert-renew.lock"
flock -n 9 || die "another renewal is running"
mkdir -p "$STATE_DIR" "$TLS_DIR/releases"

# --- credentials: only as files, only for the KAS provider --------------------------------
if [ "$DNS_PROVIDER" = "allinkl" ]; then
  CRED=${CREDENTIALS_DIRECTORY:?KAS credentials must come from systemd LoadCredential=}
  [ -s "$CRED/kas_login" ] && [ -s "$CRED/kas_password" ] || die "KAS credentials missing"
  export ALL_INKL_LOGIN_FILE="$CRED/kas_login" ALL_INKL_PASSWORD_FILE="$CRED/kas_password"
  export ALL_INKL_PROPAGATION_TIMEOUT=${ALL_INKL_PROPAGATION_TIMEOUT:-600}
  export ALL_INKL_POLLING_INTERVAL=${ALL_INKL_POLLING_INTERVAL:-10}
fi

# lego v5: `run` both obtains and renews-when-due (--renew-days); the timer already randomizes,
# so lego's own random pre-renewal sleep is off. RENEW_FORCE=1 forces a renewal (drills only).
LEGO=("$LEGO_BIN" --log.format text run --accept-tos --email "$EMAIL" --server "$ACME_SERVER"
      --path "$LEGO_PATH" --dns "$DNS_PROVIDER" --domains "$DOMAIN" --key-type EC256
      --renew-days "$RENEW_DAYS" --no-random-sleep)
if [ -n "${DNS_RESOLVERS:-}" ]; then LEGO+=(--dns.resolvers "$DNS_RESOLVERS"); fi
if [ "${RENEW_FORCE:-0}" = 1 ]; then LEGO+=(--renew-force); fi
ISSUED="$LEGO_PATH/certificates/$DOMAIN.crt"
ISSUED_KEY="$LEGO_PATH/certificates/$DOMAIN.key"

leaf_fp() {  # sha256 of the first certificate in a PEM file, or empty
  [ -s "$1" ] && openssl x509 -in "$1" -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2 | tr -d : | tr 'A-F' 'a-f' || true
}

before=$(leaf_fp "$ISSUED")
ok=0
for attempt in $(seq 1 "$ATTEMPTS"); do
  if "${LEGO[@]}"; then ok=1; break; fi
  log "attempt $attempt failed; retrying in $((attempt * ${RETRY_BASE_S:-60}))s"
  sleep $((attempt * ${RETRY_BASE_S:-60}))
done
[ "$ok" = 1 ] || die "ACME order failed after $ATTEMPTS attempts"
after=$(leaf_fp "$ISSUED")
installed=$(leaf_fp "$TLS_DIR/live/fullchain.pem")

if [ "$after" = "$installed" ] && [ -n "$installed" ]; then
  log "not due; installed certificate $installed is current"
  date +%s > "$STATE_DIR/last-success.tmp" && mv -f "$STATE_DIR/last-success.tmp" "$STATE_DIR/last-success"
  exit 0
fi
log "new certificate ${after:0:16} (was ${before:0:16}); verifying before install"

# --- verify the new material before it can be served ----------------------------------------
openssl x509 -in "$ISSUED" -noout -checkend 0 >/dev/null || die "new certificate is already expired"
openssl x509 -in "$ISSUED" -noout -ext subjectAltName | grep -q "DNS:$DOMAIN" || die "new certificate lacks SAN $DOMAIN"
[ "$(openssl x509 -in "$ISSUED" -noout -pubkey | openssl sha256)" = \
  "$(openssl pkey -in "$ISSUED_KEY" -pubout | openssl sha256)" ] || die "new key does not match the certificate"

# --- atomic install: a new release directory, then one rename(2) of the `live` symlink --------
rel="$TLS_DIR/releases/$(date -u +%Y%m%dT%H%M%SZ)-${after:0:12}"
mkdir "$rel"
install -m 0644 "$ISSUED" "$rel/fullchain.pem"
install -m 0600 "$ISSUED_KEY" "$rel/privkey.pem"
sync
prev=$(readlink "$TLS_DIR/live" 2>/dev/null || true)
swap_to() { ln -sfn "$1" "$TLS_DIR/live.next" && mv -T "$TLS_DIR/live.next" "$TLS_DIR/live"; }
served_fp() {
  echo | openssl s_client -connect "$SERVE_CHECK_ADDR:443" -servername "$DOMAIN" 2>/dev/null \
    | openssl x509 -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2 | tr -d : | tr 'A-F' 'a-f' || true
}
wait_served() {  # a reload is asynchronous for nginx workers: wait (bounded) for $1 to be served
  local fp=""
  for _ in $(seq 1 15); do fp=$(served_fp); [ "$fp" = "$1" ] && break; sleep 1; done
  echo "$fp"
}
rollback() {
  log "ROLLBACK: $1"
  if [ -n "$prev" ]; then
    swap_to "$prev"
    if "$NGINX_BIN" -t && systemctl reload nginx; then
      want=$(leaf_fp "$prev/fullchain.pem")
      got=$(wait_served "$want")
      if [ "$got" = "$want" ]; then log "rollback verified: serving ${want:0:16} again"
      else log "rollback NOT verified: serving '${got:0:16}', expected ${want:0:16}"; fi
    else
      log "rollback reload failed: nginx needs an operator"
    fi
  fi
  die "$1 (rolled back to ${prev:-nothing})"
}
swap_to "$rel"
"$NGINX_BIN" -t 2>&1 || rollback "nginx -t rejected the configuration with the new certificate"
systemctl reload nginx || rollback "nginx reload failed"

# --- confirm nginx actually serves it ---------------------------------------------------------
served=$(wait_served "$after")
[ "$served" = "$after" ] || rollback "nginx serves ${served:0:16}, not the new ${after:0:16}"

# --- keep a bounded history of releases (never the live or previous one) ---------------------
ls -1dt "$TLS_DIR"/releases/*/ 2>/dev/null | tail -n +"$((KEEP_RELEASES + 1))" | while read -r old; do
  old=${old%/}
  [ "$old" = "$rel" ] || [ "$old" = "$prev" ] || rm -rf -- "$old"
done
date +%s > "$STATE_DIR/last-success.tmp" && mv -f "$STATE_DIR/last-success.tmp" "$STATE_DIR/last-success"
log "installed and serving ${after:0:16}"
