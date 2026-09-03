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


def check_claims(pages: list[Path]) -> None:
    """Anything the matrix calls planned must not be written as available."""
    planned = ("AgentNode Sandbox", "self-hosted", "your own server", "managed")
    promise = re.compile(r"\b(you can now|available today|get started now|start now|book|"
                         r"sign up)\b", re.I)
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for line in text.splitlines():
            if promise.search(line) and any(p.lower() in line.lower() for p in planned):
                if "planned" in line.lower() or "not " in line.lower():
                    continue
                fail(page.name, f"writes a planned thing as available: {line.strip()[:90]!r}")


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
    check_claims(pages + blog)

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
