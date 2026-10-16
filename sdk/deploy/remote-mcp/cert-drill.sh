#!/bin/bash
# cert-drill.sh -- forced-renewal MECHANICS drill for the R12 renewal subsystem, against a
# local ACME test CA (pebble) whose DNS-01 validation reads a local test DNS (pebble-challtestsrv).
#
# It drives the SHIPPED pieces unchanged -- rmcp-cert-renew.sh via rmcp-cert-renew.service,
# rmcp-cert-check (the monitor) via rmcp-cert-check.service, rmcp-alert@.service, nginx-main.conf
# and nginx-mcp.conf -- and only swaps, through a systemd drop-in and the env file, the parts that
# need the owner's KAS credentials: the DNS provider (lego `exec` -> test DNS instead of `allinkl`)
# and the ACME directory (pebble instead of Let's Encrypt). nginx listens on 127.0.0.1 only.
#
# Phases (each recorded as one JSON line in $DRILL/drill-evidence.jsonl):
#   A first issuance -> atomic install -> nginx serves it; challenge TXT cleaned up
#   B monitor OK          C not-due run changes nothing      D forced renewal swaps + serves
#   E nginx -t failure -> rollback to the previous release + alert
#   F monitor alerts: < min-days, and renewal timer not active
#   no port 80 listener at any point; full teardown at the end (also on failure).
#
# usage: cert-drill.sh <sdk_dir> <drill_dir> <python>   (bin/lego, bin/pebble, bin/pebble-challtestsrv)
set -uo pipefail
SDK=${1:?sdk dir}; DRILL=${2:?drill dir}; PY=${3:?python}
DEP="$SDK/deploy/remote-mcp"; BIN="$DRILL/bin"; EV="$DRILL/drill-evidence.jsonl"
TLS_DIR=/etc/agentnode-mcp/tls; STATE=/var/lib/agentnode-mcp/cert; ALERTS=/var/lib/agentnode-mcp/alerts/alerts.log
DOMAIN=mcp.agentnode.net
# nginx deliberately NOT on 127.0.0.1: the serve check must follow SERVE_CHECK_ADDR, not assume loopback
EDGE=127.0.0.2
: > "$EV"
ev() { "$PY" -c 'import json,sys,time; print(json.dumps({"t": round(time.time(),1), "phase": sys.argv[1], "check": sys.argv[2], "ok": sys.argv[3] == "1", "detail": sys.argv[4]}))' "$@" >> "$EV"; }
fp_file() { openssl x509 -in "$1" -noout -fingerprint -sha256 | cut -d= -f2 | tr -d : | tr 'A-F' 'a-f'; }
fp_served() { echo | openssl s_client -connect "$EDGE:443" -servername "$DOMAIN" 2>/dev/null | openssl x509 -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2 | tr -d : | tr 'A-F' 'a-f'; }
no80() { if ss -ltnH | awk '{print $4}' | grep -Eq '(^|:)80$'; then ev "$1" "no port 80 listener" 0 "$(ss -ltnH | grep ':80 ')"; else ev "$1" "no port 80 listener" 1 "none"; fi; }
txt_clean() { local t; t=$(dig +short @127.0.0.1 -p 8053 TXT "_acme-challenge.$DOMAIN"); [ -z "$t" ] && ev "$1" "challenge TXT removed after the run" 1 "empty" || ev "$1" "challenge TXT removed after the run" 0 "$t"; }
alerts_for() { local n; n=$(grep -c "$1" "$ALERTS" 2>/dev/null); echo "${n:-0}"; }
wait_alert() {  # OnFailure= runs the alert unit asynchronously: wait (bounded) for a new entry
  for _ in $(seq 1 30); do [ "$(alerts_for "$1")" -gt "$2" ] && return 0; sleep 1; done; return 1
}

