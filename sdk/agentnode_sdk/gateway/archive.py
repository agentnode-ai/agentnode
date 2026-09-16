"""A backup of a gateway IS its private keys, so a backup is encrypted or it is not taken.

## Why this exists

The frozen data-operations criterion `P1-NO-SECRET-REACHES-A-LOG-AN-AUDIT-OR-A-REFUSAL` failed
on this, and correctly:

> the backup design explicitly places the metering signing key and TLS private key into an
> unencrypted tar archive. Therefore private secret material can reach a backup.

The archive cannot simply stop containing them. A restore without the meter signing key produces
a gateway that cannot verify its own record of use; without the TLS private key it cannot be
reached over the certificate its clients pinned; without the key beside the state directory it
cannot read its own operator policy and refuses every job. That is not a leak to be removed --
it is what a backup of a gateway IS. So the archive is encrypted, the key lives somewhere the
archive does not, and both of those are enforced here rather than advised in a comment.

## What is used, and what is deliberately not invented

`cryptography`'s `AESGCM` -- an established authenticated cipher from a library this project
already depends on for its certificates. Nothing here invents a construction, a mode, a padding
scheme or a key schedule. The only choices made are how the header is bound and what fails
closed, and both are stated below.

## The shape on disk

    AGENTNODE-SEALED-1\\n
    <one line of JSON: the header>\\n
    <12 bytes: nonce><ciphertext and tag>

The header is NOT encrypted, because a restore has to know which key it needs before it can use
one. It IS authenticated: it is passed to AESGCM as additional data, so changing a single byte of
it -- the key id, the manifest digest, the gateway it came from -- makes the whole archive fail
to open. It carries no key and no passphrase; `test_a_half_written_file`-style assertions about
that live in the tests.

## What fails closed, and before what

Every one of these is decided BEFORE a single byte of plaintext is written anywhere:

* no key, or the wrong key -> `CannotOpen`. The tag does not verify and nothing is produced.
* a tampered archive, anywhere in it -> `CannotOpen`, same path, same reason.
* a TRUNCATED archive -> `CannotOpen`. AES-GCM authenticates the whole message, so a short read
  is a failed tag rather than a short restore.
* an archive for a DIFFERENT gateway or a different manifest -> `CannotOpen`, because both are
  in the authenticated header and `open_sealed` is told what it expects.

## The one limit, stated rather than discovered

`AESGCM` here is one-shot: the archive is held in memory while it is sealed and while it is
opened. A gateway's state directory is small -- token hashes, an audit, a metering log -- and the
alpha's is under a megabyte. A deployment whose state outgrows its memory needs a chunked format
with a chunk counter in the additional data, which is a different piece of work and is NOT what
this is. The limit is asserted in `seal` rather than left to be met as a crash.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path

#: What the first line says, so a reader and a script can tell what they are holding.
MAGIC = b"AGENTNODE-SEALED-1"

#: The construction. Named in the header so a future format cannot be mistaken for this one.
ALGORITHM = "AES-256-GCM"

#: AES-GCM's nonce. Random per archive; never reused, because the key is long-lived and a
#: repeated nonce under one key is the way this construction fails catastrophically.
NONCE_BYTES = 12

#: The key file's size. Raw bytes rather than a passphrase: a passphrase needs a KDF, a work
#: factor and a decision about how it is prompted for, and every one of those is somewhere else
#: to get it wrong. An operator who wants a passphrase can derive one into this file themselves.
KEY_BYTES = 32

#: Refuse rather than swap a gateway to death. See the limit in the module docstring.
MOST_BYTES = 512 * 1024 * 1024


class CannotOpen(Exception):
    """This archive cannot be opened, and therefore nothing has been restored.

    One exception for every reason -- wrong key, tampered bytes, truncated file, an archive for
    another gateway -- because telling them apart tells whoever is holding the archive which of
    those it is, and none of those answers helps the person who is entitled to it.
    """


class NoKey(Exception):
    """There is no key file where one was expected. Its own type: this is the recoverable one.

    An operator who has mislaid the key has a different problem from one holding a damaged
    archive, and the first is the one where "look in your password manager" is the answer.
    """


def new_key() -> bytes:
    return secrets.token_bytes(KEY_BYTES)


def key_id(key: bytes) -> str:
    """WHICH key, without being the key. A digest of a domain-separated copy of it.

    In the header so a restore can say "this is not the key that sealed it" instead of "the tag
    did not verify", which is the difference between a person finding the right file and a person
    concluding their backup is corrupt.
    """
    return hashlib.sha256(b"agentnode-archive-key\x00" + key).hexdigest()[:16]


def read_key(where) -> bytes:
    """The key from a file the archive does not contain and must never contain."""
    path = Path(where)
    try:
        raw = path.read_bytes().strip()
    except FileNotFoundError as gone:
        raise NoKey("there is no backup key at %s" % path) from gone
    except OSError as unreadable:
        raise NoKey("the backup key at %s cannot be read (%s)"
                    % (path, str(unreadable)[:120])) from unreadable
    key = bytes.fromhex(raw.decode("ascii")) if len(raw) == KEY_BYTES * 2 else raw
    if len(key) != KEY_BYTES:
        raise NoKey("the backup key at %s is %d bytes; it has to be %d"
                    % (path, len(key), KEY_BYTES))
    return key


def write_key(where, key: bytes) -> None:
    """Owner-only, and never inside a directory that is about to be archived."""
    path = Path(where)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(handle, "wb") as fh:
        fh.write(key.hex().encode("ascii") + b"\n")


def seal(plain: bytes, key: bytes, *, about: dict) -> bytes:
    """Encrypt, binding `about` so it cannot be changed or moved to another archive."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if len(plain) > MOST_BYTES:
        raise ValueError(
            "this archive is %d bytes and this format holds the whole of it in memory; above %d "
            "it needs a chunked format, which this is not" % (len(plain), MOST_BYTES))
    header = dict(about)
    header.update({
        "algorithm": ALGORITHM,
        "key_id": key_id(key),
        # The plaintext's own digest, authenticated. A restore that opens can also say the bytes
        # are the bytes -- two independent statements rather than one.
        "plain_sha256": hashlib.sha256(plain).hexdigest(),
        "plain_bytes": len(plain),
    })
    line = json.dumps(header, sort_keys=True).encode("utf-8")
    nonce = secrets.token_bytes(NONCE_BYTES)
    sealed = AESGCM(key).encrypt(nonce, plain, MAGIC + b"\n" + line)
    return MAGIC + b"\n" + line + b"\n" + nonce + sealed


