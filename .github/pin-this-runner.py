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

    # FOUND THE WAY THE PIN LATER READS IT -- through importlib.metadata, not by looking next
    # to the package. A lane that installs editable keeps its dist-info somewhere else
    # entirely, and the first version of this went looking beside the source tree, found
    # nothing, and said so.
    import importlib.metadata as md

    try:
        dist = md.distribution("agentnode-sdk")
    except md.PackageNotFoundError:
        print("agentnode-sdk is not installed here, so there is nothing to record a digest in")
        return 1
    meta = getattr(dist, "_path", None)
    if meta is None or not pathlib.Path(meta).is_dir():
        print("the distribution has no directory to record a digest in:", meta)
        return 1
    meta = pathlib.Path(meta)

    # A digest OF SOMETHING rather than a constant: what matters is that the value the pin
    # names is the value the installation records, which is the relationship a deployment
    # establishes. The bytes digested are the installed distribution's own RECORD.
    record = meta / "RECORD"
    digest = hashlib.sha256(record.read_bytes() if record.is_file()
                            else meta.as_posix().encode()).hexdigest()
    (meta / "AGENTNODE_ARTEFACT").write_text(digest + "\n", encoding="utf-8")

    said = runtime_pin.write_pin(where, python_version=runtime_pin.running_python(),
                                 artefact_sha256=digest, commit="0" * 40)
    print("recorded", digest[:16], "in", meta.name)
    print("pinned  ", said)

    # PROVE IT AGREES, here, rather than finding out when the gateway refuses to start.
    runtime_pin.check(where)
    print("and the pin agrees with what is installed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
