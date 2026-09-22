"""The deployment's own identities for the gateway and the worker, and the transport's checks.

Built to `mtls-transport-decision.md` (MTLS-TRANSPORT-DECISION-0014), stages 1 to 5:

    identity.py     the identity grammar and the peer checks that are not OpenSSL's (2 to 6)
    issuer.py       the issuer: inventory, enrollment, renewal over a bounded overlap,
                    revocation, recovery from a compromised key, and the root run (`tick`)
    revocation.py   the signed revocation list: made from the inventory, read by the services
    floor.py        the time floor: written only by root, read by the services, and the
                    effective time every validity judgement is made at
    trust.py        what a service reads of root's files at one moment, judged when asked
    files.py        every file operation the issuer makes, through one seam a test can crash,
                    including the two-stage publication of what the services read

What is still NOT here is the decision's gate after stage 5: nothing in this package lets either
end of the transport leave loopback.
"""
