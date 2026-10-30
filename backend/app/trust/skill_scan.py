"""Content check for prompt-only skills.

A skill runs no code: its whole effect is the text it gives an agent. The code
scanner (trust/scanner.py) reads only .py files, so without this module a
skill's SKILL.md was never looked at. These rules run on every text file of a
skill, on every upload, with no API key and no network.

Severity is chosen so that automation can act on it:
  high   — near-certainly malicious (hidden characters, a real secret, reading
           credential files and sending them, covert sending). Verification
           fails and the publisher is told why.
  medium — needs a human look (overriding the agent's rules, piping a remote
           script into a shell, sending credentials, concealing from the user,
           long encoded blobs). The version waits in the review queue.
  low    — information only (external links whose content is not reviewed).

Pattern rules do not catch every cleverly worded instruction; they catch the
common and the blatant ones. See docs/design/skill-publishing-trust.md.
"""

from __future__ import annotations

import re
import unicodedata

TEXT_EXTENSIONS = (
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".xml",
    ".html",
    ".css",
    ".svg",
)

_TRANSFER = r"(?:send|upload|post|transmit|forward|exfiltrate|e-?mail|paste|share|copy)"

# Credential stores an agent has no business reading for a third party.
_CREDENTIAL_PATHS = re.compile(
    r"~/\.ssh|\.ssh/|\bid_(?:rsa|ed25519|ecdsa|dsa)\b|\.aws/credentials|\.npmrc\b|\.pypirc\b"
    r"|\.netrc\b|\.git-credentials\b|\.docker/config\.json|\.config/gh/hosts|/etc/shadow\b"
    r"|\bkeychain\b|\bwallet\.dat\b|\bseed phrase\b|\brecovery phrase\b|\bLogin Data\b"
    r"|browser (?:cookies|passwords|password store|saved passwords)",
    re.IGNORECASE,
)

_SECRET_TOKENS = [
    (
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
        "private key",
    ),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "GitHub token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"), "GitHub token"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{20,}\b"), "Slack token"),
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b"), "API key"),
]

_HIGH_RULES = [
    (
        "skill_covert_transfer",
        re.compile(
            r"\b(?:silently|secretly|covertly|stealthily|quietly|without (?:the )?user'?s? "
            r"(?:knowledge|consent|noticing|knowing))\b.{0,80}\b" + _TRANSFER + r"\b"
            r"|\b" + _TRANSFER + r"\b.{0,80}\b(?:silently|secretly|covertly|stealthily|"
            r"without (?:the )?user'?s? (?:knowledge|consent|noticing|knowing))\b",
            re.IGNORECASE,
        ),
        "instructs the agent to send something covertly",
    ),
]

_MEDIUM_RULES = [
    (
        "skill_rule_override",
        re.compile(
            r"\b(?:ignore|disregard|forget|override|bypass)\b.{0,40}\b(?:previous|prior|above|earlier|"
            r"system|safety|security|your)\b.{0,25}\b(?:instructions?|rules|prompts?|guidelines|"
            r"polic(?:y|ies)|guardrails|restrictions)\b",
            re.IGNORECASE,
        ),
        "tells the agent to set aside its instructions or safety rules",
    ),
    (
        "skill_rule_override",
        re.compile(
            r"\b(?:reveal|print|output|disclose|repeat)\b.{0,30}\b(?:system prompt|hidden instructions|"
            r"initial instructions)\b|\bjailbreak\b|\bDAN mode\b|\bdo anything now\b|"
            r"\bdeveloper mode (?:enabled|on)\b|\bwithout (?:any )?(?:restrictions|limitations|filters)\b",
            re.IGNORECASE,
        ),
        "asks the agent to reveal its prompt or drop its restrictions",
    ),
    (
        "skill_concealment",
        re.compile(
            # "do not tell the user that/about/what ...", "hide ... from the user" — but not an
            # honesty rule like "never tell the user something you did not find".
            r"\b(?:do not|don't|never)\s+(?:tell|inform|show|mention|reveal|disclose)\s+"
            r"(?:this\s+|it\s+|anything\s+)?(?:to\s+)?(?:the\s+)?user\s+(?:that|about|what|which|how|you)\b"
            r"|\b(?:hide|conceal|keep)\b.{0,40}\bfrom (?:the )?user\b",
            re.IGNORECASE,
        ),
        "tells the agent to hide something from the user",
    ),
    (
        "skill_remote_execution",
        re.compile(
            r"\b(?:curl|wget|iwr|invoke-webrequest)\b[^\n|]{0,200}\|\s*(?:sudo\s+)?"
            r"(?:sh|bash|zsh|python3?|node|iex|powershell)\b"
            r"|\b(?:iex|invoke-expression)\b\s*[\(\$]"
            r"|\bpowershell(?:\.exe)?\b.{0,40}\s-(?:e|en|enc|encodedcommand)\s+[A-Za-z0-9+/=]{16,}"
            r"|\bbase64\s+(?:-d|--decode)\b[^\n]{0,80}\|\s*(?:sh|bash)\b",
            re.IGNORECASE,
        ),
        "tells the agent to run a remote or encoded script",
    ),
    (
        "skill_secret_transfer",
        re.compile(
            r"\b"
            + _TRANSFER
            + r"\b.{0,80}\b(?:api[ _-]?keys?|secrets?|access tokens?|passwords?|"
            r"credentials?|private keys?|env(?:ironment)? var(?:iable)?s?|ssh keys?|cookies|session ids?)\b",
            re.IGNORECASE,
        ),
        "tells the agent to send credentials or secrets somewhere",
    ),
    (
        "skill_encoded_blob",
        re.compile(r"[A-Za-z0-9+/]{200,}={0,2}"),
        "contains a long encoded blob",
    ),
]

