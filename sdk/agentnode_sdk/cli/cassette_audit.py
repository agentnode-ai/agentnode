"""Audit VCR cassettes for dynamic/sensitive fields that harm determinism."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class AuditFinding:
    cassette: str
    path: str
    category: str
    value_preview: str


@dataclass
class AuditResult:
    findings: list[AuditFinding] = field(default_factory=list)
    cassettes_checked: int = 0

    @property
    def has_warnings(self) -> bool:
        return len(self.findings) > 0

    @property
    def has_secrets(self) -> bool:
        return any(f.category == "secret" for f in self.findings)


_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)
_ISO_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
)
_UNIX_TIMESTAMP_RE = re.compile(
    r"(?<!\d)1[6-9]\d{8}(?:\.\d+)?(?!\d)"
)
_JWT_RE = re.compile(
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
)
_HEX_TOKEN_RE = re.compile(
    r"(?<![0-9a-f])[0-9a-f]{32,}(?![0-9a-f])", re.I
)
_SECRET_PREFIX_RE = re.compile(
    r"(sk-|pk_live_|pk_test_|ghp_|gho_|Bearer\s+\S{20,})", re.I
)

_DYNAMIC_HEADER_NAMES = {
    "x-request-id", "x-trace-id", "x-correlation-id", "request-id",
    "x-amzn-requestid", "x-amzn-trace-id", "cf-ray",
    "x-ratelimit-remaining", "x-ratelimit-reset",
    "date", "x-response-time", "x-runtime",
}

_DYNAMIC_BODY_KEYS = {
    "created_at", "updated_at", "timestamp", "ts", "date",
    "request_id", "trace_id", "correlation_id", "session_id",
    "nonce", "csrf_token", "expires_at", "expires_in",
}


def audit_cassettes(cassette_paths: list[Path]) -> AuditResult:
    """Scan cassettes for dynamic/sensitive content."""
    result = AuditResult()

    for path in cassette_paths:
        if not path.exists():
            continue
        result.cassettes_checked += 1

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            data = yaml.safe_load(content)
        except Exception:
            continue

        if not isinstance(data, dict):
            continue

        cassette_name = path.name
        interactions = data.get("interactions", [])

        for i, interaction in enumerate(interactions):
            if not isinstance(interaction, dict):
                continue

            response = interaction.get("response", {})
            if not isinstance(response, dict):
                continue

            _audit_headers(
                response.get("headers", {}),
                cassette_name,
                f"interactions[{i}].response.headers",
                result,
            )

            _audit_body(
                response.get("body", {}),
                cassette_name,
                f"interactions[{i}].response.body",
                result,
            )

    return result


def _audit_headers(
    headers: dict, cassette: str, prefix: str, result: AuditResult
) -> None:
    if not isinstance(headers, dict):
        return

    for name, values in headers.items():
        name_lower = name.lower()

        if name_lower in _DYNAMIC_HEADER_NAMES:
            val_preview = _preview(values)
            result.findings.append(AuditFinding(
                cassette=cassette,
                path=f"{prefix}.{name}",
                category="dynamic",
                value_preview=val_preview,
            ))

        val_str = str(values)
        _check_secrets(val_str, cassette, f"{prefix}.{name}", result)


def _audit_body(
    body: dict | str, cassette: str, prefix: str, result: AuditResult
) -> None:
    if isinstance(body, dict):
        body_str = body.get("string", "")
    elif isinstance(body, str):
        body_str = body
    else:
        return

    if not body_str:
        return

    try:
        parsed = yaml.safe_load(body_str) if body_str.strip().startswith("{") else None
    except Exception:
        parsed = None

    if isinstance(parsed, dict):
        _walk_dict(parsed, cassette, prefix, result)
    else:
        _check_patterns(body_str, cassette, prefix, result)


def _walk_dict(
    obj: dict, cassette: str, prefix: str, result: AuditResult, depth: int = 0
) -> None:
    if depth > 5:
        return

    for key, value in obj.items():
        path = f"{prefix}.{key}"
        key_lower = key.lower()

        if key_lower in _DYNAMIC_BODY_KEYS:
            result.findings.append(AuditFinding(
                cassette=cassette,
                path=path,
                category="dynamic",
                value_preview=_preview(value),
            ))
            continue

        if isinstance(value, str):
            _check_patterns(value, cassette, path, result)
        elif isinstance(value, dict):
            _walk_dict(value, cassette, path, result, depth + 1)
        elif isinstance(value, list):
            for i, item in enumerate(value[:5]):
                if isinstance(item, dict):
                    _walk_dict(item, cassette, f"{path}[{i}]", result, depth + 1)
                elif isinstance(item, str):
                    _check_patterns(item, cassette, f"{path}[{i}]", result)


def _check_patterns(value: str, cassette: str, path: str, result: AuditResult) -> None:
    if len(value) > 2000:
        value = value[:2000]

    _check_secrets(value, cassette, path, result)

    if _UUID_RE.search(value):
        result.findings.append(AuditFinding(
            cassette=cassette, path=path, category="uuid",
            value_preview=_preview(value),
        ))
        return

    if _ISO_TIMESTAMP_RE.search(value):
        result.findings.append(AuditFinding(
            cassette=cassette, path=path, category="timestamp",
            value_preview=_preview(value),
        ))
        return

    if _UNIX_TIMESTAMP_RE.search(value):
        result.findings.append(AuditFinding(
            cassette=cassette, path=path, category="timestamp",
            value_preview=_preview(value),
        ))


def _check_secrets(value: str, cassette: str, path: str, result: AuditResult) -> None:
    if _JWT_RE.search(value):
        result.findings.append(AuditFinding(
            cassette=cassette, path=path, category="secret",
            value_preview="JWT token detected",
        ))
        return

    if _SECRET_PREFIX_RE.search(value):
        result.findings.append(AuditFinding(
            cassette=cassette, path=path, category="secret",
            value_preview="API key/token pattern detected",
        ))
        return

    if _HEX_TOKEN_RE.search(value) and len(value) < 200:
        result.findings.append(AuditFinding(
            cassette=cassette, path=path, category="possible_token",
            value_preview=_preview(value),
        ))


def _preview(value) -> str:
    s = str(value)
    if len(s) > 60:
        return s[:57] + "..."
    return s
