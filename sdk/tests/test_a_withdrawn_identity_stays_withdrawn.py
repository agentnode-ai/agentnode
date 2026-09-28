"""Revoking a worker, and the one step that used to bring it straight back.

Revocation is by SERIAL. Issue a new certificate for the same instance name and it has a new
serial, which is not in the revocation list -- and every other check passes, because the
certificate is perfectly genuine: this deployment's CA signed it, it is current, the usage fits,
the name is in the accept list. The worker that was taken away is back, and nothing noticed.

`recover_entry` already revokes every serial an entry ever had and locks renewal. What it cannot
do is tell a REMOTE verifier anything: a worker on another machine has no issuer inventory to
consult and must be able to prove the withdrawal from something it holds.

So a withdrawn identity gets a tombstone -- the URI itself, in a list signed by the deployment
CA, judged at the effective time like the revocation list, and refused rather than defaulted to
empty when it cannot be believed.

NOT A HOST-ISOLATION TEST: one process, one kernel.
"""
from __future__ import annotations

import json

import pytest

from agentnode_sdk.pki import tombstones as T

DEPLOYMENT = "alpha"
A_WORKER = "agentnode://alpha/worker/w1"


class Signer:
    """A throwaway EC key standing in for the deployment CA."""

    def __init__(self):
        from cryptography.hazmat.primitives.asymmetric import ec

        self.key = ec.generate_private_key(ec.SECP256R1())

    def sign(self, body: bytes) -> bytes:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        return self.key.sign(body, ec.ECDSA(hashes.SHA256()))

    def verifier(self, body: bytes, signature: bytes) -> None:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        self.key.public_key().verify(signature, body, ec.ECDSA(hashes.SHA256()))


@pytest.fixture()
def ca():
    return Signer()


class TestTheListIsAuthenticated:

    def test_a_list_this_deployments_ca_signed_is_believed(self, ca):
        raw = T.publish(DEPLOYMENT, [A_WORKER], now=1000.0, signer=ca.sign)
        said = T.read(raw, deployment=DEPLOYMENT, verifier=ca.verifier)
        assert said.holds(A_WORKER)

    def test_a_list_signed_by_somebody_else_is_refused(self, ca):
        """The whole reason the list is signed: it can then be distributed by a file copy, a
        configuration manager or anything else, and none of those has to be trusted."""
        raw = T.publish(DEPLOYMENT, [A_WORKER], now=1000.0, signer=Signer().sign)
        with pytest.raises(T.ListUnusable) as refused:
            T.read(raw, deployment=DEPLOYMENT, verifier=ca.verifier)
        assert refused.value.reason == T.BAD_SIGNATURE

    def test_an_edited_list_is_refused(self, ca):
        """Taking a name OUT is the edit an attacker wants, so it is the one tested."""
        raw = T.publish(DEPLOYMENT, [A_WORKER], now=1000.0, signer=ca.sign)
        tampered = json.loads(raw.decode())
        tampered["body"]["uris"] = []
        with pytest.raises(T.ListUnusable) as refused:
            T.read(json.dumps(tampered).encode(), deployment=DEPLOYMENT, verifier=ca.verifier)
        assert refused.value.reason == T.BAD_SIGNATURE

    def test_a_field_added_outside_the_signature_is_refused(self, ca):
        raw = T.publish(DEPLOYMENT, [A_WORKER], now=1000.0, signer=ca.sign)
        tampered = json.loads(raw.decode())
        tampered["body"]["extra"] = "smuggled"
        with pytest.raises(T.ListUnusable) as refused:
            T.read(json.dumps(tampered).encode(), deployment=DEPLOYMENT, verifier=ca.verifier)
        assert refused.value.reason == T.MALFORMED

    def test_a_list_for_another_deployment_is_refused(self, ca):
        raw = T.publish("somebody-else", [A_WORKER], now=1000.0, signer=ca.sign)
        with pytest.raises(T.ListUnusable) as refused:
            T.read(raw, deployment=DEPLOYMENT, verifier=ca.verifier)
        assert refused.value.reason == T.NOT_OURS

    def test_rubbish_is_refused(self, ca):
        with pytest.raises(T.ListUnusable):
            T.read(b"{not json", deployment=DEPLOYMENT, verifier=ca.verifier)


