"""The deployment's own identities for the gateway and the worker, and the transport's checks.

Built to `mtls-transport-decision.md` (MTLS-TRANSPORT-DECISION-0014), stages 1 to 4:

    identity.py   the identity grammar and the peer checks that are not OpenSSL's
    issuer.py     the issuer: inventory, enrollment, renewal, the durable transaction
    files.py      every file operation the issuer makes, through one seam a test can crash

Stage 5 of the decision -- the signed revocation list and the rollback-resistant time floor --
is not here. Until it is, no peer is checked for revocation and certificate validity is judged
by the system clock.
"""
