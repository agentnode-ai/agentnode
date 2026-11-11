"""Task #9: the README must tell a Windows or macOS user what the sandbox needs.

It said packs run "with automatic subprocess isolation (all trust levels)" and that unverified
tools "run in isolated child processes" -- neither is what the code does. With the shipped
host-trust policy (``curated_only``) only AgentNode-curated packs run on the host; everything
else needs a container runtime or is refused (sandbox/policy.py, config.py). Someone without
Docker or Podman then met a refusal the README never mentioned. The platform status must match
docs/sandbox/availability.md: Linux tested, Windows and macOS available but not tested.
"""
from __future__ import annotations

from pathlib import Path

README = Path(__file__).resolve().parents[2] / "README.md"


def _text() -> str:
    return README.read_text(encoding="utf-8")


def test_the_readme_no_longer_promises_subprocess_isolation():
    text = _text()
    assert "automatic subprocess isolation" not in text
    assert "isolated child processes" not in text


def test_the_readme_says_what_the_sandbox_needs_and_how_to_check():
    text = _text()
    assert "Docker or Podman" in text
    assert "agentnode sandbox doctor" in text
    assert "docs/sandbox/setup-local.md" in text


def test_the_readme_states_the_platform_status_honestly():
    text = _text()
    section = text[text.index("### Sandbox and platforms"):]
    section = section[:section.index("\n## ")]
    assert "Linux" in section and "tested" in section
    for platform in ("Windows", "macOS"):
        line = next(ln for ln in section.splitlines() if ln.startswith("| " + platform))
        assert "not yet tested" in line, line