class TestItExpires:

    def test_a_list_that_stopped_being_published_stops_being_believed(self, ca):
        raw = T.publish(DEPLOYMENT, [A_WORKER], now=1000.0, signer=ca.sign)
        said = T.read(raw, deployment=DEPLOYMENT, verifier=ca.verifier)
        said.usable_at(1000.0 + T.VALID_SECONDS - 1)
        with pytest.raises(T.ListUnusable) as refused:
            said.usable_at(1000.0 + T.VALID_SECONDS + 1)
        assert refused.value.reason == T.EXPIRED

    def test_it_is_refreshed_well_inside_its_validity(self):
        """So a root run that misses a few ticks does not take the services down."""
        assert T.REFRESH_AFTER_SECONDS * 3 <= T.VALID_SECONDS


class TestWhatAVerifierDoesWithIt:

    def test_a_missing_list_where_one_is_required_is_a_refusal(self):
        """"I could not read who is banned" is not "nobody is banned"."""
        from agentnode_sdk.pki.identity import PeerRefused
        from agentnode_sdk.pki.trust import TrustView

        view = TrustView(anchor=None, revocation_list=None, floor=None, floor_problem="",
                         role="gateway", identity_tombstones=None, tombstones_required=True)
        with pytest.raises(PeerRefused):
            view.withdrawn(1000.0, A_WORKER)

    def test_and_where_none_is_configured_nothing_is_withdrawn(self):
        """A deployment that has not turned this on is different from one whose list vanished."""
        from agentnode_sdk.pki.trust import TrustView

        view = TrustView(anchor=None, revocation_list=None, floor=None, floor_problem="",
                         role="gateway", identity_tombstones=None, tombstones_required=False)
        assert view.withdrawn(1000.0, A_WORKER) == frozenset()

    def test_the_check_is_part_of_checking_a_peer(self):
        import inspect

        from agentnode_sdk.pki import identity

        source = inspect.getsource(identity.check_peer)
        assert "withdrawn" in source
        assert source.index("_not_revoked") < source.index("withdrawn"), (
            "the serial check and the identity check are different checks, in that order")


class TestWhatTheIssuerPutsInIt:

    def _inventory(self, **entries):
        return {"deployment": DEPLOYMENT, "entries": entries}

    def _withdrawn(self, inventory):
        from agentnode_sdk.pki.issuer import Issuer

        return Issuer._withdrawn_identities(None, inventory)

    def test_an_entry_whose_renewal_is_locked_is_withdrawn(self):
        """What `recover_entry` does to a compromised entry, and exactly the case where
        re-issuing under the same name must not bring the holder back."""
        got = self._withdrawn(self._inventory(
            w1={"uri": A_WORKER, "renewal_locked": True, "certificates": []}))
        assert got == {A_WORKER}

    def test_an_entry_whose_every_certificate_is_revoked_is_withdrawn(self):
        got = self._withdrawn(self._inventory(
            w1={"uri": A_WORKER, "certificates": [{"status": "revoked"},
                                                  {"status": "superseded"}]}))
        assert got == {A_WORKER}

    def test_an_entry_with_a_live_certificate_is_not(self):
        got = self._withdrawn(self._inventory(
            w1={"uri": A_WORKER, "certificates": [{"status": "revoked"},
                                                  {"status": "current"}]}))
        assert got == set()

    def test_an_entry_that_was_never_claimed_is_not_withdrawn(self):
        """Nothing was ever issued for it, so there is nothing to take away."""
        got = self._withdrawn(self._inventory(w1={"uri": A_WORKER, "certificates": []}))
        assert got == set()

    def test_an_entry_in_its_renewal_overlap_is_not_withdrawn(self):
        got = self._withdrawn(self._inventory(
            w1={"uri": A_WORKER, "certificates": [{"status": "overlapping"}]}))
        assert got == set()


class TestTheWithdrawalIsPermanent:

    def test_there_is_no_way_to_take_a_name_back_out(self):
        """A replacement takes a NEW name. That is better than reinstating an old one for a
        second reason: the health binding notices a different worker and re-measures, where
        reinstating would look like nothing had changed."""
        assert not hasattr(T, "unpublish")
        assert not hasattr(T.Tombstones, "remove")
        source = open(T.__file__, encoding="utf-8").read()
        assert "A tombstone is permanent" in source
