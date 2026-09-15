"""What is decided before anything runs, and the honest name for what that can achieve.

## What this is not

**This does not detect what a job is for.** It cannot, and no part of this product may be
described as though it could. A program that reads a network socket is a backup client or an
exfiltration tool depending on facts that are not in the program. Static analysis of somebody
else's code, asking a model whether the code looks malicious, matching against a list of bad
strings -- all of these produce a number that can be put in a report, and none of them produces a
true statement about intent. A sandbox service that claimed otherwise would be selling a property
it does not have, and the first customer to rely on it would be the one harmed.

So the claim made here is deliberately smaller and is true:

* **technical limits** -- how much, how often, how many at once, how large, for how long. These
  are measured and enforced, and they bound damage regardless of intent.
* **operator rules** -- what this operator permits at all: the network mode, the destinations,
  the ceilings, the image. Decided by a person, not inferred from a job.
* **behavioural signals** -- patterns in what a customer has actually done here, which are
  evidence of a PATTERN and never evidence of a purpose. A burst of refusals looks like probing
  and also looks like a broken script.
* **suspension** -- the ability to stop a customer once a human has decided to, with the
  operator's own words attached.

Between them, the damage a customer can do is bounded and a customer can be stopped. That is what
a managed sandbox can offer. Whether a permitted job is being used for something the operator
would not want is **not** something this system determines, and the documentation says so where a
reader will meet it rather than only here.

## Why it is one decision

Admission used to be several checks at several call sites: readiness in one place, ceilings in
another, the stop in a third, and nothing at all for the rate. A check per call site is a check
per call site somebody remembers, and the one that is forgotten is the one that matters. There is
one function each for the two questions this service actually asks --

    may this caller do ANYTHING right now          `before_an_operation`
    may this particular job run                    `before_a_run`

-- and both are reached from `GatewayService`, which remains the only implementation of the quota
claim, the policy and the meter. Nothing here re-implements those; it sequences them and names the
refusals.

## Reason codes

Every refusal carries a name from `REASONS`, which is closed. A caller that has to read prose to
tell "you are over your own ceiling" from "the operator has stopped everything" will get it wrong,
and the two need different actions. The contract's refusal names are coarser on purpose -- they
are what a client branches on -- so each reason code maps onto exactly one of them, and the finer
name travels alongside for the person reading it.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from agentnode_sdk.access import contract
from agentnode_sdk.gateway.filelock import ProcessLock

#: Every reason admission can refuse for. Closed: a refusal that could invent its own name is a
#: refusal a client cannot be written against, and an operator cannot count.
REASONS = (
    "gateway_stopped",            # the operator stopped everything
    "gateway_unmeasured",         # this gateway cannot say what it enforces
    "account_suspended",          # this customer is suspended, with the operator's words
    "account_unreadable",         # this gateway cannot tell who is suspended
    "device_rate",                # too many requests from one device, too quickly
    "account_rate",               # too many requests from one account, too quickly
    "device_concurrent",          # this device already has as many runs going as it may
    "account_concurrent",         # this account already has as many runs going as it may
    "device_runs_window",         # this device has started as many runs as it may in the window
    "account_runs_window",        # this account has started as many runs as it may in the window
    "device_seconds_window",      # this device has used its wall clock for the window
    "account_seconds_window",     # this account has used its wall clock for the window
    "artifact_too_large",         # the code sent is larger than this gateway accepts
    "refused_by_operator_policy",  # the job asks for something the operator does not allow
    "not_enrolled",               # no account could be established for this caller
)

#: Which contract refusal each reason is rendered as. A client branches on the contract name; a
#: person reads the reason. Keeping the mapping here rather than at each raise site is what makes
#: it checkable that every reason has one.
AS_A_REFUSAL = {
    "gateway_stopped": "gateway_stopped",
    "gateway_unmeasured": "sandbox_unavailable",
    "account_suspended": "gateway_stopped",
    "account_unreadable": "gateway_stopped",
    "device_rate": "over_a_ceiling",
    "account_rate": "over_a_ceiling",
    "device_concurrent": "over_a_ceiling",
    "account_concurrent": "over_a_ceiling",
    "device_runs_window": "over_a_ceiling",
    "account_runs_window": "over_a_ceiling",
    "device_seconds_window": "over_a_ceiling",
    "account_seconds_window": "over_a_ceiling",
    "artifact_too_large": "over_a_ceiling",
    "refused_by_operator_policy": "refused_by_policy",
    "not_enrolled": "not_authenticated",
}

assert set(AS_A_REFUSAL) == set(REASONS), "every reason renders as exactly one refusal"
assert set(AS_A_REFUSAL.values()) <= set(contract.REFUSALS), (
    "every refusal admission produces is one the contract declares")


class NotAdmitted(Exception):
    """Work was refused before it started. Carries what to call it and what to do about it."""

    def __init__(self, reason: str, because: str, what_to_do: str,
                 lifts_at: float = 0.0) -> None:
        if reason not in REASONS:
            raise ValueError("%r is not a reason admission declares" % reason)
        if not str(because).strip():
            raise ValueError("a refusal has to say what happened")
        if not str(what_to_do).strip():
            # A refusal with nothing to do about it leaves somebody stuck, and "stuck" is
            # indistinguishable from "broken" to the person it happens to. Refused at the raise
            # site rather than discovered by a customer.
            raise ValueError("a refusal has to name something the refused party can do")
        super().__init__(because)
        self.reason = reason
        self.refusal = AS_A_REFUSAL[reason]
        self.because = str(because)
        self.what_to_do = str(what_to_do)
        self.lifts_at = float(lifts_at or 0.0)


def _when(lifts_at: float, now: float) -> str:
    seconds = max(0.0, float(lifts_at) - float(now))
    if seconds <= 0:
        return "shortly"
    if seconds < 90:
        return "in about %d seconds" % max(1, round(seconds))
    if seconds < 90 * 60:
        return "in about %d minutes" % max(1, round(seconds / 60.0))
    return "in about %d hours" % max(1, round(seconds / 3600.0))


# --------------------------------------------------------------------------- the rate

#: Where per-caller request rates are counted. Beside the other counters, under its own lock.
RATE_NAME = "rate.json"

#: A key has to be one of ours. A caller-shaped value would let one caller count against
#: another's key, or against a key that is also a filename.
_KEY = re.compile(r"^[0-9a-zA-Z_:-]{1,96}$")


class RateLimit:
    """How many requests one key has made lately, durably, across processes.

    Separate from `throttle.Budget`, which is deliberately keyless: that one bounds an
    UNAUTHENTICATED stranger and refuses to depend on any notion of who is asking, because behind
    a proxy there is none it could believe. This one runs after authentication, where the gateway
    established the identity itself, so keying on it is sound.

    Unreadable is spent. A rate limit that forgets when its file is damaged is a rate limit an
    attacker removes by damaging a file.
    """

    def __init__(self, path: str | os.PathLike[str], window_seconds: float = 60.0) -> None:
        self.path = Path(path)
        self.window = float(window_seconds)
        self._lock = threading.Lock()

    def _load(self, now: float) -> dict | None:
        """What has been spent, or None meaning "treat every key as exhausted"."""
        if not self.path.exists():
            return {}
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(body, dict):
            return None
        cutoff = now - self.window
        return {str(k): [float(t) for t in v if float(t) > cutoff]
                for k, v in body.items() if isinstance(v, list)}

    def spend(self, key: str, allowance: int, now: float | None = None) -> float:
        """Record one request. Returns 0.0 when there was room, or when it lifts if there wasn't.

        A ceiling of zero is no ceiling, like every other ceiling in this gateway, and then
        nothing is written at all -- a rate limiter that wrote a file per request while
        enforcing nothing would be pure cost.
        """
        if not allowance:
            return 0.0
        if not _KEY.match(str(key or "")):
            # Not a key this gateway issues. Counting it would create an entry an outsider
            # chose the name of; refusing to count it would exempt it. Treated as exhausted.
            return float(time.time() if now is None else now) + self.window
        at = time.time() if now is None else now
        with self._lock, ProcessLock(self.path):
            body = self._load(at)
            if body is None:
                return at + self.window
            mine = body.get(str(key), [])
            if len(mine) >= int(allowance):
                return min(mine) + self.window
            mine.append(at)
            body[str(key)] = mine
            self._write(body)
            return 0.0

    def forget(self, key: str) -> bool:
        """Drop one key entirely. What deleting a customer has to imply here too."""
        with self._lock, ProcessLock(self.path):
            body = self._load(time.time())
            if body is None or str(key) not in body:
                return False
            del body[str(key)]
            self._write(body)
            return True

    def _write(self, body: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=str(self.path.parent),
                                       prefix="." + self.path.name + "-")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(body, sort_keys=True))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        try:
            os.chmod(self.path, 0o600)
        except OSError:                                       # pragma: no cover - advisory here
            pass


# --------------------------------------------------------------------------- the decisions


@dataclass(frozen=True)
class Standing:
    """What the gateway knows about a caller before it looks at what they asked for."""

    account_id: str
    device_id: str
    #: "" when the account is in good standing, otherwise the operator's own words.
    suspended_because: str = ""
    #: True when this gateway could not read who is suspended, which is not the same as nobody.
    cannot_tell: bool = False


def before_an_operation(standing: Standing, *, stopped_because: str, allowance,
                        rate: RateLimit, would_run_work: bool,
                        now: float | None = None) -> None:
    """May this caller do anything at all right now? Raises `NotAdmitted` to refuse.

    Two different scopes, on purpose.

    **The rate is asked of every operation.** The cheap ones are what a probe uses: enumerating
    devices, asking about runs, and collecting refusals are all requests, and a gateway that
    rate-limits only the operation that runs something has not rate-limited anything.

    **The stop and suspension refuse WORK, and not reading.** A stopped gateway still answers
    what happened: the moment an operator reaches for the stop is exactly the moment somebody
    needs to see why, and a customer locked out of their own record during an incident has been
    given a worse product and no additional safety. Suspension follows the same rule rather than
    a stricter one, because a suspended customer who cannot read their own usage cannot check
    the operator's account of what they did.

    The order is cheapest-and-most-total first: an operator who has stopped this gateway has
    stopped it for everybody, so nobody's counters are spent finding that out.
    """
    at = time.time() if now is None else now

    if not standing.account_id:
        raise NotAdmitted(
            "not_enrolled",
            "This credential is not attached to an account, so there is nothing to run work "
            "against.",
            "Pair this device again with a fresh invitation.")

    if would_run_work:
        if stopped_because:
            # STOPPED_SAYS is the stable opening the rest of the product already uses for this.
            # Composing a second sentence here would mean the same event read differently
            # depending on which door somebody came through, and a client matching on one of
            # them would be right about half the time.
            from agentnode_sdk.gateway.allowance import STOPPED_SAYS

            raise NotAdmitted(
                "gateway_stopped",
                STOPPED_SAYS + stopped_because,
                "Nothing will run until whoever runs it starts it again. You can still ask "
                "what happened to runs you already submitted.")

        if standing.cannot_tell:
            raise NotAdmitted(
                "account_unreadable",
                "This sandbox cannot currently read which of its accounts are suspended, so it "
                "is not taking work from any of them.",
                "Ask whoever runs this sandbox to look at the gateway's state directory. "
                "Nothing was run and nothing was counted against you.")

        if standing.suspended_because:
            raise NotAdmitted(
                "account_suspended",
                "This account is suspended: %s" % standing.suspended_because,
                "Reply to whoever runs this sandbox. A suspension is lifted by a person, and "
                "nothing you send here changes it. You can still read what this account has "
                "already done.")

    lifts = rate.spend(standing.device_id, allowance.requests_per_minute, now=at)
    if lifts:
        raise NotAdmitted(
            "device_rate",
            "This device has made more requests in the last minute than this sandbox accepts "
            "(%d)." % allowance.requests_per_minute,
            "Wait %s and send it again. Nothing was run." % _when(lifts, at),
            lifts_at=lifts)

    lifts = rate.spend(standing.account_id, allowance.account_requests_per_minute, now=at)
    if lifts:
        raise NotAdmitted(
            "account_rate",
            "This account has made more requests in the last minute than this sandbox accepts "
            "(%d), across all of its devices." % allowance.account_requests_per_minute,
            "Wait %s and send it again. If this is unexpected, look at your device list: it "
            "counts every device in the account, not only this one." % _when(lifts, at),
            lifts_at=lifts)


def artifact_within_ceiling(allowance, artifact_bytes: int) -> None:
    """The one job-shaped ceiling this gateway enforces itself.

    The runtime bounds what a job does once it is running. It cannot bound what was handed to
    the gateway in the first place, because by then the bytes have already been received and
    stored -- so this is checked here, before the artifact goes anywhere.
    """
    if allowance.max_artifact_bytes and int(artifact_bytes) > allowance.max_artifact_bytes:
        raise NotAdmitted(
            "artifact_too_large",
            "The code sent is %d bytes and this sandbox accepts up to %d."
            % (int(artifact_bytes), allowance.max_artifact_bytes),
            "Send less: fetch large inputs from inside the job instead of sending them with it.")


def describe(allowance) -> dict:
    """What a caller is told about the ceilings that apply to them.

    The account's ceilings are included. A customer who cannot see the ceiling that refused them
    is a customer who has to guess, and the numbers are the operator's own published limits
    rather than anything about another account.
    """
    return dict(allowance.as_dict())