teardown() {
  systemctl stop rmcp-cert-renew.timer rmcp-cert-check.timer rmcp-drill-pebble rmcp-drill-challtestsrv nginx 2>/dev/null
  systemctl disable rmcp-cert-renew.timer rmcp-cert-check.timer 2>/dev/null
  rm -rf /etc/systemd/system/rmcp-cert-renew.service.d /etc/systemd/system/rmcp-cert-check.service.d
  rm -f /etc/systemd/system/rmcp-cert-renew.{service,timer} /etc/systemd/system/rmcp-cert-check.{service,timer} /etc/systemd/system/rmcp-alert@.service
  rm -f /etc/nginx/conf.d/rmcp-mcp.conf
  [ -f /etc/nginx/nginx.conf.distro ] && mv -f /etc/nginx/nginx.conf.distro /etc/nginx/nginx.conf
  rm -rf /etc/agentnode-mcp /var/lib/agentnode-mcp
  systemctl reset-failed 'rmcp-*' 2>/dev/null
  systemctl stop 'system-rmcp\x2dalert.slice' 2>/dev/null
  systemctl daemon-reload
}
trap teardown EXIT

# ---- test CA for pebble's own ACME API, pebble + test DNS as transient units ----------------
mkdir -p "$DRILL/pebble" && cd "$DRILL/pebble" || exit 1
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 2 -subj /CN=drill-api-ca -keyout ca.key -out ca.pem 2>/dev/null
openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -subj /CN=localhost -keyout key.pem -out api.csr 2>/dev/null
printf 'subjectAltName=DNS:localhost,IP:127.0.0.1\n' > san.ext
openssl x509 -req -in api.csr -CA ca.pem -CAkey ca.key -CAcreateserial -days 2 -extfile san.ext -out cert.pem 2>/dev/null
cat > pebble.json <<EOF
{"pebble": {"listenAddress": "127.0.0.1:14000", "managementListenAddress": "127.0.0.1:15000",
 "certificate": "$DRILL/pebble/cert.pem", "privateKey": "$DRILL/pebble/key.pem",
 "httpPort": 5002, "tlsPort": 5001, "ocspResponderURL": "", "externalAccountBindingRequired": false}}
EOF
systemd-run --unit=rmcp-drill-challtestsrv --collect "$BIN/pebble-challtestsrv" -defaultIPv4 127.0.0.1 -defaultIPv6 "" \
  -dnsserver 127.0.0.1:8053 -http01 "" -https01 "" -tlsalpn01 "" -doh "" -management 127.0.0.1:8055 >/dev/null
systemd-run --unit=rmcp-drill-pebble --collect -E PEBBLE_VA_NOSLEEP=1 -E PEBBLE_WFE_NONCEREJECT=0 \
  "$BIN/pebble" -config "$DRILL/pebble/pebble.json" -dnsserver 127.0.0.1:8053 >/dev/null
for _ in $(seq 1 30); do curl -sf --cacert ca.pem https://127.0.0.1:14000/dir >/dev/null && break; sleep 1; done
curl -sf --cacert ca.pem https://127.0.0.1:15000/roots/0 > pebble-root.pem
[ -s pebble-root.pem ] && ev setup "pebble ACME + test DNS up" 1 "$("$BIN/pebble" -version 2>&1 | head -1)" || { ev setup "pebble ACME + test DNS up" 0 "no root"; exit 1; }

# lego exec hook: the test DNS's management API stands in for the KAS API
cat > "$DRILL/challtest-hook.sh" <<'EOF'
#!/bin/bash
# lego exec provider: $1=present|cleanup $2=fqdn $3=value
case "$1" in
  present) curl -sf -X POST -d "{\"host\":\"$2\",\"value\":\"$3\"}" http://127.0.0.1:8055/set-txt ;;
  cleanup) curl -sf -X POST -d "{\"host\":\"$2\"}" http://127.0.0.1:8055/clear-txt ;;
esac
echo "$(date -u +%FT%TZ) $1 $2" >> "$(dirname "$0")/hook.log"
EOF
chmod 0755 "$DRILL/challtest-hook.sh"

# a nginx stand-in whose `-t` fails, for the rollback phase only
cat > "$DRILL/nginx-t-fails" <<'EOF'
#!/bin/bash
[ "${1:-}" = "-t" ] && { echo "drill: simulated nginx -t failure" >&2; exit 1; }
exec nginx "$@"
EOF
chmod 0755 "$DRILL/nginx-t-fails"

