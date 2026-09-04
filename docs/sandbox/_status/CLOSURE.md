# Sandbox documentation track — closure status

**TECHNICAL DOCUMENTATION COMPLETE — human usability and remaining platform validation pending.**

Recorded against the branch state of 2026-09-04. This file is a status record rather than a
documentation page: it is not part of the reader-facing set and is not checked by
`_checks/check_docs.py`.

## What is being closed

| | |
|---|---|
| branch | `docs/sandbox-track` |
| documentation anchor | `1883338d118651692882343fe087ea0c3ebe4cc4` |
| tree of that commit | `272c3bd18cec52b11f537791f720adeda36ad41a` |
| base | `main 8b83059` |
| product source changed | none |
| merged | no |
| published | no |

The anchor above is the commit carrying the reviewed documentation. **This status file is committed
after it and therefore names that commit rather than itself** — a commit cannot contain its own
hash. The per-file hashes below are the authoritative anchors for the content.

## Regenerated against the merged code

This status was first written against `main 8b83059`. The EM-3B conformance suite (#116) and the
EM-3B-R1 runtime correction (#117) have since merged, and the pages were regenerated against that
code rather than left describing what it used to do. What changed in the facts, and therefore in the
pages:

* **the memory ceiling now includes swap** — `--memory-swap` is set to the same value as `--memory`,
  so the total binds. An engine that cannot account for swap is refused rather than run under a
  limit that might not hold;
* **a wall-clock timeout now ends the program**, not just the command that started it: the run's own
  container is removed, waited for and checked absent by id and by name, and a stop that cannot be
  shown is a distinct containment error rather than an ordinary timeout;
* **refusals are structured**, with eight situations the classifier tells apart, each carrying an
  action that exists on the platform in front of you and a re-check afterwards.

A checker now binds those cases to the pages in both directions: a case the code can refuse with
that no page describes fails, and a case the checker knows that the code no longer has fails too.

## The three separate assessments

A missing proof in one category does not turn a passed category into a defect. These answer three
different questions and are kept apart deliberately.

| category | question | status |
|---|---|---|
| **TECHNICAL_CONTENT** | does the documentation agree with the code, the contract and the security model? | **COMPLETE** — six criteria passed |
| **PLATFORM_VALIDATION** | were the described steps actually carried out on each platform? | **PARTIAL** — Linux only; everything else is source-verified or does not exist |
| **HUMAN_USABILITY** | have real non-technical people completed the flow? | **DEFERRED_EXTERNAL_VALIDATION** — no participants; not simulated, not inferred |

## TECHNICAL_CONTENT — the six criteria that passed

Verdict `SANDBOX-DOCS-0008`, profile `sandbox-docs-r8`, against the anchor above:

| criterion | result |
|---|---|
| `D1-MATCHES-THE-CODE` | PASS |
| `D2-AVAILABILITY-IS-HONEST` | PASS |
| `D3-NO-UNSAFE-INSTRUCTION` | PASS |
| `D4-NO-FALSE-SECURITY-CLAIM` | PASS |
| `D6-CONSISTENT-WITH-THE-CONTRACT` | PASS |
| `D7-VERDICT` | PASS |

Eight review rounds, twenty-one findings, every one corrected except the last, which is recorded
below as what it is. Two corrections changed what the product says about itself: the
destination-limited network was marked inert on the strength of a stale docstring while two run
paths call it, and a consented key does reach the sandboxed program rather than staying outside it.

## HUMAN_USABILITY — DEFERRED_EXTERNAL_VALIDATION

`D5-BEGINNER-PATH-WORKS` was returned NOT_EVIDENCED. That is the absence of an external result, not
a defect found in the text. Ten people and a facilitator produce it; nothing else does.

**No simulated study will be run.** No automated browser test, no persona, no model, and no reading
of the pages by their author counts as a substitute. Until real sessions happen, no page, blog draft
or marketing line may say the onboarding is validated by beginners, easy, or quick.

The test pack is preserved **unchanged** on branch `em3a/sandbox-contract`, tip
`3fbb71c24838bfcb8c47cdb45b36abb0e495e28a`:

| file | bytes | sha256 |
|---|---|---|
| `docs/em3/usability/README.md` | 3,672 | `5a59d3dc0b99ac3aeaa536772651e18eceb33c1c4e883d2ae8cf54001cfaf409` |
| `docs/em3/usability/accessibility.md` | 1,470 | `5b07fdb5358061b500e591f0bb175c31adfee670aa36def6c1c610ea80a2cb98` |
| `docs/em3/usability/commit_roster.py` | 1,806 | `bbfa8a47a22a9e0157f9156e0c95ce2938e06fb698b923acda55ca42c594233c` |
| `docs/em3/usability/evaluate.html` | 10,073 | `136c988c5bba774493529a27e3a310518145662d4f8b79b296140deaf3105900` |
| `docs/em3/usability/evaluate.py` | 6,510 | `1a56f6f65cdaaf043edbb358ee8494b46eb0516813177d9ddd60907c9260f4c3` |
| `docs/em3/usability/results-guide.md` | 1,450 | `2087cc6259d07826576e6a20d1c0a21b5f0e58b9a6794ca0ca7d26aeeb0a8458` |
| `docs/em3/usability/results-template.csv` | 484 | `7042155ddbf12f206dff8a3f00e7a7063d101e27152b27fb6bd4d58816f925d5` |
| `docs/em3/usability/screening.md` | 1,654 | `c43d94b044ff15959bca7b98d578f5b8128405a5998f0142d7b1ef259faf479b` |
| `docs/em3/usability/session-script.md` | 2,977 | `18f905116acb32348e6948762bf0d71832579a944fdc74d0f6e89facdb399182` |

## PLATFORM_VALIDATION

Four statuses, and nothing is promoted by expectation:

* **tested** — the steps were executed there and the result recorded;
* **source-verified** — the code path was read and does not branch on that platform, and nobody has
  executed it there;
* **not tested** — neither;
* **not available** — it does not exist in the code, so there is nothing to test.

| platform or path | status | what backs it |
|---|---|---|
| Linux, ubuntu-24.04 (CI) | **tested** | run `33633469871`, five lanes with per-test outcomes; run `33654001890`, artefact smoke against the installed wheel |
| Linux desktop | **tested** | same code path, same runs |
| Linux server, headless and multi-user | **source-verified** | identical path; that deployment shape has not been exercised as such |
| Windows + Docker Desktop | **source-verified** | the runtime probe does not branch on the operating system. One hand observation exists of the *refusal* path with no runtime installed, which is not a sandbox run |
| Windows + WSL2 | **source-verified** | same probe; the WSL2/Windows split changes which PATH is searched and has not been exercised |
| macOS | **source-verified** | same probe; no macOS machine is in CI |
| Phone or tablet | **not available** | no mobile client and no remote backend exist |
| Self-hosted remote sandbox | **not available** | no remote backend class, no connector |
| Managed AgentNode Sandbox | **not available** | no managed backend, no service |

Capability rows, same vocabulary:

| what | status |
|---|---|
| tool packs in a container | tested |
| MCP servers in a container | tested |
| community agents in a container | tested |
| refusal when no runtime exists | tested |
| destination-limited network (`egress`) | tested |
| secrets passed by name | source-verified — live in both runners, no end-to-end run recorded |
| a community agent's own entrypoint on the host | not available — removed deliberately, and the source records the refusal |
| credential broker | not available |
| backend conformance suite | not available — this is EM-3B |
| EM-3 selection contract | not available on `main`; under review in pull request #115 |

The generated matrix in `availability.md` uses its own five-word vocabulary. The crosswalk is exact:
*available, tested* = **tested**; *available, not tested here* = **source-verified**; *planned* and
*removed* = **not available**; *experimental* would be **source-verified**, and no row currently
carries it.

## Inventory

### Documentation pages (12)

| file | bytes | sha256 |
|---|---|---|
| `docs/sandbox/README.md` | 2,584 | `9be589620372bd6f0d06616a5ff5be01e8e9195828cb8f8c7ccf3f9ebe8920a3` |
| `docs/sandbox/admin-policy.md` | 4,193 | `b1dd53e07e168402c2802930c6d0d2fd0481f7e1ab711ca6624153138b220549` |
| `docs/sandbox/availability.md` | 13,328 | `6a4992b8f0401b33850f5cc58ced98257831d1f74febfa71bd2f061cc46cc6fa` |
| `docs/sandbox/choose.md` | 3,008 | `afee2b76ca2543aebf26742a6c11f643b7c6c4fb888e5d64387eb2064327b159` |
| `docs/sandbox/managed.md` | 2,509 | `3e241c4f325521fd9c5f9ce7e21affb4fda1eb6a896e6325f51c0009c19e8c51` |
| `docs/sandbox/mobile.md` | 1,539 | `106840d9b2b72b02d07bb3657633afba9a34918d1327ccad06dc173c8d80b2b1` |
| `docs/sandbox/security-model.md` | 8,041 | `55b9d7a3d66620894fef3f65d81f2e8ea2d9f3faa584b982d68af7b7254e12ce` |
| `docs/sandbox/self-hosted.md` | 5,427 | `29553c0869b5fb8a93aa1027296a3daf537298adcc502d409d38f9fa5b006d11` |
| `docs/sandbox/setup-contract.md` | 4,475 | `c969701f63ff725bfca308cd7b739dd6d8fea395e2abceeffdb473338f60b0cf` |
| `docs/sandbox/setup-local.md` | 6,006 | `8f14c226a4ecb842d10e75fea46be519a3ad8059b7272de77fd9efa493dd81d9` |
| `docs/sandbox/troubleshooting.md` | 9,012 | `556a47e1f46f791ec13a5c66a0fc0b66ce6aaabb0f6404a27c239924c25a6b47` |
| `docs/sandbox/understand.md` | 3,426 | `655bcad194187f31134da0d2dcc5d5cfe392c6777399dc06a6370e08edba6b5b` |

### Generators and extracted facts

| file | bytes | sha256 |
|---|---|---|
| `docs/sandbox/_checks/build_matrix.py` | 20,000 | `8c3fb788bd00b2f0f834c10c7704c0550f9a272c2014866d979f0ec62ca7e925` |
| `docs/sandbox/_checks/check_docs.py` | 24,122 | `7067946245d934e62a65ea5f3ffffe3d39f43a34cbc542cf9c4e8547033e6311` |
| `docs/sandbox/_checks/extract_facts.py` | 12,244 | `14d5898dcada1e66c5763628f60aeb9d41f9e0871a861084ce2b9fb679d1f3f5` |
| `docs/sandbox/_facts/code-facts.json` | 7,694 | `0e8a558aea1921fdb60dde66882ee56a7d4c584832043392acd337c6e6bd9b10` |
| `docs/sandbox/_facts/test-evidence.json` | 9,324 | `2b60119b1ead9bc13e40d2a706f61e902915a5ed952209b2d365852e11fec4fe` |

### Blog drafts (7) — written, **not published**

None of these has been published anywhere. Every draft carries a header saying so, and the drafts
describing the self-hosted and managed services state in their own words that those services do not
exist, are not bookable and have no price.

| # | title | file | sha256 |
|---|---|---|---|
| 1 | Why AgentNode never quietly runs a stranger's code on your computer | `01-never-silently.md` | `eb5ace08412e0da842e55ab458f047befed00dcd043249bb30c5ec3e2891cf84` |
| 2 | Local, your own, or managed: which sandbox is yours? | `02-which-sandbox.md` | `6f89c9035439dea2f4511bc19818abc9526225d510a4bf44d467cf848074b95e` |
| 3 | Setting up a local sandbox without knowing what a container is | `03-setup-without-expertise.md` | `e35a5a121b90792a55b2a580498626a443d442a7065c140d7d23fef9a35e3259` |
| 4 | From a phone to a Linux server: where your agent actually runs | `04-where-your-agent-runs.md` | `d0603e00064c1881b257b85b7370818cc25c54d0529f85b7f03be2467de9800e` |
| 5 | Why your API keys do not belong in the sandbox | `05-keys-out-of-the-sandbox.md` | `ac101637e91b23c5ed56de357fd29df6b1de0ca39006c3ba9acf82fe220defc5` |
| 6 | Container, microVM, remote sandbox: what the words mean | `06-container-microvm-remote.md` | `dac25b66c9bbcadbe854b6891095b53bef17772794d63e0f2481f8becc4be61d` |
| 7 | The AgentNode Sandbox: what we are planning, and what we have not decided | `07-security-as-a-service.md` | `5f81a2810d79157bb3d34cff9042844848784f87a5aac2c7959df24f78f44dc7` |

## Open backend dependencies

Each of these is currently documented as absent. Building one changes the documentation rather than
contradicting it, which is the point of having written it this way.

1. **Backend conformance suite** — the next track, EM-3B. Nothing today can measure a backend's
   claims rather than repeat them.
2. **Remote backend and its connector** — `sandbox_backend_implementations` is `ContainerBackend`
   and `NoSandboxBackend`. Until a third exists, self-hosted and managed cannot be selected at all.
3. **Managed service** — no backend, no service, no billing, no region handling, no kill switch.
4. **Credential broker** — nothing substitutes a credential at a proxy, so a consented key is inside
   the sandbox with the program that asked for it.
5. **Egress binding beyond the hostname** — the proxy checks that a name resolves to a public
   address; it cannot verify the host behind the name without terminating the encryption.
6. **EM-3 selection contract** — exists on a branch, imported by nothing.

Two smaller ones worth carrying forward: the network mode named `restricted` emits an ordinary
bridge network and restricts nothing, and nothing selects it; and a single call is killed after 120
seconds while a long-lived agent session has no wall clock at all.

## What remains a founder action

Merging this branch, and publishing any of it. Neither has been done, neither is implied by this
status, and the technical work continues without them.
