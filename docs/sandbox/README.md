# The sandbox documentation

Other people's code runs somewhere it cannot reach your things — or it does not run. That is the
whole idea; the rest is detail.

## Start here

| | for whom | how long |
|---|---|---|
| **[The sandbox in two minutes](understand.md)** | everyone | 2 min |
| **[Which sandbox suits you](choose.md)** | everyone | 3 min |
| **[Set up the local sandbox](setup-local.md)** | anyone on a computer | 10 min, mostly waiting |
| **[What actually works today](availability.md)** | anyone deciding what to rely on | 2 min |

## When something goes wrong

**[When something does not work](troubleshooting.md)** — every entry says what happened, what
AgentNode stopped, and one thing you can do.

## The other two ways to run

| | status |
|---|---|
| **[On a phone or tablet](mobile.md)** | 🔭 planned — nothing runs on the phone itself, ever |
| **[Your own sandbox server](self-hosted.md)** | 🔭 planned — design documentation for operators |
| **[The AgentNode Sandbox](managed.md)** | 🔭 planned — not bookable, not priced |

## For engineers and administrators

* **[The security model, and where it stops](security-model.md)** — what the sealed workspace (a
  *container*) actually does, what
  the policy layer is *not*, container versus virtual machine, the network limit, secrets, and the
  old compatibility setting.
* **[Administration and policy](admin-policy.md)** — the settings that exist, what upgrading did,
  and what is deliberately not configurable.
* **[The setup flow, as a product contract](setup-contract.md)** — what the interface must do when
  it is built.

## How to trust this documentation

[`availability.md`](availability.md) is **generated** from the SDK source by
[`_checks/build_matrix.py`](_checks/build_matrix.py), reading facts extracted by
[`_checks/extract_facts.py`](_checks/extract_facts.py). Every status carries the fact key or the test
evidence that produced it.

[`_checks/check_docs.py`](_checks/check_docs.py) then reads these pages back and fails if a command
appears that the CLI does not have, if a link points nowhere, if the matrix has drifted from the
code, or if a beginner page uses a technical word it never explains.

Two things this documentation does not claim:

1. **Only Linux has been tested.** Everything else is a reasonable expectation from the code, and an
   expectation is not a result.
2. **Nobody outside the project has tried the setup.** The ten-person test is prepared and has not
   been run. Until it has, these pages describe the steps rather than calling them easy.