# ---- install the shipped config/units -------------------------------------------------------
mkdir -p /etc/agentnode-mcp "$TLS_DIR/releases/bootstrap" "$STATE"
chmod 0700 /etc/agentnode-mcp
cat > /etc/agentnode-mcp/cert.env <<EOF
DOMAIN=$DOMAIN
EMAIL=drill@agentnode.net
ACME_SERVER=https://127.0.0.1:14000/dir
DNS_PROVIDER=exec
DNS_RESOLVERS=127.0.0.1:8053
LEGO_BIN=$BIN/lego
LEGO_PATH=$DRILL/lego
TLS_DIR=$TLS_DIR
SERVE_CHECK_ADDR=$EDGE
STATE_DIR=$STATE
RENEW_DAYS=30
ATTEMPTS=2
RETRY_BASE_S=2
KEEP_RELEASES=5
export EXEC_PATH=$DRILL/challtest-hook.sh LEGO_CA_CERTIFICATES=$DRILL/pebble/ca.pem LEGO_DNS_PROPAGATION_WAIT=2s
EOF
chmod 0600 /etc/agentnode-mcp/cert.env
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 1 -subj "/CN=bootstrap" \
  -addext "subjectAltName=DNS:$DOMAIN" -keyout "$TLS_DIR/releases/bootstrap/privkey.pem" \
  -out "$TLS_DIR/releases/bootstrap/fullchain.pem" 2>/dev/null
ln -sfn "$TLS_DIR/releases/bootstrap" "$TLS_DIR/live"
[ -f /etc/nginx/nginx.conf.distro ] || cp -p /etc/nginx/nginx.conf /etc/nginx/nginx.conf.distro
install -m 0644 "$DEP/nginx-main.conf" /etc/nginx/nginx.conf
sed "s/LISTEN_ADDR/$EDGE/g" "$DEP/nginx-mcp.conf" > /etc/nginx/conf.d/rmcp-mcp.conf
nginx -t 2>/dev/null && systemctl restart nginx && ev setup "nginx (shipped config, $EDGE:443, bootstrap cert)" 1 "$(nginx -v 2>&1)" \
  || { ev setup "nginx up" 0 "$(nginx -t 2>&1 | tail -2)"; exit 1; }
for u in rmcp-cert-renew.service rmcp-cert-renew.timer rmcp-cert-check.service rmcp-cert-check.timer rmcp-alert@.service; do
  install -m 0644 "$DEP/$u" /etc/systemd/system/
done
mkdir -p /etc/systemd/system/rmcp-cert-renew.service.d /etc/systemd/system/rmcp-cert-check.service.d
cat > /etc/systemd/system/rmcp-cert-renew.service.d/drill.conf <<EOF
[Service]
# drill: no KAS credentials (exec provider -> test DNS); the shipped script at its deployed path.
# The test stand lives under /root, which the shipped ProtectHome=yes hides (prod: /opt/agentnode).
LoadCredential=
ProtectHome=no
ExecStart=
ExecStart=$DEP/rmcp-cert-renew.sh
EOF
check_dropin() {  # $1 = min days
  cat > /etc/systemd/system/rmcp-cert-check.service.d/drill.conf <<EOF
[Service]
Environment=PYTHONPATH=$SDK
ProtectHome=no
ExecStart=
ExecStart=$PY -m agentnode_sdk.remote_mcp.cert_check --domain $DOMAIN --connect $EDGE --issuer-contains Pebble --cafile $DRILL/pebble/pebble-root.pem --min-days $1 --installed $TLS_DIR/live/fullchain.pem --timer rmcp-cert-renew.timer --stamp $STATE/last-success --max-renew-age-hours 36
EOF
  systemctl daemon-reload
}
check_dropin 21
systemctl enable --now rmcp-cert-renew.timer rmcp-cert-check.timer >/dev/null 2>&1
no80 setup

