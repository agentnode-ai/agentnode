"""Production login: bind the remote-MCP OAuth ``/authorize`` step to the REAL AgentNode gateway
accounts, devices, browser sessions, capabilities and revocations (profile R3/R4, /goal #2).

The transport needs only *a* way to establish who is authorizing. On the restricted test stand
that is the pre-shared-secret shim in :mod:`server` (``X-AgentNode-Login``). In PRODUCTION it is
this provider, which reads the gateway's own ``__Host-agentnode`` HttpOnly session cookie and
turns it into the account identity THROUGH the frozen access-control decision code
(:func:`agentnode_sdk.access.dispatch.identify_session` / :func:`a_confirmed_session`) so the
browser never becomes a second permission system:

    cookie -> Sessions.whose -> device (client_id) -> identify_client
          -> account_id + capabilities -> account must be ACTIVE, device not withdrawn,
             and hold the RUN capability -> principal_id (+ the ``mcp:use`` scope)

The OAuth scope ``mcp:use`` is granted ONLY to a device that holds ``RUN``, so the OAuth grant
is a projection of the existing capability model, not a parallel one. Revocation is immediate
and needs nothing here: withdrawing the device ends its sessions (``identify_session`` -> nobody),
suspending the account fails the ACTIVE check, and :meth:`TokenStore.revoke_all_for` bumps the
principal's generation so tokens already minted stop verifying.

``X-AgentNode-Login`` is never read here. The stand shim lives only in ``build_stand_app``; the
production app constructs this provider and no resolver, so that header is structurally dead in
production (asserted by a counter-check).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional

from agentnode_sdk.access import contract
from agentnode_sdk.access import dispatch
from agentnode_sdk.access.rest import CSRF_HEADER, SESSION_COOKIE, _cookie, _header
from agentnode_sdk.access.sessions import Sessions
from agentnode_sdk.gateway import accounts as _accounts
from agentnode_sdk.gateway.identity import GatewayState

MCP_USE_SCOPE = "mcp:use"


@dataclass(frozen=True)
class Subject:
    """An authenticated, consenting account as the login provider established it.

    ``principal_id`` is the gateway account id (``acct-…``/``solo:…``). ``scopes`` is what the
    device is actually permitted (``mcp:use`` iff it holds RUN). ``consent_token`` is a fresh
    CSRF value to embed in the consent form; the POST must echo it back.
    """

    principal_id: str
    scopes: tuple[str, ...]
    display_name: str = ""
    consent_token: str = ""
    permitted: bool = True


class GatewayLogin:
    """Resolve the gateway browser session into an account, read-only over the frozen stores."""

    def __init__(self, gateway_dir: str, *, version: str = "remote-mcp",
                 clock=time.time) -> None:
        state = GatewayState(gateway_dir, version)
        sessions = Sessions(gateway_dir, clock)
        # identify_session/identify_client/a_confirmed_session need exactly these two members.
        self._svc = SimpleNamespace(state=state, sessions=sessions)
        self._accounts = state.accounts

    # ---- helpers --------------------------------------------------------- #
    def _scopes_for(self, principal) -> tuple[str, ...]:
        return (MCP_USE_SCOPE,) if contract.RUN in (principal.capabilities or ()) else ()

    def _account_active(self, account_id: str) -> bool:
        try:
            return self._accounts.get(account_id).state == _accounts.ACTIVE
        except Exception:  # noqa: BLE001 — an unreadable account is not "active"
            return False

    def _subject_from(self, principal, consent_token: str) -> Optional[Subject]:
        if not principal.authenticated:
            return None
        if not self._account_active(principal.account_id):
            return None
        scopes = self._scopes_for(principal)
        return Subject(principal_id=principal.account_id, scopes=scopes,
                       display_name=principal.device_name, consent_token=consent_token,
                       permitted=bool(scopes))

    # ---- the two seams the transport calls ------------------------------- #
    def subject(self, scope: dict, headers: dict) -> Optional[Subject]:
        """GET /authorize: who is signed in (from the cookie). None ⇒ login required."""
        session_id = _cookie(headers, SESSION_COOKIE)
        who = dispatch.identify_session(self._svc, session_id, "", via="browser")
        if not who.authenticated or not self._account_active(who.account_id):
            return None
        token = self._svc.sessions.new_confirmation(session_id)   # fresh CSRF for the form
        return self._subject_from(who, token)

    def confirmed_subject(self, scope: dict, headers: dict,
                          form: dict) -> Optional[Subject]:
        """POST /authorize: the signed-in browser confirmed, with a matching CSRF token."""
        session_id = _cookie(headers, SESSION_COOKIE)
        presented = form.get("confirm") or _header(headers, CSRF_HEADER)
        who = dispatch.a_confirmed_session(self._svc, session_id, presented)
        return self._subject_from(who, presented)
