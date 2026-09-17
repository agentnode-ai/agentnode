#!/usr/bin/env bash
#
# Back this gateway up, and put it back.
#
# Written as ONE script with two verbs on purpose. A backup procedure and a restore procedure
# kept apart drift apart, and the one that drifts is always the restore -- because the backup
# runs every night and the restore runs once, badly, at the worst possible moment.
#
#   backup-and-restore.sh backup  [--dir /var/lib/agentnode/state] [--to /root/agentnode-backups]
#   backup-and-restore.sh restore --from /root/agentnode-backups/<stamp>
#   backup-and-restore.sh check   --from /root/agentnode-backups/<stamp>
#
# ## What is backed up, and what is deliberately not
#
# The gateway's state directory, whole: its identity, its token hashes, its accounts, its
# ceilings, its ledger, its metering chain and head, its conformance report, its counters.
#
# NOT the venv and NOT the units. Those come from the deployment, are reproducible from it, and
# restoring a venv from a tarball is how a machine ends up running software nobody can name. If
# the code needs putting back, re-run single-host-development.sh with the wheel you want.
#
# ## THIS ARCHIVE CONTAINS SECRETS, AND IS THEREFORE SEALED
#
# Said here because it was nowhere stated and a review was right to refuse it on that. The state
# directory holds the METER SIGNING KEY and the gateway's TLS PRIVATE KEY, and it has to: a
# restore without them produces a gateway that cannot verify its own record of use and cannot be
# reached over the certificate its clients pinned. That is not a leak to be fixed; it is what a
# backup of a gateway IS.
#
# So it is ENCRYPTED, with AES-256-GCM from the `cryptography` library this project already
# depends on for its certificates. `P1-NO-SECRET-REACHES-A-LOG-AN-AUDIT-OR-A-REFUSAL` failed on
# the unencrypted version and was right to: the archive cannot stop containing those keys, so it
# has to stop being readable without one more.
#
# Three things follow, and the first two are enforced here rather than advised:
#
#   * THE KEY LIVES WHERE THE ARCHIVE DOES NOT. This script refuses to write an archive into a
#     directory that contains its key, and refuses a key inside the state directory it is about
#     to back up. An archive and its key in one place is an unencrypted archive with extra steps;
#   * the archive is written 0600 into a 0700 directory, and this script REFUSES if it cannot
#     make that true;
#   * anywhere you copy it to inherits that requirement and this script cannot enforce it there.
#     An archive on a share, in object storage or attached to a ticket is still this gateway's
#     state in that place -- sealed, and only as strong as where the key is;
#   * a backup taken before a customer was deleted still contains that customer. Deletion cannot
#     reach a file it does not have. The schedule on which you retire old backups IS your
#     retention policy for backups, and nothing else is.
#
# ## Why the check is part of it
#
# A restore that produced a gateway which *looks* fine is the failure mode worth designing
# against. So `check` asks the six questions that would actually be wrong:
#
#   1. does the metering chain still verify, end to end and to its head
#   2. does the gateway still know its own identity, and is it the SAME identity
#   3. are the devices still there, and the accounts still attached to them
#   4. CAN A GATEWAY BUILT ON THIS STATE READ ITS OWN OPERATOR POLICY
#   5. are the permissions still owner-only
#   6. DID EVERY STORE COME BACK THE WAY IT WENT IN
#
# The fourth was added after a drill: the first four-question version passed on a restore whose
# gateway then refused every job, because the key authenticating the policy was not in the
# archive. Checking files is not checking that a gateway made of them works.
#
# The sixth was added after a review, and it is the one the other five cannot substitute for.
# All of them ask whether the copy looks plausible, and a copy on its own has nothing to
# disagree with: a restore that came back with half the accounts, no sessions, an empty audit
# and no tombstones passes every one. So a backup now writes down WHAT IT CONTAINED -- one
# entry per store, with how many records and a digest over what identifies them -- and the check
# recomputes it and names each store that differs. The list of stores is not kept in this file:
# it is `retention.CLASSES` plus `backup.BESIDES`, so a store added to the product is in the
# drill the same day.
#
# Records are summarised by what identifies them. KEYS AND CERTIFICATES ARE NOT: for those the
# manifest records that the file is there and how long it is, never a digest of its content. A
# manifest travels with an archive and sometimes out of it, and "we only stored a hash of the
# signing key" is the sentence that comes just before that being a problem.
#
# `restore` runs `check` afterwards and fails if any of them does. A restore that cannot answer
# them is reported as a failed restore, not as a restore with a warning.
#
# ## The one property a restore cannot give back
#
# A backup is a point in time. Runs that happened after it are not in the restored metering
# record, and the chain will verify perfectly without them, because a chain proves nothing was
# ALTERED and cannot prove nothing is missing from before it was copied. That is stated here
# rather than discovered: if the gap matters, the head file from the live machine is what shows
# how long the log was supposed to be.

