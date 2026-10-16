"""RFC 9728 Protected Resource Metadata + the RFC 6750 ``WWW-Authenticate`` challenge (R3).

The resource server (our /mcp) publishes, at
``https://mcp.agentnode.net/.well-known/oauth-protected-resource``, a document that names
the EXACT protected resource identifier and the one authorization server a client must use,
and on any unauthenticated/invalid request answers ``401`` with a ``WWW-Authenticate: Bearer``
header carrying ``resource_metadata=<that URL>`` so a spec-conformant client can discover how
to authenticate. The resource server itself never fetches a client metadata document; that is
the authorization server's job (see :mod:`oauth_as`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ResourceConfig:
    resource: str                 # exact MCP resource URI, e.g. https://mcp.agentnode.net/mcp
    authorization_server: str     # the AS issuer, e.g. https://mcp.agentnode.net
    scopes_supported: tuple[str, ...] = ("mcp:use",)

    @property
    def prm_path(self) -> str:
        return "/.well-known/oauth-protected-resource"

    @property
    def prm_url(self) -> str:
        parts = urlsplit(self.resource)
        return "%s://%s%s" % (parts.scheme, parts.netloc, self.prm_path)

    def document(self) -> dict:
        """The RFC 9728 metadata document. ``resource`` MUST exactly equal the protected
        resource a client requests a token for (the audience we then enforce)."""
        return {
            "resource": self.resource,
            "authorization_servers": [self.authorization_server],
            "bearer_methods_supported": ["header"],
            "scopes_supported": list(self.scopes_supported),
            "resource_documentation": "%s/docs" % self.authorization_server,
        }

    def document_bytes(self) -> bytes:
        return json.dumps(self.document(), indent=1, sort_keys=True).encode("utf-8")

    def challenge(self, *, error: str | None = None, error_description: str | None = None) -> str:
        """The ``WWW-Authenticate`` value for a 401/403. Carries the PRM URL so a client can
        discover the AS. ``error``/``error_description`` are RFC 6750 fields; never put a
        secret or the presented token in them."""
        parts = ['Bearer resource_metadata="%s"' % self.prm_url]
        if error:
            parts.append('error="%s"' % error)
        if error_description:
            # keep it reason-only; callers pass stable codes, never secrets
            parts.append('error_description="%s"' % error_description.replace('"', ""))
        return ", ".join(parts)
