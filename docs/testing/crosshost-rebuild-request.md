# Cross-host test stand: rebuild request and acceptance plan

Status: **prepared, not started.** Nothing in this file has been done on a server. It supersedes,
for the next acceptance run, the earlier request in the review bundle
(`agentnode-Tests/a1e_review/crosshost-repair-2/REBUILD-REQUEST.md`). Checked by
`sdk/tests/test_crosshost_rebuild_request.py`.

Who does what is split on purpose:

* **Chef steps** happen at the server provider and nowhere else. The rebuild of each machine is
  one. Who performs it stays open until the Chef reports it done.
* **Mitarbeiter steps** begin only after the Chef's report for the machine concerned, and only over
  SSH to the two machines named below. None of them touches the provider account.

## Targets -- these two machines and no others

| role | server name | public IPv4 | private IPv4 |
|---|---|---|---|
| control plane | `agentnode-control-r16` | `2.28.194.41` | `10.0.0.4` |
| worker | `agentnode-worker-r16` | `128.140.87.58` | `10.0.0.2` |

The live service and the DevelopServer with its closed alpha are not part of this stand; they are
deliberately not named here by address. Reaching any other machine is an abort condition.

## Criteria, their origin, and the profile the run is judged against

* **Acceptance catalog (decided for this rebuild):** X1-X10, **unchanged**, from
  `remote-worker-cross-host-r2` (`a1e_review/crosshost-final/remote-worker-cross-host-r2.json`,
  sha256 `5fc1cc507413df18a6fbf22f5c09df63d6443f1409a7cc92b57eef2f3738e56d`). r2 is not edited.
* **Historical frozen state:** `remote-worker-cross-host-r3`
  (`a1e_review/crosshost-r3/frozen/remote-worker-cross-host-r3.json`, sha256
  `3aaee465933f2a777d8fed46b17ab4874970209b7d929de7a8d17b7e7bc02a10`, frozen 2026-10-02T17:15:58Z)
  carried the same ten criteria over verbatim and renewed only its bindings. It stays unchanged and
  is named as history, not as the catalog.
* **The profile the run is judged against: `remote-worker-cross-host-r4`, which does not exist yet.**
  It is created after both rebuilds, carries X1-X10 verbatim from the r2 catalog, names r3 (with its sha256) as
  what it supersedes, and binds exactly the two targets above and the build actually tested. It is
  validated, filed in the evidence folder and **frozen prospectively before any criterion runs**.
  Its sha256 is whatever the frozen file turns out to be; nobody writes it down in advance.
* **Build-up procedure:** `remote-worker-r1` (`a1e_review/remote-worker/remote-worker-r1.json`,
  sha256 `e480c2f02382bad9432706561a49add218f4f9f03cb9a368b83804fcf2108d3a`), carried out through
  the two-host install in `sdk/deploy/separate-worker-host/README.md` (install 1-4, upgrade,
  rollback, diagnose), which is what its R14 and R16 ask for. The criteria map below ties every X
  criterion to the r1 criterion it comes from and to the steps that answer it.

