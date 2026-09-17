"""Make a CI runner look like a machine somebody deployed.

Lanes that start a real gateway have to be pinned, because a start with no pin is a refusal --
that is the point of the pin. What a deployment does is install a wheel, record the digest it
installed INSIDE the installed distribution, and write a pin naming that same digest. This does
the same two things in the same order, so the lane exercises the pinned path rather than an
exception to it.

The alternative was `AGENTNODE_ALLOW_UNPINNED=1`, which would have made the lane test the one
arrangement the product does not have.
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

from agentnode_sdk.gateway import runtime_pin


def main() -> int:
    where = pathlib.Path(sys.argv[1])
    where.mkdir(parents=True, exist_ok=True)

    import agentnode_sdk

    package = pathlib.Path(agentnode_sdk.__file__).resolve().parent
    dist = next((d for d in package.parent.glob("agentnode_sdk-*.dist-info")), None)
    if dist is None:
        print("no dist-info beside", package, "-- nothing to record a digest in")
        return 1

    # A digest OF SOMETHING, rather than a constant: what matters for the lane is that the value
    # the pin names is the value the installation records, which is the relationship a deployment
    # establishes. The bytes digested here are the installed package's own RECORD file.
    record = dist / "RECORD"
    digest = hashlib.sha256(record.read_bytes() if record.is_file()
                            else package.as_posix().encode()).hexdigest()
    (dist / "AGENTNODE_ARTEFACT").write_text(digest + "\n", encoding="utf-8")

    said = runtime_pin.write_pin(where, python_version=runtime_pin.running_python(),
                                 artefact_sha256=digest, commit="0" * 40)
    print("recorded", digest[:16], "in", dist.name)
    print("pinned  ", said)

    # PROVE IT AGREES, here, rather than finding out when the gateway refuses to start.
    runtime_pin.check(where)
    print("and the pin agrees with what is installed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