set -euo pipefail

STATE_DIR="/var/lib/agentnode/state"
# The key that authenticates the operator policy lives BESIDE the state directory rather than
# inside it, on purpose. A backup has to take both, and until a drill started the restored
# gateway nothing noticed that this one did not.
BACKUP_ROOT="/root/agentnode-backups"
# Where the key is. NOT under BACKUP_ROOT, and this script checks rather than trusts.
KEY="${AGENTNODE_BACKUP_KEY:-/root/.agentnode-backup.key}"
FROM=""
VERB="${1:-}"
shift || true

while [ $# -gt 0 ]; do
  case "$1" in
    --dir)  STATE_DIR="$2"; shift 2 ;;
    --to)   BACKUP_ROOT="$2"; shift 2 ;;
    --from) FROM="$2"; shift 2 ;;
    --key)  KEY="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

SECRET_DIR="${STATE_DIR}.secret"

say() { printf '  %s\n' "$*"; }

# The key must not be inside anything this script is about to archive, and must not be inside the
# directory the archive is written to. Both are the same mistake -- a key that travels with its
# ciphertext -- and both are refused rather than warned about.
key_is_somewhere_else() {
  local key_dir
  key_dir="$(cd "$(dirname "$KEY")" 2>/dev/null && pwd || echo "$(dirname "$KEY")")"
  case "$key_dir/" in
    "$(cd "$BACKUP_ROOT" 2>/dev/null && pwd || echo "$BACKUP_ROOT")"/*)
      die "the backup key is inside $BACKUP_ROOT. An archive and its key in one place is an
  unencrypted archive with extra steps. Move the key, or pass --key." ;;
  esac
  case "$key_dir/" in
    "$(cd "$STATE_DIR" 2>/dev/null && pwd || echo "$STATE_DIR")"/*|"$(cd "$SECRET_DIR" 2>/dev/null && pwd || echo "$SECRET_DIR")"/*)
      die "the backup key is inside the state this script is about to back up, so it would be
  sealed inside the archive it unlocks. Move it, or pass --key." ;;
  esac
}
die() { printf '\n  FAILED: %s\n\n' "$*" >&2; exit 1; }

# Whichever python has the SDK. On the deployed host that is the gateway's own venv.
# The pinned environment by default. It used to default to /opt/agentnode/venv, which on a
# machine that has moved to a pinned interpreter is the PREVIOUS installation -- so a manifest
# check ran against code that did not know about the files the current build writes.
PY="${AGENTNODE_PYTHON:-${AGENTNODE_VENV:-/opt/agentnode/venv312}/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3 || true)"
[ -n "$PY" ] || die "no python with the agentnode SDK on it (set AGENTNODE_PYTHON)"

check_state() {
  local where="$1"
  local manifest="${2:-}"
  local failed=0

  say "checking $where"

  "$PY" - "$where" <<'PYEOF' || failed=1
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
bad = []

from agentnode_sdk.gateway import meter

held = meter.verify(root)
if not held.get("ok"):
    bad.append("the metering record does not verify: " + str(held.get("detail"))[:160])
else:
    print("    metering       : %d line(s) verify; %d erased on request"
          % (held.get("lines", 0), held.get("erased", 0)))

identity = root / "identity.json"
if not identity.is_file():
    bad.append("there is no identity.json, so this gateway does not know who it is")
else:
    print("    identity       : %s" % json.loads(identity.read_text())["gateway_id"][:16])

tokens = root / "tokens.json"
if tokens.is_file():
    held_tokens = json.loads(tokens.read_text() or "{}")
    accounts = {v.get("account_id") or ("solo:" + str(v.get("client_id")))
                for v in held_tokens.values()}
    print("    devices        : %d in %d account(s)" % (len(held_tokens), len(accounts)))
    if held_tokens and not accounts:
        bad.append("devices came back with no account attached to any of them")
else:
    print("    devices        : none")

for line in bad:
    print("    PROBLEM        : " + line)
sys.exit(1 if bad else 0)
PYEOF

  # THE FIFTH QUESTION, and the one that caught a real defect: is the key that authenticates
  # the operator policy in this archive at all? It lives BESIDE the state directory on purpose,
  # so a backup of the state alone restores to a gateway that refuses every job -- correctly,
  # because it cannot tell whether its own policy was changed by something else.
  #
  # Asked as "is it here", not as "does a gateway built on this copy read its policy". That
  # second question needs a WORKER, because reading the active state validates it against the
  # worker's configuration, and a copy in a temporary directory has no worker and should not be
  # given one. `restore` asks the full question on the real path, where there is one.
  if [ -f "$where/../.secret-present" ] || [ -d "${where}.secret" ]; then
    say "    policy key     : in the archive"
  else
    say "    PROBLEM        : the key that authenticates the operator policy is NOT in this"
    say "                     archive. Restoring it produces a gateway that refuses every job."
    failed=1
  fi

  # THE SIXTH QUESTION: did every store come back the way it went in? Everything above asks
  # whether this copy looks all right; this is the only one that asks whether it is the SAME.
  # A backup taken before this existed has no manifest, and that is reported as what it is --
  # an unverifiable restore -- rather than passed over.
  if [ -n "$manifest" ] && [ -f "$manifest" ]; then
    "$PY" -m agentnode_sdk.gateway.backup compare "$where" "$manifest" || failed=1
  elif [ -n "$manifest" ]; then
    say "    PROBLEM        : this backup carries no $(basename "$manifest"), so there is"
    say "                     nothing to compare the restored state against. It was taken"
    say "                     before the drill recorded what it contained; take a fresh one."
    failed=1
  fi

  # Owner-only. A restore that widened the permissions is a restore that handed this machine's
  # other accounts a list of credentials.
  if [ "$(stat -c '%a' "$where")" != "700" ]; then
    say "    PROBLEM        : $where is $(stat -c '%a' "$where"), not 700"
    failed=1
  else
    say "    permissions    : 700"
  fi

  return $failed
}

# Open a sealed archive, or STOP. Every reason it can fail -- no key, the wrong key, altered
# bytes, a file cut short, an archive belonging to something else -- ends here, before a single
# byte of it has been written anywhere a gateway would read.
open_the_archive() {
  "$PY" -m agentnode_sdk.gateway.archive open --in "$1" --out "$2" --key "$KEY" \
    || die "that archive did not open. Nothing has been restored and the existing state is
  untouched. Either the key at $KEY is not the one it was sealed with, or the archive has
  been altered or cut short since it was written."
  chmod 600 "$2"
}

case "$VERB" in
  newkey)
    key_is_somewhere_else
    "$PY" -m agentnode_sdk.gateway.archive newkey --key "$KEY" || exit 1
    echo
    say "This key is the ONLY thing that opens the archives it seals. A backup whose key is"
    say "lost is not a backup. Put it where your other credentials live -- NOT beside the"
    say "archives, and NOT inside $STATE_DIR."
    echo
    ;;

  backup)
    [ -d "$STATE_DIR" ] || die "no state directory at $STATE_DIR"
    STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
    WHERE="$BACKUP_ROOT/$STAMP"
    mkdir -p "$WHERE"
    chmod 700 "$BACKUP_ROOT" "$WHERE"
    # Refuse rather than warn. This archive is about to contain the gateway's meter signing key
    # and its TLS private key; writing it somewhere other accounts can read is not a note to put
    # in the output, it is a reason not to write it at all.
    if [ "$(stat -c '%a' "$BACKUP_ROOT")" != "700" ] || [ "$(stat -c '%a' "$WHERE")" != "700" ]; then
      die "$BACKUP_ROOT is not owner-only, and this archive holds this gateway's private keys"
    fi
    echo
    say "backing up $STATE_DIR"
    say "NOTE: this archive contains the meter signing key, the TLS private key AND the key that"
    say "      authenticates the operator policy. Wherever you copy it, it is those keys there."
    key_is_somewhere_else
    [ -f "$KEY" ] || die "there is no backup key at $KEY. Make one first:
  $0 newkey --key $KEY
  and keep it somewhere this archive is not."
    tar -C "$(dirname "$STATE_DIR")" -cf "$WHERE/state.tar" "$(basename "$STATE_DIR")"
    # AND the key directory beside it. This was missing, and the first drill that actually
    # STARTED the restored gateway is what found it: the tag authenticating the operator policy
    # is keyed from a file kept deliberately OUTSIDE the state directory, so a backup of the
    # state alone restores to a gateway that refuses every job -- correctly, because it cannot
    # tell whether its own policy was changed by something else. A backup that restores to a
    # gateway which will not run anything is not a backup.
    if [ -d "$SECRET_DIR" ]; then
      tar -C "$(dirname "$SECRET_DIR")" -cf "$WHERE/secret.tar" "$(basename "$SECRET_DIR")"
      say "     and $SECRET_DIR, without which the restored gateway cannot read its own policy"
    else
      say "     (no $SECRET_DIR on this gateway; nothing to take)"
    fi

    # WHAT IT CONTAINED, store by store, so a restore has something to disagree with. Written
    # from the LIVE directory rather than from the archive: the point is to record what was
    # there at the moment of taking it.
    "$PY" -m agentnode_sdk.gateway.backup write "$STATE_DIR" "$WHERE/WHAT_IS_IN_IT.json"
    # Digested with the tarballs, so a manifest edited afterwards to match a damaged restore
    # fails the same digest check the archive does.
    # SEALED, and the plaintext tars removed. The manifest digest goes into the archive's
    # authenticated header, so an archive cannot be re-pointed at another gateway's manifest and
    # cannot have its own swapped: changing one byte of that header breaks the whole thing.
    MANIFEST_SHA="$(sha256sum "$WHERE/WHAT_IS_IN_IT.json" | cut -d' ' -f1)"
    GATEWAY_ID="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1]))['gateway_id'])" \
                  "$STATE_DIR/identity.json" 2>/dev/null || echo "")"
    for what in state secret; do
      [ -f "$WHERE/$what.tar" ] || continue
      "$PY" -m agentnode_sdk.gateway.archive seal \
        --in "$WHERE/$what.tar" --out "$WHERE/$what.tar.sealed" --key "$KEY" \
        --gateway "$GATEWAY_ID" --manifest-sha256 "$MANIFEST_SHA" \
        || die "the archive could not be sealed, so nothing was left behind"
      shred -u "$WHERE/$what.tar" 2>/dev/null || rm -f "$WHERE/$what.tar"
    done
    say "sealed with the key at $KEY -- WITHOUT IT THIS ARCHIVE IS NOTHING"

    if [ -f "$WHERE/secret.tar.sealed" ]; then
      ( cd "$WHERE" && sha256sum state.tar.sealed secret.tar.sealed WHAT_IS_IN_IT.json \
        > SHA256SUMS )
    else
      ( cd "$WHERE" && sha256sum state.tar.sealed WHAT_IS_IN_IT.json > SHA256SUMS )
    fi


    # A store this gateway keeps that the drill does not know how to check is a store whose loss
    # a restore would report as nothing. The ARCHIVE IS STILL WRITTEN -- refusing to back a
    # gateway up because somebody added a file is the wrong failure -- but this exits non-zero
    # so a schedule notices, and `check` will refuse it.
    UNCHECKED=0
    "$PY" -m agentnode_sdk.gateway.backup unaccounted "$STATE_DIR" || UNCHECKED=1
    chmod 600 "$WHERE"/*
    say "wrote $WHERE/state.tar.sealed"
    say "     $(cat "$WHERE/SHA256SUMS")"
    echo
    say "to put it back:  $0 restore --from $WHERE"
    echo
    if [ "${UNCHECKED:-0}" != "0" ]; then
      say "The archive was written. It is NOT fully checkable: see the PROBLEM line above."
      echo
      exit 1
    fi
    ;;

  check)
    [ -n "$FROM" ] || die "check needs --from <backup directory>"
    echo
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    ( cd "$FROM" && sha256sum -c SHA256SUMS >/dev/null ) || die "the backup does not match its own SHA256SUMS"
    say "the archive matches its own digests"
    open_the_archive "$FROM/state.tar.sealed" "$TMP/state.tar"
    tar -C "$TMP" -xf "$TMP/state.tar"
    if [ -f "$FROM/secret.tar.sealed" ]; then
      open_the_archive "$FROM/secret.tar.sealed" "$TMP/secret.tar"
      tar -C "$TMP" -xf "$TMP/secret.tar"
    fi
    rm -f "$TMP"/*.tar
    INNER="$TMP/$(ls "$TMP" | head -1)"
    # The archive carries the gateway account's ownership. Checking a copy means reading it as
    # WHOEVER IS CHECKING, and the gateway refuses to touch a state directory owned by somebody
    # else -- correctly, since that is somebody who could change it mid-read. So the copy is
    # taken over for the duration of the check. The live directory is untouched.
    chown -R "$(id -u):$(id -g)" "$TMP"
    chmod 700 "$INNER"
    check_state "$INNER" "$FROM/WHAT_IS_IN_IT.json" \
      || die "the backup does not restore to a gateway that checks out"
    echo
    say "this backup restores to a gateway that checks out."
    echo
    ;;

  restore)
    [ -n "$FROM" ] || die "restore needs --from <backup directory>"
    echo
    ( cd "$FROM" && sha256sum -c SHA256SUMS >/dev/null ) || die "the backup does not match its own SHA256SUMS"
    say "the archive matches its own digests"

    # The live directory is moved aside rather than deleted. A restore is performed by somebody
    # who is already having a bad day, and "it made things worse and there is no way back" is
    # the outcome to design against.
    if [ -d "$STATE_DIR" ]; then
      ASIDE="$STATE_DIR.replaced-$(date -u +%Y%m%dT%H%M%SZ)"
      mv "$STATE_DIR" "$ASIDE"
      say "moved the existing state to $ASIDE (nothing was deleted)"
    fi
    OPENED="$(mktemp -d)"
    trap 'rm -rf "$OPENED"' EXIT
    open_the_archive "$FROM/state.tar.sealed" "$OPENED/state.tar"
    # THE RECEIVING MACHINE KEEPS ITS OWN PIN. A backup carries the sending machine's idea of
    # which interpreter, artefact and commit it is allowed to run as, and restoring that here
    # would tell this machine it is something it is not -- after which its own start would
    # refuse, correctly, for a reason nobody introduced on purpose. The customer data is
    # restored; the installation's identity is not.
    KEEP_PIN=""
    if [ -f "$STATE_DIR/runtime-pin.json" ]; then
      KEEP_PIN="$(mktemp)"
      cp "$STATE_DIR/runtime-pin.json" "$KEEP_PIN"
    fi
    tar -C "$(dirname "$STATE_DIR")" -xf "$OPENED/state.tar"         --exclude='*/runtime-pin.json' --exclude='runtime-pin.json'
    if [ -n "$KEEP_PIN" ]; then
      cp "$KEEP_PIN" "$STATE_DIR/runtime-pin.json"
      chown agentnode-gateway "$STATE_DIR/runtime-pin.json" 2>/dev/null || true
      rm -f "$KEEP_PIN"
      echo "   this machine kept its own runtime pin; the archive's was not restored"
    fi
    chmod 700 "$STATE_DIR"
    if [ -f "$FROM/secret.tar.sealed" ]; then
      open_the_archive "$FROM/secret.tar.sealed" "$OPENED/secret.tar"
      rm -rf "$SECRET_DIR"
      tar -C "$(dirname "$SECRET_DIR")" -xf "$OPENED/secret.tar"
      chmod 700 "$SECRET_DIR"
    fi
    rm -rf "$OPENED"
    if id agentnode-gateway >/dev/null 2>&1; then
      chown -R agentnode-gateway:agentnode-gateway "$STATE_DIR"
      [ -d "$SECRET_DIR" ] && chown -R agentnode-gateway:agentnode-gateway "$SECRET_DIR"
    fi
    say "restored $STATE_DIR"

    check_state "$STATE_DIR" "$FROM/WHAT_IS_IN_IT.json" \
      || die "restored, and it does not check out. The previous state is still at $ASIDE"

    # And the question a copy could not answer: does a gateway on THIS state, on this machine,
    # with this machine's worker, read its own operator policy? A restore that leaves a gateway
    # refusing every job is a failed restore, not a restore with a note.
    say "asking whether a gateway on it reads its own policy"
    runuser -u agentnode-gateway -- env HOME=/var/lib/agentnode "$PY" - "$STATE_DIR" <<'READYEOF' || die "restored, and a gateway on it cannot read its own operator policy. The previous state is still at $ASIDE"
import sys
from pathlib import Path

from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService

state = GatewayState(str(Path(sys.argv[1])), version="restore-check")
try:
    service = GatewayService(state)
    service.active_state()
    print("    reads its policy: yes (%s)" % service.operator_envelope().mode)
except Exception as exc:                                      # noqa: BLE001
    said = str(exc)
    if "authentication tag" in said or "generation" in said:
        print("    PROBLEM        : it cannot. The key that authenticates the operator policy "
              "lives BESIDE the state directory; this archive did not carry it.")
    else:
        print("    PROBLEM        : the operator policy cannot be read (%s)" % said[:140])
    sys.exit(1)
finally:
    state.close()
READYEOF
    echo
    say "restored, and it checks out."
    say "restart the gateway:  systemctl restart agentnode-gateway"
    echo
    ;;

  *)
    cat <<'USAGE'

  backup-and-restore.sh newkey  [--key <file>]
  backup-and-restore.sh backup  [--dir <state>] [--to <backups>] [--key <file>]
  backup-and-restore.sh restore --from <backup directory>
  backup-and-restore.sh check   --from <backup directory>

  `check` restores into a temporary directory and asks the six questions that would
  actually be wrong, without touching the live gateway -- including whether every
  store came back the way it went in, which is the one the others cannot stand in
  for. Run it after every backup; a backup nobody has restored is a hope, not a
  backup.

USAGE
    exit 2
    ;;
esac