```json
{
  "targets": {
    "control": {"name": "agentnode-control-r16", "public_ipv4": "2.28.194.41", "private_ipv4": "10.0.0.4", "role": "control-plane"},
    "worker": {"name": "agentnode-worker-r16", "public_ipv4": "128.140.87.58", "private_ipv4": "10.0.0.2", "role": "worker"}
  },
  "build_up_procedure": {"profile": "remote-worker-r1", "sha256": "e480c2f02382bad9432706561a49add218f4f9f03cb9a368b83804fcf2108d3a"},
  "acceptance_criteria": {
    "catalog_profile": "remote-worker-cross-host-r2",
    "catalog_sha256": "5fc1cc507413df18a6fbf22f5c09df63d6443f1409a7cc92b57eef2f3738e56d",
    "criteria": ["X1-A-WORK-CROSSES-THE-KERNEL-BOUNDARY", "X2-B-THE-WORKER-PORT-IS-PRIVATE-AND-THAT-IS-MEASURED-NEGATIVELY",
                 "X3-C-A-REAL-NETWORK-BREAK-DURING-A-JOB", "X4-D-BOTH-SIDES-SURVIVE-A-RESTART",
                 "X5-E-INSTALL-UPGRADE-ROLLBACK-ON-TWO-HOSTS", "X6-F-WHAT-IS-NOT-ON-THE-WORKER-DISK",
                 "X7-G-EXHAUSTION-ON-THE-WORKER-LEAVES-THE-CONTROL-PLANE-HONEST", "X8-R16-TWO-REAL-MACHINES",
                 "X9-NO-RESULT-WAS-REPEATED-INTO-EXISTENCE", "X10-THE-RECORD-DOES-NOT-OVERCLAIM"]
  },
  "history": [{"profile": "remote-worker-cross-host-r3", "sha256": "3aaee465933f2a777d8fed46b17ab4874970209b7d929de7a8d17b7e7bc02a10", "role": "historical frozen state, unchanged"}],
  "new_profile": {
    "name": "remote-worker-cross-host-r4",
    "supersedes": {"profile": "remote-worker-cross-host-r3", "sha256": "3aaee465933f2a777d8fed46b17ab4874970209b7d929de7a8d17b7e7bc02a10"},
    "criteria": "X1-X10 verbatim from the r2 catalog",
    "binds": {"control": "2.28.194.41", "worker": "128.140.87.58",
              "build": "the head commit, wheel digest and both role artefact digests of the build actually installed in M4/M5, read from that build"},
    "filed_at": "crosshost-r4/frozen/remote-worker-cross-host-r4.json"
  },
  "steps": [
    {"id": "C1", "phase": "chef-rebuild", "who": "chef", "machine": "control", "what": "rebuild agentnode-control-r16 in place, image Rocky Linux 10; server type, private network and firewalls unchanged; report done"},
    {"id": "V1", "phase": "validate-machine", "who": "mitarbeiter", "machine": "control", "what": "control plane on its own: new SSH host key shown to and confirmed by the Chef (never accepted silently), hostname agentnode-control-r16, private address 10.0.0.4, nothing of AgentNode present; baseline transcript"},
    {"id": "C2", "phase": "chef-rebuild", "who": "chef", "machine": "worker", "what": "rebuild agentnode-worker-r16 in place, image Rocky Linux 10; server type, private network and firewalls unchanged; report done -- only after V1 passed"},
    {"id": "V2", "phase": "validate-machine", "who": "mitarbeiter", "machine": "worker", "what": "worker on its own: new SSH host key confirmed by the Chef, hostname agentnode-worker-r16, private address 10.0.0.2, nothing of AgentNode and no podman left from earlier runs; baseline transcript"},
    {"id": "M4", "phase": "install", "who": "mitarbeiter", "machine": "control", "what": "install phase 1 on the control plane from its own artefact: new deployment, new identities, new pair key; enabled, not started"},
    {"id": "M5", "phase": "install", "who": "mitarbeiter", "machine": "worker", "what": "install on the worker from its own artefact, three passes with the enrolment handover; rootless podman as the worker account"},
    {"id": "P1", "phase": "profile-create", "who": "mitarbeiter", "machine": "none", "what": "create remote-worker-cross-host-r4: X1-X10 verbatim from the r2 catalog, supersedes r3 with its sha256, binds 2.28.194.41, 128.140.87.58 and the build installed in M4/M5"},
    {"id": "P2", "phase": "profile-validate", "who": "mitarbeiter", "machine": "none", "what": "check r4 mechanically: criteria byte-equal to r2's, r2 and r3 file digests unchanged, both targets and the installed build bound, no result inherited"},
    {"id": "P3", "phase": "profile-file", "who": "mitarbeiter", "machine": "none", "what": "file r4 in the evidence folder under crosshost-r4/frozen/"},
    {"id": "P4", "phase": "profile-freeze", "who": "mitarbeiter", "machine": "none", "what": "freeze r4 prospectively: FREEZE.txt with its sha256 and the evidence chain head, before any criterion runs; r4 is never edited afterwards"},
    {"id": "M6", "phase": "criterion", "who": "mitarbeiter", "machine": "both", "what": "start the worker, then install --verify on the control plane: measure the worker, check mutual TLS and the expected worker identity, only then start"},
    {"id": "M7", "phase": "criterion", "who": "mitarbeiter", "machine": "both", "what": "scenario A: a job with a nonce in its source crosses to the worker; the worker boot id is read independently over SSH"},
    {"id": "M8", "phase": "criterion", "who": "mitarbeiter", "machine": "both", "what": "scenario B: worker port 8443 from the control plane, from the public internet, and with the admitted source changed and restored"},
    {"id": "M9", "phase": "criterion", "who": "mitarbeiter", "machine": "both", "what": "scenario C: a real network break and packet loss during a job; lease, cleanup, reconnect, exactly one closing line"},
    {"id": "M10", "phase": "criterion", "who": "mitarbeiter", "machine": "both", "what": "scenario D: worker restart and control-plane restart; boot id, journal, epoch, time floor"},
    {"id": "M11", "phase": "criterion", "who": "mitarbeiter", "machine": "both", "what": "scenario E: upgrade and rollback per host with upgrade-one-host.sh and rollback-one-host.sh in the documented order, a job after each"},
    {"id": "M12", "phase": "criterion", "who": "mitarbeiter", "machine": "worker", "what": "scenario F: enumerate the worker disk for tokens, account lists, signing identity, CA private key"},
    {"id": "M13", "phase": "criterion", "who": "mitarbeiter", "machine": "worker", "what": "scenario G: drive worker CPU, memory and disk to exhaustion; the control plane refuses honestly"},
    {"id": "M14", "phase": "criterion", "who": "mitarbeiter", "machine": "none", "what": "file every transcript, red ones included, hash-chained in the evidence folder; independent review against the frozen r4"},
    {"id": "M15", "phase": "park", "who": "mitarbeiter", "machine": "both", "what": "park both machines: services stopped and disabled, no identity or pair key left, 8443 private only, nothing running"}
  ],
  "criteria_map": {
    "X1-A-WORK-CROSSES-THE-KERNEL-BOUNDARY": {"r1": ["R16-TWO-REAL-MACHINES", "R3-ONE-WORKER-ONE-PROOF"], "steps": ["M6", "M7"]},
    "X2-B-THE-WORKER-PORT-IS-PRIVATE-AND-THAT-IS-MEASURED-NEGATIVELY": {"r1": ["R16-TWO-REAL-MACHINES"], "steps": ["M8"]},
    "X3-C-A-REAL-NETWORK-BREAK-DURING-A-JOB": {"r1": ["R4-FAIL-CLOSED-ON-LOSS-REVOCATION-EXPIRY-AND-TIME", "R6-EXACTLY-ONCE-OR-HONESTLY-UNKNOWN", "R8-CLEANUP-IS-DONE-OR-HONESTLY-UNPROVEN"], "steps": ["M9"]},
    "X4-D-BOTH-SIDES-SURVIVE-A-RESTART": {"r1": ["R4-FAIL-CLOSED-ON-LOSS-REVOCATION-EXPIRY-AND-TIME", "R6-EXACTLY-ONCE-OR-HONESTLY-UNKNOWN"], "steps": ["M10"]},
    "X5-E-INSTALL-UPGRADE-ROLLBACK-ON-TWO-HOSTS": {"r1": ["R14-INSTALL-UPGRADE-ROLLBACK-AND-DIAGNOSE"], "steps": ["M4", "M5", "M6", "M11"]},
    "X6-F-WHAT-IS-NOT-ON-THE-WORKER-DISK": {"r1": ["R5-WHAT-THE-WORKER-IS-NOT-GIVEN", "R11-NO-SECRET-WHERE-A-SECRET-DOES-NOT-BELONG"], "steps": ["M12"]},
    "X7-G-EXHAUSTION-ON-THE-WORKER-LEAVES-THE-CONTROL-PLANE-HONEST": {"r1": ["R12-A-REFUSAL-SAYS-WHICH-CAUSE-AND-WHAT-TO-DO"], "steps": ["M13"]},
    "X8-R16-TWO-REAL-MACHINES": {"r1": ["R16-TWO-REAL-MACHINES"], "steps": ["M7", "M8"]},
    "X9-NO-RESULT-WAS-REPEATED-INTO-EXISTENCE": {"r1": ["R15-THE-COUNTER-CHECKS-DISCRIMINATE", "R17-THE-BUNDLE-DOES-NOT-OVERCLAIM"], "steps": ["M14"]},
    "X10-THE-RECORD-DOES-NOT-OVERCLAIM": {"r1": ["R17-THE-BUNDLE-DOES-NOT-OVERCLAIM"], "steps": ["M14"]}
  },
  "abort_conditions": [
    "a machine answers with a different hostname, private address or role than in targets",
    "a host key differs and the Chef has not confirmed the new one",
    "any address other than the two targets would be reached",
    "anything would create a new paid resource or exceed the cost cap",
    "the single-machine validation of the control plane has not passed before the worker is rebuilt",
    "r4 has not passed its freeze (P4), or its criteria differ from the r2 catalog's, when the first criterion step would start",
    "a step would need a credential that is not already provided for it"
  ],
  "rollback": "Before M4 nothing of AgentNode exists on the machines, so nothing is rolled back: the stand stays rebuilt and idle. From M4 on, a failed step stops the run; both machines are parked as in M15 (services stopped and disabled, identities and pair key removed, 8443 private only) and the attempt is filed, red, in the evidence folder. A frozen r4 is never edited: a mistake in it means a new profile, not a correction. Product rollback inside the run is part of the test (M11), not a recovery.",
  "evidence_dir": "H:\\Claude Projekte\\agentnode-Tests\\a1e_review\\crosshost-r4",
  "cost_cap_eur_per_month": 20
}
```