# ---- A: first issuance through the unit -----------------------------------------------------
systemctl start rmcp-cert-renew.service; rc=$?
ISSUED="$DRILL/lego/certificates/$DOMAIN.crt"
A_FP=$(fp_file "$TLS_DIR/live/fullchain.pem"); SERVED=$(fp_served)
ev A "renewal unit succeeded (first issuance)" $([ $rc = 0 ] && echo 1 || echo 0) "rc=$rc"
ev A "live points at a new release, not bootstrap" $([ "$(readlink "$TLS_DIR/live")" != "$TLS_DIR/releases/bootstrap" ] && echo 1 || echo 0) "$(readlink "$TLS_DIR/live")"
ev A "nginx serves the issued certificate" $([ -n "$SERVED" ] && [ "$SERVED" = "$A_FP" ] && [ "$A_FP" = "$(fp_file "$ISSUED")" ] && echo 1 || echo 0) "served=${SERVED:0:16} live=${A_FP:0:16}"
ev A "issuer is the ACME CA (pebble)" $(openssl x509 -in "$TLS_DIR/live/fullchain.pem" -noout -issuer | grep -qi pebble && echo 1 || echo 0) "$(openssl x509 -in "$TLS_DIR/live/fullchain.pem" -noout -issuer)"
ev A "dns-01 hook presented and cleaned up" $(grep -q present "$DRILL/hook.log" && grep -q cleanup "$DRILL/hook.log" && echo 1 || echo 0) "$(tr '\n' ';' < "$DRILL/hook.log")"
txt_clean A
ev A "private key installed 0600" $([ "$(stat -c %a "$TLS_DIR/live/privkey.pem")" = 600 ] && echo 1 || echo 0) "$(stat -c %a "$TLS_DIR/live/privkey.pem")"
no80 A

# ---- B: monitor OK --------------------------------------------------------------------------
systemctl start rmcp-cert-check.service; rc=$?
ev B "monitor passes on a healthy origin" $([ $rc = 0 ] && echo 1 || echo 0) "$(journalctl -u rmcp-cert-check.service -n 3 --no-pager -o cat | tr '\n' ';')"

# ---- C: not due -> nothing changes ----------------------------------------------------------
before_stamp=$(cat "$STATE/last-success"); sleep 1
systemctl start rmcp-cert-renew.service; rc=$?
ev C "not-due run succeeds without changing the live certificate" $([ $rc = 0 ] && [ "$(fp_file "$TLS_DIR/live/fullchain.pem")" = "$A_FP" ] && echo 1 || echo 0) "rc=$rc"
ev C "success stamp refreshed" $([ "$(cat "$STATE/last-success")" != "$before_stamp" ] && echo 1 || echo 0) "$(cat "$STATE/last-success")"

# ---- D: forced renewal ----------------------------------------------------------------------
echo "RENEW_FORCE=1" >> /etc/agentnode-mcp/cert.env
systemctl start rmcp-cert-renew.service; rc=$?
D_FP=$(fp_file "$TLS_DIR/live/fullchain.pem"); SERVED=$(fp_served)
ev D "forced renewal through the unit succeeded" $([ $rc = 0 ] && echo 1 || echo 0) "rc=$rc"
ev D "a NEW certificate is live and served" $([ "$D_FP" != "$A_FP" ] && [ "$SERVED" = "$D_FP" ] && echo 1 || echo 0) "old=${A_FP:0:16} new=${D_FP:0:16} served=${SERVED:0:16}"
txt_clean D
no80 D

# ---- E: nginx -t failure after the swap -> rollback + alert -----------------------------------
a0=$(alerts_for rmcp-cert-renew.service)
echo "NGINX_BIN=$DRILL/nginx-t-fails" >> /etc/agentnode-mcp/cert.env
systemctl start rmcp-cert-renew.service; rc=$?
sleep 2
E_FP=$(fp_file "$TLS_DIR/live/fullchain.pem"); SERVED=$(fp_served)
ev E "renewal unit FAILS when nginx rejects the config" $([ $rc != 0 ] && echo 1 || echo 0) "rc=$rc"
ev E "live rolled back to the previous release and still served" $([ "$E_FP" = "$D_FP" ] && [ "$SERVED" = "$D_FP" ] && echo 1 || echo 0) "live=${E_FP:0:16} served=${SERVED:0:16} expected=${D_FP:0:16}"
ev E "rollback logged" $(journalctl -u rmcp-cert-renew.service -n 30 --no-pager -o cat | grep -q ROLLBACK && echo 1 || echo 0) "$(journalctl -u rmcp-cert-renew.service -n 30 --no-pager -o cat | grep -E 'ROLLBACK|FAILED' | tr '\n' ';')"
ev E "alert raised for the failed renewal" $(wait_alert rmcp-cert-renew.service "$a0" && echo 1 || echo 0) "$(tail -1 "$ALERTS" 2>/dev/null)"
sed -i '/^NGINX_BIN=/d;/^RENEW_FORCE=/d' /etc/agentnode-mcp/cert.env
systemctl reset-failed rmcp-cert-renew.service

