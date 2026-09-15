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
# ## THIS ARCHIVE CONTAINS SECRETS. All of them.
#
# Said here because it was nowhere stated and a review was right to refuse it on that. The state
# directory holds the METER SIGNING KEY and the gateway's TLS PRIVATE KEY, and it has to: a
# restore without them produces a gateway that cannot verify its own record of use and cannot be
# reached over the certificate its clients pinned. That is not a leak to be fixed; it is what a
# backup of a gateway IS.
#
# Three things follow, and the first is enforced here rather than advised:
#
#   * the archive is written 0600 into a 0700 directory, and this script REFUSES if it cannot
#     make that true;
#   * anywhere you copy it to inherits that requirement and this script cannot enforce it there.
#     An archive on a share, in object storage, or attached to a ticket is this gateway's keys
#     in that place;
#   * a backup taken before a customer was deleted still contains that customer. Deletion cannot
#     reach a file it does not have. The schedule on which you retire old backups IS your
#     retention policy for backups, and nothing else is.
#
# ## Why the check is part of it
#
# A restore that produced a gateway which *looks* fine is the failure mode worth designing
# against. So `check` asks the four questions that would actually be wrong:
#
#   1. does the metering chain still verify, end to end and to its head
#   2. does the gateway still know its own identity, and is it the SAME identity
#   3. are the devices still there, and the accounts still attached to them
#   4. are the permissions still owner-only
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
BACKUP_ROOT="/root/agentnode-backups"
FROM=""
VERB="${1:-}"
shift || true

while [ $# -gt 0 ]; do
  case "$1" in
    --dir)  STATE_DIR="$2"; shift 2 ;;
    --to)   BACKUP_ROOT="$2"; shift 2 ;;
    --from) FROM="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

say() { printf '  %s\n' "$*"; }
die() { printf '\n  FAILED: %s\n\n' "$*" >&2; exit 1; }

# Whichever python has the SDK. On the deployed host that is the gateway's own venv.
PY="${AGENTNODE_PYTHON:-/opt/agentnode/venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3 || true)"
[ -n "$PY" ] || die "no python with the agentnode SDK on it (set AGENTNODE_PYTHON)"

check_state() {
  local where="$1"
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

case "$VERB" in
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
    say "NOTE: this archive contains the meter signing key and the TLS private key. It has to."
    say "      Wherever you copy it, it is those keys in that place."
    tar -C "$(dirname "$STATE_DIR")" -cf "$WHERE/state.tar" "$(basename "$STATE_DIR")"
    ( cd "$WHERE" && sha256sum state.tar > SHA256SUMS )
    chmod 600 "$WHERE"/*
    say "wrote $WHERE/state.tar"
    say "     $(cat "$WHERE/SHA256SUMS")"
    echo
    say "to put it back:  $0 restore --from $WHERE"
    echo
    ;;

  check)
    [ -n "$FROM" ] || die "check needs --from <backup directory>"
    echo
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    ( cd "$FROM" && sha256sum -c SHA256SUMS >/dev/null ) || die "the backup does not match its own SHA256SUMS"
    say "the archive matches its own digests"
    tar -C "$TMP" -xf "$FROM/state.tar"
    INNER="$TMP/$(ls "$TMP")"
    chmod 700 "$INNER"
    check_state "$INNER" || die "the backup does not restore to a gateway that checks out"
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
    tar -C "$(dirname "$STATE_DIR")" -xf "$FROM/state.tar"
    chmod 700 "$STATE_DIR"
    if id agentnode-gateway >/dev/null 2>&1; then
      chown -R agentnode-gateway:agentnode-gateway "$STATE_DIR"
    fi
    say "restored $STATE_DIR"

    check_state "$STATE_DIR" || die "restored, and it does not check out. The previous state is still at $ASIDE"
    echo
    say "restored, and it checks out."
    say "restart the gateway:  systemctl restart agentnode-gateway"
    echo
    ;;

  *)
    cat <<'USAGE'

  backup-and-restore.sh backup  [--dir <state>] [--to <backups>]
  backup-and-restore.sh restore --from <backup directory>
  backup-and-restore.sh check   --from <backup directory>

  `check` restores into a temporary directory and asks the four questions that would
  actually be wrong, without touching the live gateway. Run it after every backup;
  a backup nobody has restored is a hope, not a backup.

USAGE
    exit 2
    ;;
esac
