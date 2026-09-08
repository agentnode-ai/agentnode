"""Recording an external run so the record can be checked rather than believed.

`EM3C-EXTERNAL-0017` blocked on the evidence, not on the code. The findings were specific and
they are the specification for this module:

* a transcript recorded one run id while the record beside it was about another, so the claimed
  end-to-end trace was never established;
* exit statuses were reported from the end of a pipe rather than from the command that mattered,
  which is always zero and therefore always looked like success;
* summarised output ("none above means none") could not be told apart from a check that failed to
  run at all.

So this module records, and then a separate pass verifies. The two are deliberately not the same
code path: a recorder that also judged would report the verdict it was built to reach.

## What "not proven" means here

Every rule below treats absence as failure. A missing field, an HTTP 404, an empty stdout, a check
command that itself exited non-zero, and a JSON `null` are each a reason to fail — never a match.
That is the whole lesson of the earlier rounds: the defect was never a check that said no, it was
a check that could not see its input and said yes.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from typing import Any

SCHEMA = 1

#: Anything matching these is replaced before a command line is written down. The list is
#: deliberately about argument NAMES rather than about value shapes: a token is only recognisable
#: as a token by where it appears, and a redactor that guessed from shape would miss the one that
#: did not look like the others.
_SECRET_FLAGS = ("--code", "--token", "--secret", "--password", "--key")
REDACTED = "[redacted]"


class EvidenceError(Exception):
    """The evidence does not establish what it claims."""


def redact_argv(argv) -> list[str]:
    """A command line safe to write down, with the values of secret-bearing flags removed."""
    out: list[str] = []
    skip = False
    for item in argv:
        text = str(item)
        if skip:
            out.append(REDACTED)
            skip = False
            continue
        if text in _SECRET_FLAGS:
            out.append(text)
            skip = True
            continue
        if "=" in text and text.split("=", 1)[0] in _SECRET_FLAGS:
            out.append(text.split("=", 1)[0] + "=" + REDACTED)
            continue
        out.append(text)
    return out


def redact_text(text: str, secrets) -> str:
    """Remove known secret VALUES from captured output."""
    for secret in secrets or ():
        if secret and len(str(secret)) >= 8:
            text = text.replace(str(secret), REDACTED)
    return text


@dataclass
class Step:
    """One command, and everything needed to tell whether it did what is claimed."""

    name: str
    role: str
    argv: list[str]
    started_at: float
    ended_at: float
    exit_code: int | None
    stdout: str
    stderr: str
    #: What the step was supposed to do, so a pass can be distinguished from a coincidence.
    expected_exit: int | None = None
    expected_refusal: str = ""
    #: The one run this step is about. A step that mixes two is refused by the verifier.
    run_id: str = ""
    job_id: str = ""
    client_id: str = ""
    request_policy_sha256: str = ""
    effective_policy_sha256: str = ""
    policy_deltas: list | None = None
    #: The gateway's own record of the SAME run id, fetched from the gateway.
    gateway_record: dict | None = None
    container: str = ""
    cleanup_verified: Any = None
    error_class: str = ""
    notes: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class Recorder:
    """Runs commands and writes down what happened, without judging any of it."""

    def __init__(self, path, role: str, secrets=()) -> None:
        self.path = str(path)
        self.role = role
        self.secrets = list(secrets)
        self.steps: list[Step] = []

    def run(self, name: str, argv, *, expected_exit: int | None = 0, timeout: float = 600.0,
            **fields) -> Step:
        """Execute one command. The exit code recorded is this command's own."""
        # Two layers, because neither alone is enough. Flag-based redaction removes values this
        # code has never seen, by knowing WHERE a secret goes. Value-based redaction removes the
        # ones it has been told about, wherever they happen to sit -- the self-test found a
        # secret surviving inside a `-c` script argument, which no flag rule would ever have
        # caught.
        safe = [redact_text(item, self.secrets) for item in redact_argv(argv)]
        started = time.time()
        try:
            completed = subprocess.run(list(argv), capture_output=True, text=True,
                                       timeout=timeout, check=False)
            code, out, err = completed.returncode, completed.stdout, completed.stderr
            error_class = ""
        except subprocess.TimeoutExpired as exc:
            code, out, err = None, (exc.stdout or ""), (exc.stderr or "")
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
            if isinstance(err, bytes):
                err = err.decode("utf-8", "replace")
            error_class = "TimeoutExpired"
        except OSError as exc:
            code, out, err, error_class = None, "", str(exc), type(exc).__name__

        step = Step(
            name=name, role=self.role, argv=safe,
            started_at=started, ended_at=time.time(),
            exit_code=code,
            stdout=redact_text(out, self.secrets),
            stderr=redact_text(err, self.secrets),
            expected_exit=expected_exit,
            error_class=error_class,
            **fields,
        )
        self.steps.append(step)
        self._append(step)
        return step

    def record(self, step: Step) -> Step:
        """Write down a step assembled by the caller (an in-process action, not a command)."""
        step.stdout = redact_text(step.stdout, self.secrets)
        step.stderr = redact_text(step.stderr, self.secrets)
        self.steps.append(step)
        self._append(step)
        return step

    def _append(self, step: Step) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"schema": SCHEMA, **step.as_dict()},
                                sort_keys=True, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- verification


REQUIRED_FIELDS = ("name", "role", "argv", "started_at", "ended_at", "exit_code")


