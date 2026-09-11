"""Talking to one certificate and to nothing else.

A gateway that made its own certificate is not in anybody's trust store, and it should not be: the
thing that authorised this client was an invitation a person handed over, and that invitation
carries the certificate's digest. So the client checks the far side against that digest, and the
web's certificate authorities are not consulted, because they have nothing to say about it.

## The order

The check happens in `connect()` -- after the handshake and BEFORE a single byte of the request is
written. A client that sent its token and then looked would have sent its token to whatever
answered. There is no code path here that writes a request first.

## Why the context is made here and not passed in

The context this uses does not verify a chain, because there is no chain to verify: the pin is the
trust anchor. A context like that is dangerous in anybody else's hands, so it is not in anybody
else's hands -- it is built inside the connection that performs the check, and there is no way to
obtain one without the check attached. The same shape as `Answer` in the verification tool, for the
same reason.

## What a pin is

A pin is to a KEY. It says this is the thing that issued your invitation. It does not say who owns
it, what organisation it belongs to, or that anyone has vouched for it -- and the hostname in the
certificate is not checked, because the pin is stronger than a name and the name may legitimately
be an address today and a domain tomorrow.
"""
from __future__ import annotations

import hashlib
import http.client
import ssl
import urllib.request


class WrongCertificate(Exception):
    """The far side is not the gateway this client paired with."""


class PinnedConnection(http.client.HTTPSConnection):
    """An HTTPS connection that refuses anything but one certificate.

    The context is built here rather than accepted from a caller: one that does not verify a
    chain is only safe with the check below attached to it, so the two are not separable.
    """

    def __init__(self, host, *args, pin: str = "", **kwargs) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        # Not because verification does not matter -- because the pin IS the verification, and a
        # chain to somebody else's authority would be a second, weaker opinion about the same
        # question.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        kwargs.pop("context", None)
        super().__init__(host, *args, context=context, **kwargs)
        self.pin = (pin or "").lower()

    def connect(self) -> None:
        super().connect()
        if not self.pin:
            self.close()
            raise WrongCertificate(
                "this connection has no certificate to expect, so there is nothing to check the "
                "far side against. Pair again with an invitation from the gateway.")
        der = self.sock.getpeercert(binary_form=True) if self.sock is not None else None
        if not der:
            self.close()
            raise WrongCertificate(
                "the far side presented no certificate, so it cannot be the gateway this client "
                "paired with.")
        got = hashlib.sha256(der).hexdigest()
        if got != self.pin:
            # Closed before anything is written. Nothing of this request -- not the token, not the
            # artefact -- has reached whatever this is.
            self.close()
            raise WrongCertificate(
                "the sandbox at " + str(self.host) + " presented a different certificate from the "
                "one this client paired with.\n"
                "  expected  " + self.pin[:16] + "...\n"
                "  presented " + got[:16] + "...\n"
                "Nothing was sent. Either the gateway was given a new certificate -- in which "
                "case pair again with a new invitation -- or something else is answering at that "
                "address.")


class _PinnedHandler(urllib.request.HTTPSHandler):
    def __init__(self, pin: str) -> None:
        super().__init__()
        self.pin = pin

    def https_open(self, req):
        def make(host, **kwargs):
            kwargs.pop("context", None)
            kwargs.pop("check_hostname", None)
            return PinnedConnection(host, pin=self.pin, **kwargs)

        return self.do_open(make, req)


def opener_for(pin: str, *extra):
    """An opener that will talk to that certificate and to nothing else."""
    return urllib.request.build_opener(_PinnedHandler(pin), *extra)
