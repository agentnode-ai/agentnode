#!/usr/bin/env python3
"""Read the sandbox documentation back and fail on anything that is not true or not usable.

    python docs/sandbox/_checks/check_docs.py

Five checks, each aimed at a way documentation goes wrong:

  commands      every `agentnode …` command in the docs exists in the CLI
  links         every relative link resolves to a file that is there
  drift         the generated matrix still matches the code facts
  jargon        the beginner pages never use a technical word they do not explain
  claims        no page describes something the matrix marks planned as if it worked today

It is not an accessibility checker and it does not replace the ten-person test. It catches the
mistakes a machine can catch so the humans can spend their attention on the ones it cannot.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1]
ROOT = DOCS.parents[1]
FACTS = json.loads((DOCS / "_facts" / "code-facts.json").read_text(encoding="utf-8"))
BLOG = ROOT / "docs" / "blog" / "drafts"

# Pages an ordinary person is expected to read.
BEGINNER = {"README.md", "understand.md", "choose.md", "setup-local.md", "mobile.md",
            "troubleshooting.md", "availability.md"}

# Words that must never appear unexplained on a beginner page. Each may appear if the same
# paragraph also explains it, which the check looks for crudely but usefully.
JARGON = {
    "container": "sealed workspace",
    "runtime": "the program that creates the sealed workspace",
    "egress": "outgoing network",
    "attestation": "proof from the hardware",
    "microvm": "a small separate machine",
    "digest": "an exact fingerprint of the image",
    "namespace": None,
    "seccomp": None,
    "capability": None,
}

problems: list[str] = []


def fail(where: str, msg: str) -> None:
    problems.append(f"{where}: {msg}")


GROUPS = {"mcp": "mcp_sub", "auth": "auth_sub", "config": "config_sub", "guard": "guard_sub",
          "lock": "lock_sub", "sandbox": "sandbox_sub", "capabilities": "cap_sub",
          "skill": "skill_sub"}


def known_commands() -> set[str]:
    cli = FACTS["cli"]
    known = {f"agentnode {c}" for c in set(cli.get("sub", []))}
    for parent, key in GROUPS.items():
        for child in cli.get(key, []):
            known.add(f"agentnode {parent} {child}")
    for child in cli.get("mcp_own_sub", []):
        known.add(f"agentnode mcp ownership {child}")
    return known


def command_groups() -> set[str]:
    """Commands that take a SUBCOMMAND, so a word after them must be one of its children."""
    g = {f"agentnode {p}" for p in GROUPS} | {"agentnode", "agentnode mcp ownership"}
    return g


def check_commands(pages: list[Path], known: set[str]) -> None:
    """Validate the COMPLETE command path, not merely a known prefix.

    `SANDBOX-DOCS-0001` / F-D1-001: the first version broke out of its loop as soon as any known
    prefix matched, so `agentnode sandbox invent` passed because `agentnode sandbox` exists. Now a
    command is valid only if every word of it is consumed by a real command, with the sole exception
    of placeholder arguments, which look like <this> or ARE the value in a `config set` example.

    One thing it cannot decide and does not pretend to: whether a LEAF command accepts the argument
    it was given. `agentnode search pdf` is a search term, not a subcommand, and no argument spec is
    extracted from the parser. So a word after a leaf is left alone, and a word after a command that
    takes subcommands must be one of that command's real children -- which is the case the
    `SANDBOX-DOCS-0001` finding was about.
    """
    groups = command_groups()
    placeholder = re.compile(r"^(<[^>]+>|[A-Z_]{2,}|[a-z_]+\.[a-z_.]+|curated_only|default|none)$")
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for raw in re.findall(r"agentnode(?: [a-z][A-Za-z0-9_.<>-]*)+", text):
            words = [w for w in raw.split() if not w.startswith("-")]
            # the longest real command that is a prefix of this line
            best = 0
            for n in range(len(words), 0, -1):
                if " ".join(words[:n]) in known:
                    best = n
                    break
            if best == 0:
                if words == ["agentnode"]:
                    continue
                fail(page.name, f"names a command that does not exist: {' '.join(words)!r}")
                continue
            leftover = words[best:]
            if " ".join(words[:best]) not in groups:
                continue  # a leaf command: what follows is its argument, not a subcommand
            # trailing prose is fine — documentation is prose. Trailing WORDS THAT LOOK LIKE
            # SUBCOMMANDS are not, because that is exactly how an invented command reads.
            for extra in leftover:
                if placeholder.match(extra):
                    continue
                if " ".join(words[:best] + [extra]) in known:
                    continue
                if extra in ALLOWED_PROSE:
                    break
                fail(page.name, f"names a command that does not exist: "
                                f"{' '.join(words[:best] + [extra])!r}")
                break


ALLOWED_PROSE = {
    "and", "or", "the", "a", "an", "is", "it", "to", "from", "on", "in", "then", "does", "changes",
    "tells", "gives", "prints", "says", "already", "for", "with", "that", "which", "if", "when",
    "you", "your", "we", "will", "would", "can", "may", "should", "must", "of", "as", "at", "by",
}


def selftest_command_checker(known: set[str]) -> int:
    """A checker nobody has seen fail is a checker nobody should trust.

    Returns how many of the three planted mistakes it caught; `main` checks that number too, so
    disabling this in one place is not enough. What it cannot defend against is someone deleting
    both guards on purpose -- no check inside a file protects that file. It defends against the
    thing that actually happens: the checker quietly becoming hollow as the code around it changes.
    """
    import tempfile
    before = len(problems)
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "selftest.md"
        bad.write_text("Run `agentnode sandbox invent`, `agentnode notacommand` and "
                       "`agentnode mcp ownership invent`." + chr(10), encoding="utf-8")
        check_commands([bad], known)
    caught = len(problems) - before
    del problems[before:]
    if caught != 3:
        fail("_checks/check_docs.py",
             f"the command checker did not reject three known-broken commands (caught {caught}); "
             "its clean result on the real pages would mean nothing")
    return caught


def check_links(pages: list[Path]) -> None:
    for page in pages:
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", page.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (page.parent / target).resolve().exists():
                fail(page.name, f"link points nowhere: {target}")


def check_drift() -> None:
    """The matrix must be what the generator produces from the current facts."""
    before = (DOCS / "availability.md").read_text(encoding="utf-8")
    subprocess.run([sys.executable, str(DOCS / "_checks" / "extract_facts.py")],
                   capture_output=True, check=True)
    subprocess.run([sys.executable, str(DOCS / "_checks" / "build_matrix.py")],
                   capture_output=True, check=True)
    after = (DOCS / "availability.md").read_text(encoding="utf-8")
    if before != after:
        fail("availability.md", "is stale — regenerate it with _checks/build_matrix.py")


def check_jargon(pages: list[Path]) -> None:
    for page in pages:
        if page.name not in BEGINNER:
            continue
        text = page.read_text(encoding="utf-8")
        # Code spans and fenced blocks are identifiers, not prose: a fact key like
        # `sandbox_runtime.egress_is_inert` is evidence a reader can look up, not jargon aimed at
        # them. Strip both before judging the words the page actually says.
        prose = re.sub(r"```.*?```", " ", text, flags=re.S)
        prose = re.sub(r"`[^`]*`", " ", prose)
        lowered = prose.lower()
        for word, gloss in JARGON.items():
            if word not in lowered:
                continue
            if gloss is None:
                fail(page.name, f"uses {word!r}, which a beginner page must never need")
                continue
            if gloss.split()[0].lower() not in lowered:
                fail(page.name, f"uses {word!r} without explaining it nearby "
                                f"(expected something like {gloss!r})")


def matrix_rows() -> list:
    """(row name, status) as the generated matrix states them."""
    out = []
    for line in (DOCS / "availability.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*\*\*(.+?)\*\*\s*\|\s*\S+\s*([a-z, ]+?)\s*\|", line)
        if m:
            out.append((m.group(1), m.group(2).strip()))
    return out


def check_facts_consistency(pages: list) -> None:
    """A page may not call a capability unfinished while the facts show something calling it.

    `SANDBOX-DOCS-0003`: the pages said the destination-limited network was inert and had no live
    caller. That was read out of a docstring written at an earlier stage; two run paths call it.
    The prose, the extracted facts and the matrix all agreed with each other and all three were
    wrong together, which is precisely what a check between them cannot catch and this one can.
    """
    dead = ("inert", "no live caller", "never creates the network", "nothing is passed today")
    subjects = ("network", "egress", "proxy", "secret", "credential", "passthrough")
    live = {"network": FACTS["sandbox_runtime"]["egress_live_callers"],
            "secret": FACTS["sandbox_runtime"]["secret_passthrough_live_callers"]}
    if not any(live.values()):
        return
    for page in pages:
        for line in page.read_text(encoding="utf-8").splitlines():
            low = line.lower()
            if any(d in low for d in dead) and any(s in low for s in subjects):
                fail(page.name, "calls a capability unfinished that the code facts show being "
                                f"called: {line.strip()[:90]!r}")
# Written out, not derived: deleting a planted wording must be a failure, not a smaller test.
EXPECTED_PLANTED_CLAIMS = 5


def sentences(text: str) -> list:
    """Split prose into sentences, with wrapped lines rejoined and code blocks dropped."""
    out, buf, in_code = [], [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if not line.strip():
            out.append(" ".join(buf)); buf = []
            continue
        buf.append(line.strip())
    out.append(" ".join(buf))
    result = []
    for para in out:
        for s in re.split(r"(?<=[.!?;])\s+", para):
            if s.strip():
                result.append(s.strip())
    return result


def check_local_only_claims(pages: list) -> None:
    """"Nothing leaves your machine" is a promise the consented-egress path can break.

    `SANDBOX-DOCS-0006`: the local pages said it absolutely. Where the work RUNS and what the
    program may REACH are two different questions, and a sentence that answers the first must not
    read as an answer to the second.
    """
    if not FACTS["sandbox_runtime"].get("egress_live_callers"):
        return
    absolute = re.compile(r"(nothing|never|no data)[^.]{0,40}"
                          r"leaves?[^.]{0,30}(machine|computer|device)", re.I)
    qualified = ("work ", "the work", "files", "documents", "agreed", "unless", "except",
                 "sites", "declared", "consent")
    for page in pages:
        for sentence in sentences(page.read_text(encoding="utf-8")):
            if not absolute.search(sentence):
                continue
            if any(w in sentence.lower() for w in qualified):
                continue
            fail(page.name, "promises that nothing leaves the machine, which a consented "
                            f"destination list can break: {sentence.strip()[:90]!r}")


PLANTED_LOCAL_CLAIMS = (
    "Nothing leaves the computer.",
    "You want nothing to leave your machine.",
    "It is free, and nothing ever leaves your device.",
)
EXPECTED_PLANTED_LOCAL = 3


def selftest_local_only_claims() -> int:
    import tempfile
    before = len(problems)
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "selftest.md"
        bad.write_text(chr(10) + chr(10).join(PLANTED_LOCAL_CLAIMS) + chr(10), encoding="utf-8")
        check_local_only_claims([bad])
    caught = len(problems) - before
    del problems[before:]
    return caught


#: Every situation the refusal classifier tells apart, bound to the ENTRY that documents it: the
#: page, and a phrase that must appear in one of that page's headings. A phrase found loose in some
#: paragraph is not documentation -- an entry is. Written out, so a case added to the product
#: without an entry fails here rather than passing unnoticed.
REFUSAL_CASE_ENTRIES = {
    "not_installed": ("troubleshooting.md", "No Docker or Podman found"),
    "not_running": ("troubleshooting.md", "daemon is not reachable"),
    "not_permitted": ("troubleshooting.md", "this account may not use it"),
    "incompatible": ("troubleshooting.md", "not in a mode that can run the sandbox"),
    "memory_ceiling_unenforceable": ("troubleshooting.md", "cannot hold the memory limit"),
    "image_missing": ("troubleshooting.md", "is not present locally"),
    "image_placeholder": ("troubleshooting.md", "no pinned sandbox image"),
    "platform_unsupported": ("mobile.md", "no sealed workspace on this device"),
}


def _headings(page: Path) -> list:
    return [line.lstrip("#").strip().lower()
            for line in page.read_text(encoding="utf-8").splitlines()
            if line.startswith("#")]


def check_refusal_cases(pages: list) -> None:
    """Every case the code can refuse with has to have its own entry on a named page."""
    cases = FACTS["sandbox_runtime"].get("refusal_cases") or []
    if not cases:
        return
    by_name = {page.name: page for page in pages}
    for case in cases:
        entry = REFUSAL_CASE_ENTRIES.get(case)
        if entry is None:
            fail("_checks/check_docs.py",
                 f"the code can refuse with {case!r} and this checker has no entry for it. Write "
                 "the entry and map it here, in the same change")
            continue
        filename, phrase = entry
        page = by_name.get(filename)
        if page is None:
            fail("_checks/check_docs.py", f"{case!r} is mapped to {filename}, which is not a page")
            continue
        if not any(phrase.lower() in h for h in _headings(page)):
            fail(filename,
                 f"the code can refuse with {case!r} and {filename} has no entry for it "
                 f"(no heading contains {phrase!r})")
    stale = [c for c in REFUSAL_CASE_ENTRIES if c not in cases]
    if stale:
        fail("_checks/check_docs.py",
             f"these cases have entries but the code no longer refuses with them: {stale}. Either "
             "the entry describes something that cannot happen, or a case was renamed")


def selftest_refusal_cases(pages: list) -> tuple:
    """Both directions, each planted alone so its result is about itself.

    SANDBOX-DOCS-DELTA-0002 / F-D4-002: the first version exercised only the undocumented
    direction and the brief nonetheless claimed both. Each is planted separately here, and each
    failure is matched by name rather than counted.
    """
    real = FACTS["sandbox_runtime"].get("refusal_cases")
    before = len(problems)
    try:
        # (1) the code grows a case no page has an entry for
        FACTS["sandbox_runtime"]["refusal_cases"] = ["a_case_nobody_wrote_about"]
        check_refusal_cases(pages)
        undocumented = len([x for x in problems[before:] if "a_case_nobody_wrote_about" in x])
        del problems[before:]
        # (2) a documented case the code no longer has
        FACTS["sandbox_runtime"]["refusal_cases"] = ["not_installed"]
        check_refusal_cases(pages)
        stale = len([x for x in problems[before:] if "no longer refuses with them" in x])
        del problems[before:]
    finally:
        FACTS["sandbox_runtime"]["refusal_cases"] = real
        del problems[before:]
    return undocumented, stale


def check_secret_claims(pages: list) -> None:
    """No page may promise that keys stay out of the sandbox while the runtime puts them there.

    Two rounds of this. `SANDBOX-DOCS-0004` caught the rule stated in the present tense beside the
    mechanism that contradicts it. `SANDBOX-DOCS-0005` caught the same claim in beginner wording
    the first pattern did not cover -- "none of your passwords or keys", "what it does not get:
    your keys" -- which is the more dangerous form, because it is the one a beginner reads. So the
    check is written around the CLAIM rather than around one sentence shape: any line that says a
    key does not reach the sandboxed program has to carry the exception with it.
    """
    if not FACTS["sandbox_runtime"].get("secret_value_is_inside_the_container"):
        return
    secret = re.compile(r"\b(key|keys|secret|secrets|password|passwords|credential|credentials"
                        r"|value|values)\b",
                        re.I)
    denial = re.compile(
        r"(never (holds|sees|receives|gets|enter|enters)"
        r"|does not (hold|see|receive|get|enter)"
        r"|do not (hold|see|receive|get|enter)"
        r"|(gets|receives|holds) (no|none of)"
        r"|\bnone of your\b"
        r"|\bno\b[^.]{0,20}\b(are|is) (passed|given|shared)"
        r"|does not get:"
        r"|not get:)", re.I)
    # A sentence may say it when it is about the planned broker, or when it is the exception itself.
    excused = ("planned", "would ", "working towards", "not built", "goal", "broker",
               "exception", "unless you", "if you agree", "you agree to")
    for page in pages:
        # Sentences, not lines: prose wraps, and the qualifier that makes a claim true is often on
        # the next line. A line-based check reported exactly that as a violation.
        for sentence in sentences(page.read_text(encoding="utf-8")):
            low = sentence.lower()
            if not (secret.search(sentence) and denial.search(sentence)):
                continue
            if any(w in low for w in excused):
                continue
            fail(page.name, "promises that keys stay out of the sandbox, which the code facts "
                            f"contradict: {sentence.strip()[:90]!r}")


# The exact wordings this check has been caught missing. Each one must still be rejected, so a
# later simplification of the patterns above cannot quietly reopen a finding that was already made.
PLANTED_SECRET_CLAIMS = (
    "The sandbox never holds your key.",
    "A container process does not see the value.",
    "Inside that workspace the program gets: none of your passwords or keys.",
    "What it does not get: your home folder, your browser profile, your keys.",
    "Your keys never enter it.",
)


def selftest_secret_claims() -> int:
    import tempfile
    before = len(problems)
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "selftest.md"
        bad.write_text(chr(10).join(PLANTED_SECRET_CLAIMS) + chr(10), encoding="utf-8")
        check_secret_claims([bad])
    caught = len(problems) - before
    del problems[before:]
    return caught

def selftest_facts_consistency() -> int:
    """Two planted sentences, both of which the check above must reject."""
    import tempfile
    before = len(problems)
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "selftest.md"
        bad.write_text("The egress network is inert." + chr(10)
                       + "Secrets by name have no live caller." + chr(10), encoding="utf-8")
        check_facts_consistency([bad])
    caught = len(problems) - before
    del problems[before:]
    return caught


# Words a page may use for a row the matrix calls planned or removed. The row names are generated,
# so the guarded vocabulary follows the matrix instead of being maintained beside it.
ALIASES = {
    "Your own sandbox server": ("self-hosted", "your own server", "own sandbox server"),
    "AgentNode Sandbox (managed)": ("agentnode sandbox", "managed service", "managed sandbox"),
    "Phone or tablet": ("phone", "tablet"),
    "Credential broker with a sentinel value": ("credential broker", "sentinel"),
    "Conformance suite for a backend": ("conformance suite",),
    "The EM-3 selection contract": ("selection contract",),
}


def unavailable_phrases() -> list:
    """Every phrase that names something the matrix says does not exist today."""
    out = []
    for name, status in matrix_rows():
        if not status.startswith(("planned", "removed")):
            continue
        out.append(name.lower())
        out += list(ALIASES.get(name, ()))
    if not out:
        fail("_checks/check_docs.py",
             "no row is planned or removed, so this check now guards nothing — either the matrix "
             "changed or it was not regenerated")
    return out


def check_claims(pages: list, phrases: list) -> None:
    """Anything the matrix calls planned or removed must not be written as available."""
    promise = re.compile(r"\b(you can now|available today|get started now|start now|book|"
                         r"sign up)\b", re.I)
    for page in pages:
        for line in page.read_text(encoding="utf-8").splitlines():
            if promise.search(line) and any(x in line.lower() for x in phrases):
                if "planned" in line.lower() or "not " in line.lower():
                    continue
                fail(page.name, f"writes a planned thing as available: {line.strip()[:90]!r}")


def selftest_claims(phrases: list) -> int:
    import tempfile
    before = len(problems)
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "selftest.md"
        bad.write_text("You can now use your own sandbox server." + chr(10)
                       + "The managed service is available today." + chr(10), encoding="utf-8")
        check_claims([bad], phrases)
    caught = len(problems) - before
    del problems[before:]
    return caught

def main() -> int:
    pages = sorted(p for p in DOCS.glob("*.md"))
    blog = sorted(BLOG.glob("*.md")) if BLOG.exists() else []
    known = known_commands()

    caught = selftest_command_checker(known)
    if caught != 3:
        fail("_checks/check_docs.py",
             f"the command checker self-test caught {caught} of 3 planted mistakes")
    check_commands(pages + blog, known)
    check_links(pages)
    check_drift()
    check_jargon(pages)
    phrases = unavailable_phrases()
    if selftest_claims(phrases) != 2:
        fail("_checks/check_docs.py",
             "the planned-claims check did not reject two planted sentences")
    check_claims(pages + blog, phrases)
    check_facts_consistency(pages + blog)
    check_secret_claims(pages + blog)
    check_refusal_cases(pages)
    undocumented, stale = selftest_refusal_cases(pages)
    if (undocumented, stale) != (1, 1):
        fail("_checks/check_docs.py",
             f"the refusal-case check caught {undocumented} of 1 undocumented case and {stale} of "
             "1 stale entry; it has to fail in both directions")
    check_local_only_claims(pages + blog)
    local = selftest_local_only_claims()
    if local != EXPECTED_PLANTED_LOCAL or len(PLANTED_LOCAL_CLAIMS) != EXPECTED_PLANTED_LOCAL:
        fail("_checks/check_docs.py",
             f"the local-only check rejected {local} of {EXPECTED_PLANTED_LOCAL} planted claims "
             f"and holds {len(PLANTED_LOCAL_CLAIMS)} of them")
    planted = selftest_secret_claims()
    if planted != EXPECTED_PLANTED_CLAIMS or len(PLANTED_SECRET_CLAIMS) != EXPECTED_PLANTED_CLAIMS:
        fail("_checks/check_docs.py",
             f"the secret-claim check rejected {planted} of {EXPECTED_PLANTED_CLAIMS} planted "
             f"claims and holds {len(PLANTED_SECRET_CLAIMS)} of them; these are the beginner "
             "wordings it has been caught missing before, and none may be dropped")
    if selftest_facts_consistency() != 2:
        fail("_checks/check_docs.py",
             "the facts-consistency check did not reject two planted sentences")

    print(f"  checked {len(pages)} documentation pages and {len(blog)} blog drafts "
          f"against {len(known)} real commands")
    if problems:
        print(f"\n  {len(problems)} problem(s):")
        for p in problems:
            print(f"    {p}")
        return 1
    print("  no problems found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
