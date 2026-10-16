# remote MCP origin — TLS edge and permanent certificate renewal

The public origin `https://mcp.agentnode.net` is nginx on the control host, terminating TLS on
**IPv4 443 only** and proxying to the loopback services (remote-MCP/OAuth on `127.0.0.1:8800`,
gateway login on `127.0.0.1:8099`). Nothing listens on port 80, nothing on IPv6.

| file | role |
|---|---|
| `nginx-main.conf` | `/etc/nginx/nginx.conf` — replaces the distro default (which listens on 80 and `[::]:80`); query strings are never logged |
| `nginx-mcp.conf` | `/etc/nginx/conf.d/` vhost template (`LISTEN_ADDR` → the public IPv4); unknown SNI is refused (`ssl_reject_handshake`) |
| `rmcp-cert-renew.sh` / `.service` / `.timer` | unattended ACME **DNS-01** renewal via lego's maintained All-Inkl/KAS provider, twice daily |
| `agentnode_sdk.remote_mcp.cert_check` + `rmcp-cert-check.service` / `.timer` | independent daily check of the certificate actually served |
| `rmcp-alert@.service` | `OnFailure=` target: crit journal entry + `/var/lib/agentnode-mcp/alerts/alerts.log` + optional hooks in `/etc/agentnode-mcp/alert.d/` |
| `cert.env.example` | non-secret settings → `/etc/agentnode-mcp/cert.env` |
| `cert-drill.sh` | forced-renewal mechanics drill against a local ACME test CA |

## Limits (decided table, Q-RMCP-0001 §6)

nginx: header block 16 KiB / 8 KiB per field, header read 10 s, body 256 KiB read within
15 s, upstream 135 s, and 10 requests/min/IP (burst 5) for requests carrying neither a bearer
credential nor a gateway session cookie (429 + `Retry-After`). The app enforces the rest
(`agentnode_sdk/remote_mcp/limits.py`): JSON depth 32 / 2,000 members / 64 KiB strings,
1 MiB results, discovery/list 60/min/account (burst 15), tools/call 10/min/account and
30/min/token (burst 3), 2 calls in flight per account, 8 running per process, a 32-deep queue
with a 5 s maximum wait, 125 s per call. The service runs as ONE process, so its caps are the
deployment caps; a second process needs the shared lease/semaphore first.

## How a renewal pass works

1. `lego run --renew-days 30` obtains the certificate (first run) or renews it when due. The
   `allinkl` provider sets **only** `_acme-challenge.mcp.agentnode.net` TXT through the KAS API,
   waits for propagation (checked against public resolvers), and removes the record again —
   also when the order fails. Up to `ATTEMPTS` tries with growing back-off.
2. A changed certificate is verified (not expired, SAN names the domain, key matches), written
   as a new release directory, and made live by **one `rename(2)` of the `live` symlink**.
3. `nginx -t`, then `systemctl reload nginx`, then the job confirms nginx **serves** the new
   fingerprint. Any failure after the swap renames `live` back to the previous release and
   reloads; the unit fails and raises an alert.
4. Only a verified install (or a verified "not due" run) writes the success stamp that the
   monitor checks.

**Secrets.** The KAS API login and password are root-only files
`/etc/agentnode-mcp/credentials/{kas_login,kas_password}` (0600). The unit passes them with
`LoadCredential=`; lego reads them via `ALL_INKL_LOGIN_FILE` / `ALL_INKL_PASSWORD_FILE`. They
never appear in argv, the environment, or logs. This subsystem does not assume the KAS login
can be restricted to DNS: use the narrowest KAS user the account offers, keep the credentials
on this host only, and rotate them on any suspicion.

## The monitor

`rmcp-cert-check` connects to the **public name** as a client would and alerts when the served
certificate does not name the domain, is not the installed one (fingerprint), has fewer than
21 days left, or when the renewal timer is not active / has not succeeded within 36 h. Run it
from this host's timer AND, for independence, from a second machine (same command, without
`--installed`/`--timer`).

## Owner prerequisites (not in this repository)

1. KAS API credentials → the two credential files above.
2. KAS DNS: `mcp` A `2.28.194.41`, no AAAA (see the founder runbook).
3. Hetzner firewall: inbound TCP 443 IPv4 `0.0.0.0/0` only; **no** port 80, no IPv6 rule.
4. Before launch: one renewal against Let's Encrypt **staging** (`ACME_SERVER` as in
   `cert.env.example`) with `RENEW_FORCE=1`, then switch `ACME_SERVER` to production.

## The drill (what it proves, and what it cannot)

`cert-drill.sh <sdk_dir> <drill_dir> <python>` installs the shipped units/config unchanged,
replaces only the DNS provider (lego `exec` → a local test DNS) and the ACME directory (pebble)
through a drop-in and the env file, and records each check in `drill-evidence.jsonl`: first
issuance through the unit, the challenge record removed afterwards, nginx serving the issued
certificate, a not-due run changing nothing, a forced renewal swapping and serving a new
certificate, an `nginx -t` failure rolling back to the previous release and alerting, monitor
alerts below the minimum validity and for a stopped timer, and no port-80 listener throughout.
It tears everything down at the end.

It does **not** exercise the All-Inkl KAS API or Let's Encrypt (both need the owner's KAS
credentials and public DNS); that is the staging step in the prerequisites above.
