"""EM-3C: the self-hosted remote sandbox gateway.

A person runs this on a Linux server they own. Their AgentNode client sends work to it; the
gateway runs that work in a container on its own machine and sends back status, logs and the
result. The client never runs the foreign code, and neither does the gateway's own process.

Architecture, decided as `EM3C-ARCHITECTURE-0001` and not reopened here:

* **T-C** -- the gateway is measured, and every measurement is bound to its identity and version,
  so a client can tell whether what it knows still describes what it is talking to.
* **S-B** -- the client sends the work together with the properties it requires, and the gateway
  refuses server-side when it cannot satisfy them. The refusal is the enforcement point.
"""
from agentnode_sdk.gateway.identity import (
    GatewayIdentity,
    GatewayState,
    PairingError,
    client_token_secret,
    new_pairing_code,
    normalise_code,
)
from agentnode_sdk.gateway.protocol import (
    PROTOCOL_VERSION,
    JobRequest,
    NonceCache,
    ProtocolError,
    canonical_bytes,
    check_freshness,
    digest,
    policy_digest,
    sign,
    verify_signature,
)

__all__ = [
    "PROTOCOL_VERSION",
    "GatewayIdentity",
    "GatewayState",
    "JobRequest",
    "NonceCache",
    "PairingError",
    "ProtocolError",
    "canonical_bytes",
    "check_freshness",
    "client_token_secret",
    "digest",
    "new_pairing_code",
    "normalise_code",
    "policy_digest",
    "sign",
    "verify_signature",
]
