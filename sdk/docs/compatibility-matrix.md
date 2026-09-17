# Which AIs can actually use this sandbox

Four states, and they mean different things. The difference between the first two is the whole
point of this page: one of them was watched happening, the other was reasoned about.

| State | What it means |
|---|---|
| **Really observed** | This client, by name and version, carried out the whole journey against a running gateway, and the gateway's own audit records it. Not a demo, not a screenshot. |
| **Protocol verified** | The protocol it speaks is exercised by the test suite and by the bridge, so a client speaking it correctly will work. Nobody has run *that* client. |
| **Not yet tested** | Expected to work, nothing has established it. Listed so the gap is visible rather than implied. |
| **Not compatible** | It cannot call a tool. No amount of prompting changes that. |

There is no state that means "works with all AIs", because no evidence would support it.

## Really observed

Both drove the entire journey themselves: read the capabilities, prepared a job, described in
plain language what would happen, submitted it, followed the status, read the result, and
reported the cleanup. Neither was given the answers.

| Client | Vendor | Transport | Observed | Build | What the gateway recorded |
|---|---|---|---|---|---|
| Claude Code 2.1.272 (CLI, `-p`) | Anthropic | local stdio bridge → MCP | 2026-09-15 | Managed Alpha R2 | `capabilities`, `prepare`, `submit`, `status`, `result`, `devices.list` via `mcp`; run finished, cleanup confirmed |
| Codex CLI 0.151.0 (`codex exec`, gpt-5.6-sol) | OpenAI | local stdio bridge → MCP | 2026-09-15 | Managed Alpha R2 | the same six, via `mcp`; run finished, cleanup confirmed |

Both printed `2147483647` from `print(2**31 - 1)` and both reported the container as confirmed
gone. Both were also asked to try a device- or session-management tool: each found that only
`devices.list` — which reads and changes nothing — was offered to it, and said so.

The gateway's own audit for that account, across the runs behind this table:

```
capabilities       via mcp   carried_out          x5
devices.list       via mcp   carried_out          x5
prepare            via mcp   carried_out          x8
result             via mcp   carried_out          x5
status             via mcp   carried_out          x5
submit             via mcp   carried_out          x5
submit             via mcp   disclosure_required  x1
submit             via mcp   malformed            x3
```

The refusals are shown on purpose. `malformed` is a run id a model had already used — picking a
deterministic name and running again is what both of them do — and `disclosure_required` is from
the earlier build, before a refusal that started nothing stopped consuming the approval. A matrix
that showed only the successful calls would describe a journey nobody had.

**What this does not establish.** Two clients, one task, one gateway, on one day. It says these
two can do it. It does not say every model behind them always will, and it says nothing about a
client not in this table.

**Which build was observed.** The date and the build are both in the table, because an
observation is a thing that happened and a thing that happened does not update itself. These rows
were re-run against Managed Alpha R2 after it was deployed to the alpha; they are not the earlier
observation carried forward.

**What the models found that the suite did not.** Three defects, on a build whose suite was
green:

* a submission refused for a reason that meant **nothing started** still consumed the single-use
  approval, so a person was sent back to agree again to a job that had never run;
* `devices.list` had always answered `last_used: null`, because nothing ever set it — and "which
  of these am I still using" is the question somebody opens a device list to answer;
* `requested_policy_sha256`, the digest of the policy a person approves, was **one constant for
  every job this gateway had ever disclosed**, because `prepare` was digesting an integer. Claude
  Code noticed that the number on the approval and the number on the submission disagreed, and
  said so — correctly adding that no restriction had been loosened, which was also true.

All three are fixed and all three have tests. They are listed here rather than only in a
changelog because they are the argument for this page existing: a suite exercises the gateway's
own idea of a client, and neither of these clients is that.

## Protocol verified

| Surface | How it is verified | Who this covers |
|---|---|---|
| Remote MCP over HTTP (`/v1/mcp`) | the suite drives it directly; the console's compatibility challenge is satisfied only by a real call arriving this way | any MCP client that can set a header and reach the gateway |
| Local stdio bridge (`agentnode remote bridge`) | forwards to `/v1/mcp` verbatim, so what a client gets is the gateway's own answers; the gateway records the channel as `mcp` | MCP clients that will only launch a local process |
| REST (`/v1/op/*`) + OpenAPI | the suite runs the whole journey over it | anything that can call an HTTP API |

A client here is expected to work. Until one is run, that expectation is not evidence.

## Not yet tested

| Client | Why not, specifically |
|---|---|
| ChatGPT (chatgpt.com, connectors) | needs an MCP endpoint it can reach from the internet. This gateway is bound to loopback and no port is open, by decision. Until that exists this stays here — it is **not** claimable as working. |
| Claude Desktop | local MCP, expected to work through the bridge; not run |
| Cursor, Windsurf, Zed | same |
| LangChain / LlamaIndex agents | the REST surface and the adapter exist; no agent has been driven end to end |

## Not compatible

| Client | Why |
|---|---|
| Any chat-only AI with no tool interface | It cannot call anything. A person can paste output in and out by hand, and that is a person using the sandbox, not the AI using it. Saying otherwise would make "compatible" mean nothing. |

This is said in the console too, in those words, and the console does not offer to pretend
otherwise.

## What would move ChatGPT into "really observed"

1. A remote MCP endpoint reachable from the internet, with a certificate a public client trusts.
2. A hostname, so the certificate can be for something other than an IP address.
3. Both of those are founder decisions: they mean opening a port and obtaining a domain. The
   architecture and the tests for it can be prepared without either, and are.

Nothing here should be read as a recommendation to do that before the production-readiness work
is finished — a publicly reachable endpoint is also a publicly reachable target.
