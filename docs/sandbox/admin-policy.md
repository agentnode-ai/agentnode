# Administration and policy

*For administrators. Every key named here exists in the current code; keys that do not exist are not
listed, and settings that are planned are marked as such.*

## The rule that everything else follows from

**A lower level may only narrow.** An organisation policy can tighten what a user allows; a user can
tighten what a package asks for. Nothing works in the other direction.

That matters most for the untrusted end: `allowed_domains` and requested environment variables come
from the **package**, which is written by someone you do not know. Those declarations can only ever
reduce what your policy already permits. A package cannot widen an allowlist, raise a limit, lengthen
a retention or lower a required protection level.

## The settings that exist today

| key | ships as | accepts | what it does |
|---|---|---|---|
| `sandbox.host_trust_policy` | `curated_only` | `curated_only`, `default`, `none` | which trust tiers may run directly on the machine rather than in a sandbox |
| `agent_sandbox.enabled` | `true` | `true`, `false` | whether community agents run sandboxed; with `false` they are refused outright rather than run unprotected |
| `trust.minimum_trust_level` | see `agentnode config list` | `verified`, `trusted`, `curated` | the lowest trust tier that may be installed at all |

Read and change them with:

```
agentnode config list
agentnode config get sandbox.host_trust_policy
agentnode config set sandbox.host_trust_policy curated_only
```

### About `default`

`default` lets trusted third-party code run **directly on the machine**. It is a compatibility mode
from before the current rule and it is **not protected execution**. Nothing selects it
automatically, no onboarding offers it, and no screen calls it safe. Treat its presence in a fleet as
technical debt to remove, not as a configuration choice. See
[the security model](security-model.md#the-old-setting).

### What upgrading did

An on-disk value always wins over the shipped one, and the whole configuration file is written back
whenever anything changes. So anyone who had ever run `agentnode setup` or `agentnode config set`
kept their existing value across the 0.24.0 upgrade — which may well be `default`. Only a machine
with no configuration file at all picked up `curated_only`.

**Worth auditing across a fleet.** `agentnode config get sandbox.host_trust_policy` on each machine
answers it.

## Consent for anything remote

Not a setting today, because there is nothing remote to consent to yet. The rule the design commits
to, so you can plan around it:

consent is bound to the account, the operator, the backend identity, the classes of data, the region,
the retention terms and the version of the terms. Any material change asks again instead of quietly
reusing the old agreement. Shortening a retention does not require a new agreement; lengthening one
does. Withdrawal takes effect on the next job and cannot be overridden by a package, an agent or an
organisation default.

## What is not configurable, deliberately

* **No host fallback.** There is no setting that says "run it unprotected if the sandbox is
  unavailable". When the sandbox cannot be created, the job does not run.
* **No socket mounting.** The container-runtime socket is never mounted into a sandbox, and there is
  no flag for it.
* **No automatic host selection.** No automatic path, onboarding flow or fallback may choose host
  execution.

## Fleet-wide enforcement

Not implemented. There is no managed-settings channel in the current code, so today an administrator
configures machines by whatever mechanism they already use for configuration files. If central
enforcement matters to you, that is a useful thing to say now, while the shape is still open.

---

Next: [The security model](security-model.md) · [What actually works today](availability.md)
