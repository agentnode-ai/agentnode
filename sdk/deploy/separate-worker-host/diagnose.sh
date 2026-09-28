#!/usr/bin/env bash
# What this host can see about itself and about the other one. Read-only, and it prints no
# secret.
#
# The rule it follows, everywhere: a key appears as the pair it belongs to and a generation; a
# certificate appears as the identity URI on it, which is a name; a refusal appears as its stable
# cause. Nothing here cats a key file, and nothing here prints an environment.
#
# Run it on either host. It works out which role it is on from what is installed.

set -uo pipefail

PREFIX=/opt/agentnode
CONF=/etc/agentnode
TRUST=/etc/agentnode/trust

head() { printf '\n== %s\n' "$*"; }
line() { printf '   %s\n' "$*"; }

ROLE=""
systemctl list-unit-files agentnode-worker.service  --no-legend 2>/dev/null | grep -q . && ROLE=worker
systemctl list-unit-files agentnode-gateway.service --no-legend 2>/dev/null | grep -q . && ROLE="${ROLE:+both }gateway"
[ -n "$ROLE" ] || { printf 'Neither service is installed on this machine.\n'; exit 2; }

head "this host"
line "role       : $ROLE"
line "hostname   : $(hostname)"
line "build      : $("$PREFIX/venv/bin/agentnode" --version 2>/dev/null || echo 'not installed')"
line "boot id    : $(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo 'none -- every restart looks like a reboot')"
if [ "$ROLE" = "both" ]; then
  line "BOTH ROLES ARE INSTALLED HERE. That is single-host-development, not two machines."
fi

head "the services"
for unit in agentnode-worker agentnode-gateway agentnode-pki-tick.timer; do
  systemctl list-unit-files "$unit"* --no-legend >/dev/null 2>&1 || continue
  state="$(systemctl is-active "$unit" 2>/dev/null || true)"
  since="$(systemctl show "$unit" -p ActiveEnterTimestamp --value 2>/dev/null)"
  [ -n "$state" ] && line "$(printf '%-26s %-10s %s' "$unit" "$state" "$since")"
done

head "the two signed lists, and whether they are fresh enough to be believed"
# Both expire. A list too old to believe is a REFUSAL rather than an empty list, so an expired
# one here is not cosmetic: it stops the door.
for name in ca.pem revoked.crl revoked-identities.json; do
  if [ -f "$TRUST/$name" ]; then
    line "$(printf '%-26s %s  age %s' "$name" "$(stat -c '%A %U:%G' "$TRUST/$name")" \
        "$(( ( $(date +%s) - $(stat -c %Y "$TRUST/$name") ) / 60 )) min")"
  else
    line "$(printf '%-26s MISSING -- the door that needs it will refuse every caller' "$name")"
  fi
done

head "the time floor"
# Keyed to THIS kernel's boot id and THIS machine's monotonic clock. A floor copied from the
# other host is unusable here, and this is the step most often forgotten.
"$PREFIX/venv/bin/agentnode" pki floor show 2>&1 | sed 's/^/   /' || \
  line "could not be read. Run: sudo agentnode pki tick   -- ON THIS HOST."

if [ "$ROLE" = "worker" ] || [ "$ROLE" = "both gateway" ]; then
  head "this worker's own certificate, and its configuration"
  # `preflight` answers the question that matters -- would a control plane dialling now accept
  # this worker -- and answers it without opening anything.
  if [ -f "$CONF/worker.env" ]; then
    set -a; . "$CONF/worker.env"; set +a
    runuser -u agentnode-worker -- "$PREFIX/venv/bin/agentnode" worker preflight \
        --topology separate-worker-host --listen "${AGENTNODE_LISTEN:-}" \
        --keyring "$CONF/pair-keys.json" \
        --journal /var/lib/agentnode-worker/journal \
        --tls-dir /var/lib/agentnode-worker/tls \
        --trust "$TRUST/ca.pem" \
        --deployment "${AGENTNODE_DEPLOYMENT:-}" \
        --accept-gateway "${AGENTNODE_GATEWAY_INSTANCE:-}" \
        --revocation-list "$TRUST/revoked.crl" \
        --tombstones "$TRUST/revoked-identities.json" \
        --floor /var/lib/agentnode-floor/worker.floor 2>&1 | sed 's/^/ /'
  else
    line "no $CONF/worker.env, so this worker has never been installed by the script"
  fi

  head "what is listening, and on which interface"
  # The one that matters: a worker bound to a public address is an open door with a certificate
  # on it. The product refuses a wildcard bind; this shows what actually happened.
  ss -tlnp 2>/dev/null | grep -E 'agentnode|:8443' | sed 's/^/   /' || line "nothing"

  head "things that should not be here"
  for forbidden in /var/lib/agentnode /etc/agentnode/ca; do
    [ -e "$forbidden" ] && line "FINDING: $forbidden exists on a worker host -- that is control-plane state"
  done
  line "(nothing else to report)"
fi

if [ "$ROLE" = "gateway" ] || [ "$ROLE" = "both gateway" ]; then
  head "the control plane's view of its worker"
  runuser -u agentnode-gateway -- "$PREFIX/venv/bin/agentnode" gateway doctor \
      --dir /var/lib/agentnode/state 2>&1 | sed 's/^/ /'

  head "things that should not be here"
  for runtime in docker podman; do
    command -v "$runtime" >/dev/null 2>&1 && \
      line "FINDING: $runtime is installed on the control plane. Foreign code is supposed to run
   on the other machine; a runtime here is how that stops being true."
  done
  line "(nothing else to report)"
fi

head "what this cannot tell you"
line "Whether the two hosts are really two hosts. Everything above was read on THIS machine,"
line "and a machine cannot establish its own isolation. That is measured from outside, on two"
line "real kernels, and in this deployment it has NOT been measured."
printf '\n'