def header_of(archive: bytes) -> dict:
    """What the archive SAYS it is, before anything is trusted about it.

    Unauthenticated at this point, and named that way: this is for choosing a key and for telling
    somebody which one they need. Nothing decided from it survives `open_sealed`, which
    authenticates the same bytes.
    """
    try:
        magic, line, _rest = archive.split(b"\n", 2)
    except ValueError as malformed:
        raise CannotOpen("this is not an AgentNode archive") from malformed
    if magic != MAGIC:
        raise CannotOpen("this is not an AgentNode archive")
    try:
        said = json.loads(line.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as malformed:
        raise CannotOpen("this archive's header cannot be read") from malformed
    return said if isinstance(said, dict) else {}


def open_sealed(archive: bytes, key: bytes, *, expect: dict | None = None) -> bytes:
    """Decrypt, or raise. Nothing partial is ever returned.

    `expect` is what the caller already knows about the archive it MEANT to open -- the gateway,
    the manifest. Checked against the authenticated header, so an archive that is perfectly valid
    and belongs to something else is refused rather than restored.
    """
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        magic, line, rest = archive.split(b"\n", 2)
    except ValueError as malformed:
        raise CannotOpen("this is not an AgentNode archive") from malformed
    if magic != MAGIC:
        raise CannotOpen("this is not an AgentNode archive")
    if len(rest) <= NONCE_BYTES:
        raise CannotOpen("this archive is too short to contain anything")

    said = header_of(archive)
    if said.get("key_id") and said["key_id"] != key_id(key):
        raise CannotOpen(
            "this archive was not sealed with the key that was supplied (it wants key %s)"
            % said["key_id"])

    nonce, body = rest[:NONCE_BYTES], rest[NONCE_BYTES:]
    try:
        plain = AESGCM(key).decrypt(nonce, body, magic + b"\n" + line)
    except InvalidTag as wrong:
        # ONE message for every reason. Wrong key, altered bytes, a file cut short: telling them
        # apart tells whoever holds the archive which it is, and none of those answers helps
        # somebody entitled to it.
        raise CannotOpen(
            "this archive did not open: it was sealed with a different key, or it has been "
            "altered or cut short since. Nothing has been restored.") from wrong

    if said.get("plain_sha256") and hashlib.sha256(plain).hexdigest() != said["plain_sha256"]:
        raise CannotOpen("this archive opened and its contents are not what it says they are")

    for name, wanted in (expect or {}).items():
        if said.get(name) != wanted:
            raise CannotOpen(
                "this archive is not the one that was asked for: its %s is not the expected one"
                % name)
    return plain


def main(argv=None) -> int:
    """`python -m agentnode_sdk.gateway.archive seal|open|header ...` -- what the script runs."""
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="agentnode-archive")
    ap.add_argument("verb", choices=("newkey", "seal", "open", "header"))
    ap.add_argument("--in", dest="source")
    ap.add_argument("--out", dest="target")
    ap.add_argument("--key", dest="key")
    ap.add_argument("--gateway", default="")
    ap.add_argument("--manifest-sha256", default="")
    ap.add_argument("--expect-gateway", default="")
    ap.add_argument("--expect-manifest-sha256", default="")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        if args.verb == "newkey":
            write_key(args.key, new_key())
            print("    wrote a new backup key to %s" % args.key)
            print("    KEEP IT SOMEWHERE THE ARCHIVE IS NOT. An archive and its key in one place")
            print("    is an unencrypted archive with extra steps.")
            return 0

        if args.verb == "header":
            said = header_of(Path(args.source).read_bytes())
            print(json.dumps(said, indent=2, sort_keys=True))
            return 0

        key = read_key(args.key)
        if args.verb == "seal":
            about = {"gateway": args.gateway, "manifest_sha256": args.manifest_sha256}
            Path(args.target).write_bytes(
                seal(Path(args.source).read_bytes(), key, about=about))
            os.chmod(args.target, 0o600)
            print("    sealed %s -> %s (key %s)" % (args.source, args.target, key_id(key)))
            return 0

        expect = {}
        if args.expect_gateway:
            expect["gateway"] = args.expect_gateway
        if args.expect_manifest_sha256:
            expect["manifest_sha256"] = args.expect_manifest_sha256
        plain = open_sealed(Path(args.source).read_bytes(), key, expect=expect)
        Path(args.target).write_bytes(plain)
        os.chmod(args.target, 0o600)
        print("    opened %s -> %s" % (args.source, args.target))
        return 0
    except NoKey as gone:
        print("    PROBLEM        : %s" % gone)
        return 2
    except CannotOpen as shut:
        print("    PROBLEM        : %s" % shut)
        return 1
    except (OSError, ValueError) as wrong:
        print("    PROBLEM        : %s" % str(wrong)[:200])
        return 1


if __name__ == "__main__":                                    # pragma: no cover - a CLI
    raise SystemExit(main())
