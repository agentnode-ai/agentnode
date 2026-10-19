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

## The per-account catalog

What each account can list and call is one signed document, `stand/catalog/catalog.json`
(`agentnode_sdk/remote_mcp/catalog.py`), changed only by the operator through
`python -m agentnode_sdk.remote_mcp.catalog_cli --stand <stand> …` (init, publish, unpublish,
set-default, set-account, rollback, show, heads). No MCP tool can change it.

* Publishing pins the exact imported bytes (capability id, version, manifest, artifact and tree
  digests, export) of a capability signed by the stand operator key; network or secret
  capabilities are refused. The public tool name is `<capability-name>__<export>`; the
  `agentnode_` prefix is reserved for `agentnode_search`, `agentnode_describe` and `agentnode_run`.
* An account sees its explicit list if it has one (an explicit empty list stays empty), else the
  default list. All tools share the account, token and process limits above; search and describe
  count against the list budget, every run (direct or `agentnode_run`) against the call budget.
* Every change writes a new, higher generation (rollback too: it copies old contents into a new
  generation). `listChanged` is false and list results are private with a zero TTL: a client
  sees a change on its **next request**; a cursor from an older generation is refused.
* Revocation takes effect at once for list, search and new or queued calls; a call admitted to
  the worker before the revocation committed finishes and is audited.
* A catalog that is missing, unreadable, unsigned, edited or older than one already seen is
  refused with 503; there is no stale fallback. Every catalog decision and operator change is
  recorded in the signed, hash-chained `stand/catalog-audit.jsonl` (digests, never raw
  arguments or queries). Like every hash chain without an external head, it detects edits and
  reordering but not truncation of its tail; `catalog_cli heads` prints the heads to record.
* Approvals are bound to the account, the exact catalog entry and the arguments; approve with the
  command a call returns (it names `--audience <capability id>`).

## Execution targets (managed and customer workers)

Where an authorized call runs is a second signed document, `stand/targets/targets.json`
(`remote_mcp/targets.py`). It is hash-linked history with a pinned head, changed only through
`python -m agentnode_sdk.remote_mcp.targets_cli --stand <stand> …`. A missing or untrustworthy
register refuses every run with 503.

* **Visibility and choice.** Every account sees the managed target(s) and only its OWN customer
  targets. A call runs on its wish if it names one of those: `_meta["net.agentnode/target"]`, or
  the `target` argument of `agentnode_run`. Otherwise it runs on the account's default, otherwise
  on the managed default. Unknown and foreign targets answer the same `target_unknown`.
  `agentnode_describe` shows the account's own targets with eligibility, never an address or
  identity.
* **No fallback.** An offline, full, suspended, expired, revoked or unsuitable target is a
  structured refusal. Nothing is ever retargeted, to managed or anywhere else.
* **Binding.** The approval binds the target: its id, the worker identity URI, the endpoint,
  the requirements, the properties, the assurance class and the generation those facts last
  changed in. A different target needs a new approval. At admission the account's live
  authority, the gateway kill switch and the exact target binding are judged again under the
  catalog's lock. A refusal there does not consume the approval.
* **Exactly once.** A run's id is derived from the invocation hash and the approval. The worker's
  journal recognises a repeated start. A lost connection is reconciled from that journal on the
  same target. An outcome that cannot be established is `indeterminate`, and blocks the same
  invocation until `targets_cli reconcile-dispatch` settles it.
* **Assurance, stated honestly.** A target is eligible only with an operator-signed observation
  record from the control plane's own probes: an egress canary with a positive control, memory
  and wall-clock limits with within-limit controls, and the proved identity. The worker's own
  `describe` decides nothing.
  - `operator_managed_measured` covers the operator's own worker, valid 7 days.
  - `customer_controlled_observed` covers a customer's host, valid 24 hours: behaviour the control
    plane observed, not proof of a kernel, runtime, isolation backend or of network isolation
    beyond the tested destinations. The canary's positive control runs on the control plane, so
    the operator separately checks that the worker host's network can reach the canary address;
    otherwise "not seen" means nothing.
  - A catalog entry runs only on the assurance classes it accepts (`catalog_cli set-assurance`).
* **Lifecycle.** The states are `invite → enroll → probe → active ↔ suspended`, and finally
  `revoke` or expiry. Every operation is journaled before its first side effect and resumable
  (`resume-ops`). A revoked or expired target is never reactivated: its identity is withdrawn
  for good in the issuer, and a replacement takes a new instance name.
* **Trust distribution.** `rmcp-trust-refresh.timer` runs at 00, 06, 12 and 18 UTC
  (`OnCalendar`, `Persistent=true`: a missed run is made up right after boot or install; there is
  no monotonic trigger, which is what left the first version with no next run after a reinstall,
  DEFECT-LIVE-001). One run at a time (`stand/targets/.trust-refresh.lock`; a run that cannot get
  it fails as `trust_refresh.busy`). It publishes the revocation and tombstone lists when they are
  due, offers ONE pair to every live target over the worker channel (`install_trust`), and records
  an acknowledgement only when the worker's answer -- computed from the files it read back after
  installing them -- is exactly that pair: list number, both SHA-256 digests, `nextUpdate`, and the
  tombstones' `published_at`/`not_after`. It retires pair-key overlaps after 24 h and fails into
  `rmcp-alert@` when a list is within 48 h of expiry or a target lags. A target runs only while its
  acknowledgement is exactly the control plane's own current pair.
* **Trust watchdog.** `rmcp-trust-watchdog.timer` (hourly at :20 UTC) runs
  `trust_watchdog check`, which shares no unit dependency with the refresh: both lists valid and
  signed at the effective time with ≥ 48 h left, every live target's exact acknowledgement at most
  13 h old, and `rmcp-trust-refresh.timer` loaded, enabled, active with a finite next run within
  6 h 10 min. Red = the unit fails (→ `rmcp-alert@`, again every hour while red) and
  `/var/lib/agentnode-mcp/alerts/trust-watchdog.json` says why. The daily `rmcp-cert-check`
  watches the watchdog's own timer through the drop-in `rmcp-cert-check.service.d/trust-watchdog.conf`.
* **Installing and removing the timers.** Only with `trust-timers.sh` (control host, root):
  `install <release-dir>` validates the units, installs them and the drop-in, enables and restarts
  both timers and FAILS unless both have a finite next run within their bound; `check` repeats that
  judgement; `remove` (before a rollback to a release without them, using the release that
  installed them: `TRF_SDK=<release>/sdk <release>/sdk/deploy/remote-mcp/trust-timers.sh remove`)
  deletes units, drop-in, both stamp files and the watchdog state and proves them gone. Never
  `systemctl start` the refresh to make a dead timer look alive.
* **Reachability.** The control plane dials the target over the private network; there is no
  public worker port. A worker behind NAT that connects out is a different protocol and not part
  of this.
* **Customer workers** are installed in two steps on the worker host, with the release that host
  already runs:
  1. `sdk/deploy/customer-worker/install-customer-worker.sh` sets up the worker's own account, rootless
     podman, state, units and nft rule, and makes its key and request there.
  2. `targets_cli enroll` issues the certificate on the control plane.
  3. `activate-customer-worker.sh` installs the certificate, sets up the root-owned time floor and its
     timer, and starts the worker.

  They are removed with `remove-customer-worker.sh`. Several on one worker host are separate
  services, not separate machines.

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
