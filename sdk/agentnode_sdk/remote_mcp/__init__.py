"""AgentNode Remote MCP service -- a thin, stateless Streamable-HTTP transport in FRONT of
the frozen stdio ``UsabilityMCPAdapter`` + ``UsabilityClient.prepare -> submit`` contract.

Nothing in this package modifies the frozen capability layer, worker protocol, Job/Outcome,
sandbox, gateway, approval or audit. It adds only: transport (MCP Streamable HTTP
2026-07-28, with an optional deliberately-negotiated 2025-11-25 dual-era path), one shared
bearer-token verifier fed by two issuance paths (interactive OAuth 2.1 + human-minted
personal/service tokens), Host/Origin enforcement, request limits, and the OAuth/CIMD/PRM
discovery surface. A transport token NEVER substitutes the operator-signed, single-use,
call-bound human approval: an authenticated ``tools/call`` with no deposited approval still
returns ``approval_required`` exactly as the stdio adapter does.

In production the transport fronts the per-account capability catalog (``catalog``,
``catalog_adapter``, ``catalog_audit``, operator door ``catalog_cli``) instead of a single
capability; it reuses the same ``UsabilityClient.prepare -> submit`` path.

Decisions: ``remote-mcp-service/review/Q-RMCP-0001-decision.answer.md`` and
``Q-RMCP-0002-compat-extension.answer.md``. Frozen profile:
``remote-mcp-service/frozen/remote-mcp-profile.json``
(sha256 40edaf898aca3f509231f795fcbf9c7b835f0607793588a2fc7d81591ad57aa2).
"""