_URL = re.compile(r"https?://[^\s)>\]\"']+", re.IGNORECASE)


def _hidden_characters(text: str) -> list[str]:
    found = set()
    for i, ch in enumerate(text):
        if i == 0 and ch == "﻿":
            continue  # a byte-order mark at the very start is harmless
        cat = unicodedata.category(ch)
        if cat in ("Cf", "Co", "Cs") or (cat == "Cc" and ch not in "\n\r\t"):
            found.add(f"U+{ord(ch):04X}")
    return sorted(found)


def _distinct_enough(token: str) -> bool:
    """Placeholders like sk-xxxxxxxxxxxxxxxxxxxx are not secrets."""
    return len(set(token)) >= 10


def scan_skill_text(path: str, text: str) -> list[dict]:
    """Return findings for one text file of a skill."""
    findings: list[dict] = []

    hidden = _hidden_characters(text)
    if hidden:
        findings.append(
            {
                "severity": "high",
                "finding_type": "skill_hidden_characters",
                "description": f"{path}: invisible or control characters {', '.join(hidden[:8])}",
            }
        )

    for line_no, line in enumerate(text.splitlines(), start=1):
        where = f"{path}:{line_no}"
        for pattern, label in _SECRET_TOKENS:
            m = pattern.search(line)
            if m and (label == "private key" or _distinct_enough(m.group(0))):
                findings.append(
                    {
                        "severity": "high",
                        "finding_type": "skill_embedded_secret",
                        "description": f"{where}: contains what looks like a real {label}",
                    }
                )
        if _CREDENTIAL_PATHS.search(line):
            if re.search(
                r"\b" + _TRANSFER + r"\b|https?://|\bcurl\b|\bwget\b",
                line,
                re.IGNORECASE,
            ):
                findings.append(
                    {
                        "severity": "high",
                        "finding_type": "skill_credential_exfiltration",
                        "description": f"{where}: reads a credential store and sends it somewhere",
                    }
                )
            else:
                findings.append(
                    {
                        "severity": "medium",
                        "finding_type": "skill_credential_access",
                        "description": f"{where}: refers to a credential store",
                    }
                )
        for finding_type, pattern, label in _HIGH_RULES:
            if pattern.search(line):
                findings.append(
                    {
                        "severity": "high",
                        "finding_type": finding_type,
                        "description": f"{where}: {label}",
                    }
                )
        for finding_type, pattern, label in _MEDIUM_RULES:
            if pattern.search(line):
                findings.append(
                    {
                        "severity": "medium",
                        "finding_type": finding_type,
                        "description": f"{where}: {label}",
                    }
                )

    hosts = sorted(
        {u.split("/")[2].lower() for u in _URL.findall(text) if u.count("/") >= 2}
    )
    if hosts:
        findings.append(
            {
                "severity": "low",
                "finding_type": "skill_external_links",
                "description": f"{path}: links to {len(hosts)} external host(s) ({', '.join(hosts[:5])}); "
                "content behind links is not reviewed",
            }
        )
    return findings


def scan_skill_files(files: dict[str, str]) -> list[dict]:
    """Scan every text file of a skill ({relative path: text})."""
    findings: list[dict] = []
    for path in sorted(files):
        findings.extend(scan_skill_text(path, files[path]))
    return findings


def summarize(findings: list[dict]) -> tuple[int, int, int]:
    """(high, medium, low) counts."""
    high = sum(1 for f in findings if f["severity"] == "high")
    medium = sum(1 for f in findings if f["severity"] == "medium")
    low = sum(1 for f in findings if f["severity"] == "low")
    return high, medium, low