def load(path) -> list[dict]:
    steps = []
    with open(path, encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                steps.append(json.loads(line))
            except ValueError as exc:
                raise EvidenceError(f"line {number} of the evidence is not readable: {exc}") from None
    if not steps:
        raise EvidenceError("the evidence file records no steps at all.")
    return steps


def verify(steps, secrets=()) -> list[str]:
    """Every way this evidence fails to establish what it claims. Empty means it holds."""
    problems: list[str] = []

    for index, step in enumerate(steps):
        where = f"step {index + 1} ({step.get('name') or 'unnamed'})"

        for name in REQUIRED_FIELDS:
            if name not in step:
                problems.append(f"{where}: no {name} was recorded, so nothing about it is established")

        # 1. the exit code has to be the command's own, and has to be the expected one
        expected = step.get("expected_exit", 0)
        actual = step.get("exit_code")
        if actual is None:
            problems.append(
                f"{where}: no exit code was captured"
                + (f" ({step['error_class']})" if step.get("error_class") else "")
                + ", so whether the command succeeded is unknown")
        elif expected is not None and actual != expected:
            problems.append(f"{where}: exited {actual}, and {expected} was required")

        # 2. output that was supposed to say something has to have said it
        if step.get("expect_output") and not (step.get("stdout") or "").strip():
            problems.append(
                f"{where}: stdout is empty. An empty capture and a check that never ran look "
                "the same, so it is not accepted as either")

        # 3. a refusal has to be the refusal that was expected, not any refusal
        wanted = (step.get("expected_refusal") or "").strip()
        if wanted:
            seen = (step.get("stdout") or "") + (step.get("stderr") or "")
            record = step.get("gateway_record") or {}
            seen += str(record.get("refusal") or "")
            if wanted.lower() not in seen.lower():
                problems.append(
                    f"{where}: was supposed to be refused because {wanted!r}, and that reason "
                    "does not appear. A refusal for another reason is not this test passing")

        # 4. one step is about one run
        run_id = (step.get("run_id") or "").strip()
        record = step.get("gateway_record")
        if run_id:
            if record is None:
                problems.append(
                    f"{where}: names run {run_id} and carries no gateway record for it, so only "
                    "one side of the run is evidenced")
            else:
                if not isinstance(record, dict):
                    problems.append(f"{where}: the gateway record is not a record")
                else:
                    if record.get("error") or record.get("status") == 404:
                        problems.append(
                            f"{where}: the gateway had no record of run {run_id} "
                            f"({record.get('error') or 'HTTP 404'}); an absent record is not a match")
                    recorded = str(record.get("run_id") or "")
                    if recorded and recorded != run_id:
                        problems.append(
                            f"{where}: the step is about run {run_id} and the record is about "
                            f"{recorded}. Two runs in one piece of evidence establish neither")
                    elif not recorded:
                        problems.append(
                            f"{where}: the gateway record does not say which run it is about")

                    # 5. the digests recorded must be the ones the gateway holds
                    for field_name in ("request_policy_sha256", "effective_policy_sha256"):
                        claimed = (step.get(field_name) or "").strip()
                        held = str(record.get(field_name) or "")
                        if claimed and held and claimed != held:
                            problems.append(
                                f"{where}: {field_name} recorded as {claimed[:12]}... and the "
                                f"gateway says {held[:12]}...")
                        if claimed and not held:
                            problems.append(
                                f"{where}: {field_name} was recorded but the gateway record has "
                                "none to compare it with")

                    # 6. narrowing has to be reported wherever the digests differ
                    req = str(record.get("request_policy_sha256") or "")
                    eff = str(record.get("effective_policy_sha256") or "")
                    deltas = record.get("policy_deltas")
                    if req and eff and req != eff:
                        if deltas is None:
                            problems.append(
                                f"{where}: the policy digests differ and the record carries no "
                                "policy_deltas at all")
                        elif not deltas:
                            problems.append(
                                f"{where}: the policy digests differ and no narrowing was "
                                "reported, so the caller was not told what changed")

                    # 7. cleanup has to have been asked and answered
                    if step.get("expect_cleanup"):
                        cleanup = record.get("cleanup_verified", "__absent__")
                        if cleanup == "__absent__":
                            problems.append(
                                f"{where}: cleanup was supposed to be established and the record "
                                "does not mention it")
                        elif cleanup is None:
                            problems.append(
                                f"{where}: cleanup is unknown, which is not the same as clean")
                        elif cleanup is not True:
                            problems.append(f"{where}: cleanup was not verified ({cleanup!r})")

        # 8. a container named in evidence has to belong to this run
        container = (step.get("container") or "").strip()
        if container and run_id:
            short = run_id[:12]
            if short and short not in container:
                problems.append(
                    f"{where}: container {container!r} does not carry run {short}, so it is not "
                    "shown to be this run's")

        # 9. nothing secret may appear in what was written down
        blob = json.dumps(step, ensure_ascii=False)
        for secret in secrets or ():
            if secret and len(str(secret)) >= 8 and str(secret) in blob:
                problems.append(f"{where}: a live secret value was written into the evidence")

    return problems


def check_file(path, secrets=()) -> list[str]:
    return verify(load(path), secrets)


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Check an external-run evidence file.")
    parser.add_argument("path")
    parser.add_argument("--secret", action="append", default=[],
                        help="A value that must not appear anywhere in the evidence")
    args = parser.parse_args(argv)
    try:
        problems = check_file(args.path, args.secret)
    except EvidenceError as exc:
        print(f"  {exc}")
        return 2
    if problems:
        print(f"  This evidence does not establish what it claims ({len(problems)}):")
        for problem in problems:
            print(f"    {problem}")
        return 1
    print("  Every step is complete, self-consistent and about the run it names.")
    return 0


if __name__ == "__main__":                                        # pragma: no cover
    raise SystemExit(main())
