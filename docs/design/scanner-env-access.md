# Scanner: environment-variable access

Status: plan, 2026-11-03. Not implemented. Own small security arc; the rule is not loosened as a side effect of
other work.

## The problem

`app/trust/scanner.py` rates every line that matches `os.environ` or `os.getenv(` as `env_harvesting`, severity
**high** ("potential data exfiltration risk"). The filter after the scan keeps `env_access` findings regardless of
the declared permissions, and any high finding quarantines the version (`Security scan: N high-severity finding(s)`).

Reading a configured variable is how a package gets its own API key. The rule cannot tell that apart from dumping
the environment.

Evidence on prod (read-only, 2026-11-02):

- 30 versions sit in quarantine with exactly this reason, all `agentnode` `*-agent@1.0.0` from 2026-04-22, each with
  one hit at `agent.py:31` and no other high finding. Every one of those packages has newer non-quarantined versions,
  so nothing is blocked today; they pad the admin digest (39 waiting > 24 h).
- Starter packs read named variables the same way, e.g. `os.environ.get("AZURE_SUBSCRIPTION_ID", "")` and
  `os.environ.get("AGENTNODE_API_KEY", "")`. An outside publisher writing the same line is quarantined on publish.
- Because the P0-02 gate blocks auto-clear on open *medium* findings too, simply moving the rule to medium would still
  hold every new publisher's code package that reads its key. Severity alone is the wrong lever.

## Goal

1. Legitimate access is not high: reading a named variable, especially one the manifest declares in
   `env_requirements`, and passing a filtered environment to a subprocess.
2. Real harvesting still blocks: bulk reads of the environment, reading other parties' secrets, and moving secret
   values out of the process.

## Proposed rules (AST over each `.py` file, not regex per line)

| Pattern | Severity |
| --- | --- |
| Named read (`os.getenv("X")`, `os.environ.get("X")`, `os.environ["X"]`) of a variable declared in the manifest's `env_requirements` | info (not stored as a blocking finding) |
| Named read of an undeclared variable | low, listed for review, does not block auto-clear |
| `os.environ` used as a whole: `dict(os.environ)`, `.copy()`, `.items()/.keys()/.values()`, iteration, `json.dumps(os.environ)`, `str(os.environ)`, `**os.environ` | high |
| Non-literal key read inside a loop or comprehension over many names | high |
| Any env value (named or bulk) that reaches a network sink (`requests`/`httpx`/`urllib`/`socket`) or a subprocess argument in the same function | high |
| Reading credential stores: `~/.aws/credentials`, `~/.ssh/`, `.netrc`, `.pypirc`, `.npmrc`, `.docker/config.json`, browser profile or keychain paths | high |
| `subprocess(..., env=os.environ)` or a filtered copy | low |

Also: the manifest fallback (`_scan_content(str(pv.manifest_raw), "<manifest>")`) runs the code patterns over
manifest text. Env rules must not fire on manifest prose.

Unchanged: secret, dangerous-call, network and bandit rules; the AI scanner; skills (`skill_scan.py`).

## Prospective proof (before any merge)

- **Tests first, red on today's code where they should be.**
  - The real `agent.py:31` line and the two starter-pack lines above must come out non-blocking. These tests fail
    on today's scanner.
  - Malicious samples must stay high under both the old and the new scanner: a full environment dump posted to a
    URL, `getenv` in a loop over common secret names, an `~/.aws/credentials` read followed by a send, and
    `str(os.environ)` written into an HTTP header.
  - A counter-check that turns red if the bulk-dump sample is rated anything below high.
- **Corpus dry-run, read-only, on prod artifacts.** Run old and new rules over every stored `.py` artifact and list
  each version whose maximum severity changes, with the line. The owner reviews that delta before deploy. Any
  version going *up* is a finding of its own.
- **No retroactive clearing.** Deploying the rule changes nothing for existing quarantines. Re-scanning or clearing
  the 30 April versions is a separate, owner-gated step. They could also just stay; newer versions are live.

## Out of scope

Runtime environment isolation (sandbox credential matrix) and the AI scanner key. Neither is a reason to loosen a
static rule, and neither is part of this arc.

## Steps

One PR: the AST rules plus the tests above, then the dry-run report, then an owner-gated deploy.