# ---- G: the serve check is load-bearing: pointed at an address nginx does not serve, a correct
#         install is "not served" -> rollback + alert (this is the check that catches a
#         SERVE_CHECK_ADDR / LISTEN_ADDR mismatch before it can strand a certificate) ----------
a0=$(alerts_for rmcp-cert-renew.service)
printf 'RENEW_FORCE=1\nSERVE_CHECK_ADDR=127.0.0.3\n' >> /etc/agentnode-mcp/cert.env
systemctl start rmcp-cert-renew.service; rc=$?
G_FP=$(fp_file "$TLS_DIR/live/fullchain.pem"); SERVED=$(fp_served)
ev G "renewal FAILS when nothing serves the new certificate at SERVE_CHECK_ADDR" $([ $rc != 0 ] && echo 1 || echo 0) "rc=$rc"
ev G "live rolled back; the previous certificate is still served" $([ "$G_FP" = "$D_FP" ] && [ "$SERVED" = "$D_FP" ] && echo 1 || echo 0) "live=${G_FP:0:16} served=${SERVED:0:16}"
ev G "alert raised" $(wait_alert rmcp-cert-renew.service "$a0" && echo 1 || echo 0) "$(tail -1 "$ALERTS" 2>/dev/null)"
sed -i '/^RENEW_FORCE=/d;/^SERVE_CHECK_ADDR=127.0.0.3/d' /etc/agentnode-mcp/cert.env
systemctl reset-failed rmcp-cert-renew.service

# ---- F: monitor alerts ----------------------------------------------------------------------
a0=$(alerts_for rmcp-cert-check.service)
check_dropin 100000                                   # served cert has fewer days than that
systemctl start rmcp-cert-check.service; rc=$?
sleep 1
ev F "monitor FAILS below min-days" $([ $rc != 0 ] && echo 1 || echo 0) "$(journalctl -u rmcp-cert-check.service -n 15 --no-pager -o cat | grep ALERT | tr '\n' ';')"
ev F "alert raised for the low-validity check" $(wait_alert rmcp-cert-check.service "$a0" && echo 1 || echo 0) "$(tail -1 "$ALERTS" 2>/dev/null)"
systemctl reset-failed rmcp-cert-check.service
check_dropin 21
systemctl stop rmcp-cert-renew.timer
a0=$(alerts_for rmcp-cert-check.service)
systemctl start rmcp-cert-check.service; rc=$?
sleep 1
ev F "monitor FAILS when the renewal timer is not active" $([ $rc != 0 ] && echo 1 || echo 0) "$(journalctl -u rmcp-cert-check.service -n 15 --no-pager -o cat | grep ALERT | tr '\n' ';')"
ev F "alert raised for the stopped timer" $(wait_alert rmcp-cert-check.service "$a0" && echo 1 || echo 0) "$(tail -1 "$ALERTS" 2>/dev/null)"
systemctl start rmcp-cert-renew.timer
no80 F
cp "$ALERTS" "$DRILL/alerts.log" 2>/dev/null

# ---- teardown (also the EXIT trap) + proof ----------------------------------------------------
trap - EXIT
teardown
left=$(systemctl list-units --all --no-legend --plain 'rmcp-cert*' 'rmcp-drill*' 'rmcp-alert*' 2>/dev/null | wc -l)
ev teardown "drill units gone" $([ "$left" = 0 ] && echo 1 || echo 0) "$left"
ev teardown "no 443/14000/15000/8053/8055 listeners" $(ss -ltnH | awk '{print $4}' | grep -Eq ':(443|14000|15000|8053|8055)$' && echo 0 || echo 1) "$(ss -ltnH | awk '{print $4}' | tr '\n' ' ')"
ev teardown "/etc/agentnode-mcp and /var/lib/agentnode-mcp removed; distro nginx.conf restored" $([ ! -e /etc/agentnode-mcp ] && [ ! -e /var/lib/agentnode-mcp ] && grep -q 'listen       80' /etc/nginx/nginx.conf && echo 1 || echo 0) ""
no80 teardown
"$PY" -c 'import json,sys; r=[json.loads(l) for l in open(sys.argv[1])]; bad=[x for x in r if not x["ok"]]; print("DRILL %d/%d checks ok" % (len(r)-len(bad), len(r))); [print("  FAIL", x["phase"], x["check"], x["detail"]) for x in bad]; sys.exit(1 if bad else 0)' "$EV"