## Chef steps -- at the server provider (C1, C2)

One machine after the other: the control plane first, the worker only after V1 has passed.

1. Open the server in the provider console, then **Rebuild**.
2. Image **Rocky Linux 10** (the machines ran Rocky Linux 10.2, kernel `6.12.0-211.*.el10_2`,
   SELinux permissive).
3. Confirm; all data on that server is lost, which is the point.
4. Change nothing else: not the server type, not the private network `agentnode-crosshost`
   (`10.0.0.4` / `10.0.0.2`), not either firewall.
5. Report it done. A rebuild in place creates no new paid resource; the stand stays at or below
   the cap.

## Mitarbeiter steps

Only after the Chef's report for the machine concerned. V1 and V2 confirm each machine on its own --
reachable, the expected host by name and private address, the new host key confirmed by the Chef,
nothing of AgentNode left -- before anything is installed and before the next rebuild. Then the
install (M4, M5), then r4 is created, validated, filed and frozen (P1-P4), and only then do the
criterion steps (M6-M14) run. Every step writes its transcript into the evidence folder; secrets
never appear in it.

## Abort conditions, rollback, evidence, cost cap

The JSON above is authoritative. In words: stop at the first mismatch of name, address, role or
host key; never reach a third machine; never create a paid resource; file every attempt, the red
ones too, in `agentnode-Tests\a1e_review\crosshost-r4`; the stand stays at or below **20 EUR per
month**. The worker rollback guard (P13) is not accepted as a WARN: until it is verifiable, X5
cannot pass on the worker-rollback path.
