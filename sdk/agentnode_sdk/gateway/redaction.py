"""The last thing between a secret and a file somebody will read.

The design already avoids putting secrets in logs: tokens are stored hashed, the meter takes named
values and has no field for "anything else", the audit writes only values that came from the
contract. All of that is structural and all of it is better than this module. What none of it
covers is the accident -- an exception message that quotes a URL with a code in it, a refusal that
echoes what it was handed, a field somebody adds next year without reading why the others are
shaped the way they are.

So this is a **second** line and is described as one. It is not the reason secrets stay out of
logs, and a change that made it the reason would be a regression.

## What it looks for, and why not more

Only shapes this gateway actually issues:

    token_urlsafe(32)   43 characters      a device credential
    token_urlsafe(24)   32 characters      a session, a challenge, a download ticket
    XXXX-XXXX-XXXX      a pairing code, from its own restricted alphabet
    agentnode-invite-1. an invitation, which carries a code
    -----BEGIN ...      a private key of any kind
    token=, code=,      the named ways a secret travels in a URL or a header
    Bearer ...

## What it deliberately does NOT match

A run id is `uuid4().hex`: thirty-two characters, exactly the length of a session identifier. A
device id is sixteen hex characters. A digest is sixty-four. **All three must survive**, because
an audit in which the run ids have been replaced by `[redacted]` is an audit that cannot answer
anything, and somebody would then turn this off -- which is how a redaction pass becomes the
reason secrets reach logs.

The rule that separates them is that a secret from `token_urlsafe` is *not pure lowercase hex*:
it has an upper-case letter, a digit-and-letter mix with case, or a `-` or `_`. Anything that is
only `[0-9a-f]` is an identifier this gateway published on purpose and is left alone. That is a
heuristic and is written down as one; the structural rules above it are what actually keep
secrets out.

## Where it runs

At the boundaries where text becomes a file or an answer: the audit line, a refusal's words, the
event sink, and an export. Not on a job's output -- that belongs to the customer who produced it,
is handed only back to them, and is never written to an operator-facing log.
"""
from __future__ import annotations

import re

#: What replaces a match. One string, so a reader meeting it knows what happened rather than
#: wondering whether a value was truncated.
REDACTED = "[redacted]"

_PURE_HEX = re.compile(r"^[0-9a-f]+$")

#: A private key of any kind, including everything between the markers.
_PEM = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL)

#: An invitation. It carries a code, so the whole token goes.
_INVITATION = re.compile(r"agentnode-invite-1\.[A-Za-z0-9_-]+")

#: A pairing code, in its own alphabet, which excludes look-alike characters.
_PAIRING_CODE = re.compile(r"\b[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}"
                           r"-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}"
                           r"-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}\b")

#: The named ways a secret travels in a URL, a header or a setup file.
_NAMED = re.compile(
    r"((?:token|code|secret|password|passwd|api[_-]?key|authorization|csrf|ticket|challenge)"
    r"\s*[=:]\s*[\"']?)([^\s\"'&,;}]{8,})",
    re.IGNORECASE)

_BEARER = re.compile(r"(Bearer\s+)([A-Za-z0-9._~+/=-]{8,})", re.IGNORECASE)

#: Userinfo in a URL: https://someone:secret@host
_USERINFO = re.compile(r"(\b[a-z][a-z0-9+.-]*://)([^/\s:@]+:[^/\s@]+)(@)", re.IGNORECASE)

#: The two lengths this gateway's `token_urlsafe` calls produce. Bounded on both sides so a
#: longer run of the same characters -- a base64 body, a digest -- is not partly matched.
_TOKEN_SHAPED = re.compile(r"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{32}|[A-Za-z0-9_-]{43})"
                           r"(?![A-Za-z0-9_-])")


def looks_like_a_secret(value: str) -> bool:
    """Whether one whole value has the shape of something this gateway issued.

    For structured fields, where there is no surrounding text to anchor on. Pure lowercase hex
    is never a secret here: that is a run id, a device id or a digest, and all three are meant
    to be readable.
    """
    text = str(value or "")
    if _PEM.search(text) or _INVITATION.search(text) or _PAIRING_CODE.search(text):
        return True
    if len(text) not in (32, 43):
        return False
    if _PURE_HEX.match(text):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_-]+", text))


def _not_hex(match: re.Match) -> str:
    found = match.group(1)
    return REDACTED if not _PURE_HEX.match(found) else found


def scrub(text: str) -> str:
    """Replace anything shaped like a secret this gateway issues. Never raises."""
    said = str(text or "")
    if not said:
        return said
    said = _PEM.sub(REDACTED, said)
    said = _INVITATION.sub(REDACTED, said)
    said = _USERINFO.sub(lambda m: m.group(1) + REDACTED + m.group(3), said)
    said = _BEARER.sub(lambda m: m.group(1) + REDACTED, said)
    said = _NAMED.sub(lambda m: m.group(1) + REDACTED, said)
    said = _PAIRING_CODE.sub(REDACTED, said)
    said = _TOKEN_SHAPED.sub(_not_hex, said)
    return said


def scrub_everything(value):
    """The same, applied through a structure. Keys are scrubbed as well as values.

    A key can be a secret: a mapping keyed by token hash is a perfectly ordinary thing to have
    in this codebase, and one written out unscrubbed would put the interesting half in the field
    name where nobody thought to look.
    """
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {scrub(str(k)): scrub_everything(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_everything(v) for v in value]
    return value
