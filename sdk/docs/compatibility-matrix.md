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

| Client | Vendor | Transport | Observed | What the gateway recorded |
|---|---|---|---|---|
| Claude Code (CLI, `-p`) | Anthropic | local stdio bridge → MCP | 2026-09-15 | `capabilities`, `prepare`, `submit`, `status`, `result` via `mcp`; run finished, cleanup confirmed |
| Codex CLI 0.151.0 (`codex exec`, gpt-5.6-sol) | OpenAI | local stdio bridge → MCP | 2026-09-15 | the same five, via `mcp`; run finished, cleanup confirmed |

Both printed `2147483647` from `print(2**31 - 1)` and both reported the container as confirmed
gone. Each needed more than one attempt to get a submission accepted, in every case because the
job drifted from the one that had been disclosed — which is the consent gate working. The
refusal now names the field that moved, and Codex recovered from exactly that.

**What this does not establish.** Two clients, one task, one gateway, on one day. It says these
two can do it. It does not say every model behind them always will, and it says nothing about a
client not in this table.

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
