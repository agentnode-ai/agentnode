# Local worker, slice 1 — a worker behind NAT that dials its control plane

**Status: private, off by default.** Nothing in this slice is deployed, routed publicly, installed for a
customer or released. The path exists only where a stand's `worker.json` names `reverse_listen`; without it no
socket is opened and nothing below is reachable (`remote_mcp/server.attach_reverse_hub` returns `None`).

Decision: Codex Q-LW-0001, profile `local-worker-slice1-r1` (criteria C1–C20), resolutions R1–R13 and
DECISION-0002 (the agent applies each job's own limits).

## 1. Architecture

```
   customer machine (behind NAT, no inbound port)            control plane (private network only in slice 1)
  ┌────────────────────────────────────────────┐            ┌──────────────────────────────────────────────┐
  │ agentnode worker connect                   │  TCP out   │ ReverseHub   tcps://<private ip>:<port>       │
  │   DialBackAgent: P = concurrency + 4 slots ─┼──────────▶ │   TLS CLIENT, peer must be role WORKER of     │
  │   each slot: TLS SERVER (worker cert),     │            │   this deployment AND a dial-back target that │
  │   one sealed request, one answer, close    │ ◀──────────┼   may be parked now; ≤ 2P per identity,       │
  │   Bench: MAC, freshness, replay floor,     │  frames    │   ≤ reverse_max_slots in all                  │
  │   lease/epoch, journal (at most once),     │  (sealed)  │ checkout(): exclusive, eligibility re-checked │
  │   max_runs = signed concurrency            │            │ RelayServer (unix 0600) for other processes   │
  │   rootless container per job               │            │ ReverseWorker = SocketWorker over a slot      │
  └────────────────────────────────────────────┘            └──────────────────────────────────────────────┘
```

* **Who dials whom is the only change.** The TLS roles are those of a listening worker: the worker is the TLS
  server, the control plane the TLS client (`wrap_socket(server_side=True)` on a socket the worker connected). The
  wire (`agentnode-worker/1`: `[4-byte length][32-byte HMAC-SHA256][canonical JSON]`), lease fencing, journal and
  reconciliation are the existing ones, unchanged.
* **One request per slot, never reused.** A slot that carried a request is closed by both sides; an idle slot is
  closed by the agent after 110 s (below the 120 s freshness window) and replaced. Failed dials back off 0.5–30 s
  with full jitter; the agent dials at most 4 times a second.
* **The hub holds no pair key** and never opens a frame: sealed bytes pass between the control plane's
  `ReverseWorker` and the agent untouched, so MAC and identity stay end to end.
* **Capacity.** `P = concurrency + 4`: the four extra slots carry lease renewals, `result`, `stop`, trust
  installation and probes, so runs cannot starve control traffic. The agent itself refuses a `run` beyond its signed
  concurrency (`worker-busy` → `target_full`) and keeps no queue; queueing and fairness stay in the control plane's
  scheduler (global, per-account, per-target FIFO, round robin across targets).
* **Register.** A dial-back target is a `CUSTOMER` target with address `reverse://<instance>`, topology
  `reverse-session`, an immutable `device_id` (part of the endpoint digest and of `Target.binding`) and assurance
  `customer_controlled_observed`.

## 2. Threat model (what is defended, and how)

| Threat | Defence |
|---|---|
| Someone reaches the customer machine inbound | Nothing listens. `worker connect` opens no socket; the TLS door is never bound. |
| Anyone connects to the hub | Mutual TLS, deployment anchor, role WORKER, exact instance of a parkable dial-back target; otherwise closed and counted (`refused`). Slice 1: private address only, never a wildcard, public address, loopback or :8443. |
| A worker of another account is used | Targets are visible only to their owner; a foreign id is `target_unknown`, indistinguishable from a missing one. Managed stays the default; a wished local target that is offline/full/unsuitable is refused, never replaced by managed. |
| The device is revoked / the account suspended / the target suspended or revoked / the kill switch is on | Checked at resolve, at admission recheck, at every slot checkout (the hub drains the instance) and before every lease renewal. "Immediately" means no new checkout once the change is observed; a running job ends at the latest when its 20-s lease lapses (renewal every 5 s; the worker's `LeaseWatch` stops and cleans it). |
| A frame is replayed, stale, forged or for other work | Unchanged worker-wire refusals (MAC, freshness, durable floor, nonce memory, closed fields); `RUN_ID_CONFLICT` for other work under a known run id. |
| An answer is lost | `result(run_id)` reconciliation, never a blind resend. A run the worker never recorded is `indeterminate`, never success; `not_started` only when the worker proves it accepted and never started it. |
| The agent claims properties | Suitability is observed inside isolated jobs over the agent's own slots and operator-signed: network denial (canary), memory ≥ 1024 MiB with usable memory, storage ≥ 512 MiB written and its ceiling hit, wall clock cut off, a one-core CPU quota with a two-core negative control, runtime/platform from inside the job, isolation boundary (user namespace, no capabilities, read-only root, own PID namespace). Claims are recorded as claims and never used. |
| No isolating runtime | The agent refuses before any dial (`runtime-absent`), and again at every `run` before the journal claims it. Nothing ever runs on the host. |
| The agent's secrets leak | Key and keyring must be this account's and ≤ 0600, state directories writable by nobody else, or the agent does not start. The one-use enrolment secret and request are deleted before the first dial. The agent holds its own key/certificate, the deployment anchor and lists, its pair key, its replay floor and journal — never an account token, operator key or approval credential. Logs carry names, never key material or certificate bodies. |
| Pairing is abused to run work | Pairing grants nothing: prepare → operator approval → submit is unchanged, the approval is single use (300 s), and its nonce binds account, capability, content and the target binding including `device_id`. |

## 3. Pairing (slice 1: operator invite + offline bundle)

1. Operator: `targets_cli invite --account A --reverse --device D --concurrency N --queue M`. `D` must be one of
   `A`'s live devices now (`device_authority`); otherwise refused by name. The bundle contains the anchor, the
   signed lists, the pair key, the one-use enrolment secret and `target.json` with the hub's address (`connect`) and
   the signed limits — no account, approval or operator material.
2. Customer machine: an unprivileged account makes its own key and request (`agentnode pki request --tls-dir ...`).
   The key never leaves the machine.
3. Operator: `targets_cli enroll --target T --request request.json` issues the certificate; it goes back to the
   machine, which initialises its root-owned time floor.
4. Customer machine: `agentnode worker connect --control tcps://<hub> --max-runs N --keyring ... --journal ...
   --tls-dir ... --trust ... --deployment ... --accept-gateway <gw> --revocation-list ... --tombstones ...
   --floor ...`. Before its first dial it removes the enrolment secret and request.
5. Operator: `targets_cli probe --target T` measures through the agent's own slots; only a passing, signed
   observation activates the target.

OAuth onboarding and online pairing codes are slice 2.

## 4. Recovery

* **Agent restart:** the journal is on the agent's disk; a run id it has seen is answered from it, never run again.
  Accepted-but-never-started runs are settled `never_started` at start.
* **Control/hub restart:** the hub keeps no state; agents redial within their backoff. Admitted dispatches are
  reconciled by the existing startup reconciliation (exactly one `catalog_terminal`).
* **Network loss before a run:** no slot within the caller's budget → `target_offline`, nothing admitted.
  **During a run:** the control plane cannot renew; the worker stops the job within the lease bound and journals
  the cleanup; the control plane reconciles with `result(run_id)` once a slot exists again. **After a run:** the
  answer is lost; `result(run_id)` returns the journaled outcome.

## 5. Platform limits (slice 1)

* Positive execution: Linux with rootless podman only (the private two-host proof).
* Native Windows: refused before any dial (`runtime-absent`); restrictive ACLs on the agent's secrets are not
  proved by this build, so the agent refuses to start there. WSL2 is detected and reported (`wsl2_distribution`),
  never installed or started; positive WSL2 execution is not claimed.
* The hub is single-process; other processes reach it through the local relay socket. No horizontal scaling, no
  public route, no port 443.

## 6. Slice-2 prerequisites (not done here)

* Public outbound connection to an authenticated control endpoint over TCP 443, with proxy/load-balancer
  behaviour, connection draining and denial-of-service limits specified and tested.
* A signed, reproducible installer and updater with rollback, provenance, version pinning and uninstall.
* Real Windows installation (service identity, restrictive ACLs, secure key storage, a supported isolation
  runtime) and real Linux installation across supported distributions (systemd hardening, runtime checks,
  firewall, package signing).
* Positive WSL2 installation and isolated execution with an installed distribution.
* OAuth onboarding and online pairing codes (account creation policy, expiry, replay protection, recovery,
  device replacement).
* Public PKI issuance, renewal, rotation and revocation distribution for intermittently connected agents.
* Hub horizontal scaling, authenticated slot ownership across processes, reconnect storms, capacity planning and
  durable presence.
* NAT, enterprise proxy, TLS interception, IPv4/IPv6, captive-network and long-offline compatibility testing.
* Customer-facing device inventory, rename, suspend, revoke, re-pair and recovery flows.
* A compatibility policy and exercised upgrade matrix for the worker protocol and `reverse-session` features.
* Signed release artifacts, SBOM, vulnerability response, telemetry/privacy policy and customer documentation.
* Full positive cross-platform end-to-end CI and an independent security review before any public route.
