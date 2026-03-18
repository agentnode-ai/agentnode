"""Security guardrails for the Capabilities Builder.

Protects against:
- Prompt injection attempts
- Requests for malicious code generation
- Generated code containing dangerous patterns
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Input blocklist — descriptions requesting malicious capabilities
# ---------------------------------------------------------------------------

_MALICIOUS_KEYWORDS = [
    "malware", "ransomware", "keylogger", "keystroke logger", "trojan",
    "backdoor", "rootkit", "botnet", "spyware", "adware", "worm",
    "exploit", "zero-day", "0day", "privilege escalation", "priv esc",
    "reverse shell", "bind shell", "shell code", "shellcode",
    "payload", "dropper", "loader", "packer", "crypter", "obfuscate",
    "ddos", "denial of service", "flood attack", "syn flood",
    "brute force password", "credential stuffing", "password spray",
    "phishing", "spear phishing", "social engineering",
    "data exfiltration", "exfiltrate data", "steal data", "steal credentials",
    "bypass authentication", "bypass security", "bypass firewall",
    "disable antivirus", "disable defender", "kill process",
    "crypto miner", "cryptominer", "mine bitcoin", "mine crypto",
    "inject sql", "sql injection", "xss attack", "cross site scripting",
    "man in the middle", "mitm", "arp spoof", "dns spoof",
    "packet sniffer", "network sniffer", "wiretap",
    "crack password", "hash crack", "rainbow table",
    "remote access trojan", "rat tool",
    "fork bomb", "zip bomb", "decompression bomb",
]

# Semantic exfiltration patterns — descriptions that disguise data theft as legitimate features
_EXFILTRATION_PATTERNS = [
    r"environment\s+variables?.+(?:send|post|upload|transmit|forward|log.+(?:http|endpoint|server|url))",
    r"(?:send|post|upload|transmit|forward).+environment\s+variables?",
    r"(?:read|collect|gather|dump|extract)\s+(?:all\s+)?(?:env|environment)\s+var.+(?:send|post|http|endpoint|webhook|server)",
    r"silently\s+(?:send|upload|post|transmit|copy|forward|exfil)",
    r"without\s+(?:the\s+user|telling|notif|inform|alert|consent|knowledge|knowing)",
    r"(?:secretly|covertly|stealthily|hidden|quietly)\s+(?:send|upload|post|transmit|copy|forward)",
    r"(?:send|upload|post|transmit).+(?:to\s+(?:an?\s+)?(?:external|remote|third.party|outside))",
    r"(?:api.key|secret|token|password|credential).+(?:send|post|upload|transmit|forward|http|endpoint)",
]

# Prompt injection patterns — attempts to override system instructions
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|rules|prompts)",
    r"ignore\s+your\s+(system|initial)\s+(prompt|instructions)",
    r"disregard\s+(all|your|the)\s+(previous|prior|above|system)",
    r"forget\s+(everything|all|your)\s+(above|previous|instructions)",
    r"new\s+instructions?\s*:",
    r"system\s*prompt\s*:",
    r"you\s+are\s+now\s+a",
    r"act\s+as\s+if\s+you\s+are",
    r"pretend\s+(you\s+are|to\s+be)\s+a",
    r"switch\s+to\s+.{0,20}\s+mode",
    r"enter\s+.{0,20}\s+mode",
    r"jailbreak",
    r"do\s+anything\s+now",
    r"dan\s+mode",
    r"developer\s+mode\s+(enabled|on|activate)",
    r"override\s+(safety|content|security)\s+(filter|policy|rules)",
    r"respond\s+without\s+(restrictions|limitations|filters)",
    r"output\s+the\s+system\s+prompt",
    r"reveal\s+(your|the)\s+(system|initial)\s+(prompt|instructions)",
    r"what\s+(is|are)\s+your\s+(system|initial)\s+(prompt|instructions)",
]


def validate_input(description: str) -> str | None:
    """Validate builder input. Returns error message or None if clean."""
    desc_lower = description.lower()

    # Check malicious keywords
    for keyword in _MALICIOUS_KEYWORDS:
        if keyword in desc_lower:
            logger.warning("Builder input blocked (malicious keyword: %s): %s", keyword, description[:100])
            return "This description contains terms associated with malicious software. Please describe a legitimate capability."

    # Check prompt injection patterns
    for pattern in _INJECTION_PATTERNS:
        if re.search(pattern, desc_lower):
            logger.warning("Builder input blocked (prompt injection): %s", description[:100])
            return "This description contains patterns that could interfere with the generation process. Please rephrase."

    # Check semantic exfiltration patterns
    for pattern in _EXFILTRATION_PATTERNS:
        if re.search(pattern, desc_lower):
            logger.warning("Builder input blocked (exfiltration pattern): %s", description[:100])
            return "This description appears to request data exfiltration capabilities. Please describe a legitimate capability."

    return None


# ---------------------------------------------------------------------------
# Output scan — check generated code for dangerous patterns
# ---------------------------------------------------------------------------

_DANGEROUS_CODE_PATTERNS = [
    (r"os\.system\(", "os.system() call"),
    (r"subprocess\.(run|Popen|call|check_output)\(", "subprocess execution"),
    (r"exec\(", "exec() call"),
    (r"eval\(", "eval() call"),
    (r"__import__\(", "dynamic import"),
    (r"shutil\.rmtree\(", "recursive file deletion"),
    (r"os\.remove\(|os\.unlink\(", "file deletion"),
    (r"open\(.+,\s*['\"]w", "file write operation"),
    (r"socket\.(socket|create_connection)\(", "raw socket usage"),
    (r"ctypes\.", "ctypes usage"),
    (r"pickle\.loads?\(", "pickle deserialization"),
    (r"marshal\.loads?\(", "marshal deserialization"),
    (r"compile\(.+exec", "code compilation"),
    (r"requests\.(get|post|put|delete|patch)\(", "HTTP request"),
    (r"httpx\.(get|post|put|delete|patch|AsyncClient|Client)\(", "HTTP request (httpx)"),
    (r"urllib\.request\.(urlopen|Request)\(", "HTTP request (urllib)"),
    (r"os\.environ", "environment variable access"),
    (r"os\.getenv\(", "environment variable access"),
]

_SECRET_PATTERNS = [
    (r"(?i)sk-[a-zA-Z0-9]{20,}", "potential API key"),
    (r"(?i)ghp_[a-zA-Z0-9]{36}", "GitHub token"),
    (r"(?i)AKIA[0-9A-Z]{16}", "AWS access key"),
    (r"(?i)-----BEGIN (RSA |EC )?PRIVATE KEY-----", "private key"),
]


def scan_generated_code(code_files: list[dict]) -> list[dict]:
    """Scan generated code files for security issues.

    Returns list of findings. Each finding is a dict with:
      severity, finding_type, description, file_path
    """
    findings: list[dict] = []

    for file_info in code_files:
        path = file_info.get("path", "") if isinstance(file_info, dict) else getattr(file_info, "path", "")
        content = file_info.get("content", "") if isinstance(file_info, dict) else getattr(file_info, "content", "")

        if not content or not path.endswith(".py"):
            continue

        for line_no, line in enumerate(content.splitlines(), start=1):
            for pattern, desc in _DANGEROUS_CODE_PATTERNS:
                if re.search(pattern, line):
                    findings.append({
                        "severity": "medium",
                        "finding_type": "dangerous_pattern",
                        "description": f"{desc} in {path}:{line_no}",
                        "file_path": path,
                    })

            for pattern, desc in _SECRET_PATTERNS:
                if re.search(pattern, line):
                    findings.append({
                        "severity": "high",
                        "finding_type": "secret_detected",
                        "description": f"{desc} in {path}:{line_no}",
                        "file_path": path,
                    })

    return findings


def has_critical_findings(findings: list[dict]) -> bool:
    """Check if any findings are critical enough to block the response.

    Blocks on:
    - Any high-severity finding (e.g., hardcoded secrets)
    - Combination of env var access + network requests (data exfiltration pattern)
    """
    if any(f["severity"] == "high" for f in findings):
        return True

    # Detect exfiltration combo: env var access + HTTP requests in same package
    descriptions = [f["description"] for f in findings]
    has_env_access = any("environment variable" in d for d in descriptions)
    has_network = any("HTTP request" in d for d in descriptions)
    if has_env_access and has_network:
        return True

    return False
