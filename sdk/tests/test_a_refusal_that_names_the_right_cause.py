"""A client whose hosts the operator removed is not a client that named no hosts.

WHAT WAS MEASURED. `EG3` of the beta profile -- *"A client or a job can only narrow the operator policy,
never widen it. An attempt to widen is refused and the refusal is recorded"* -- was left at WARN by the
third acceptance with this reason:

    "Widening was refused, but the remedy text incorrectly tells a client that already named hosts to
     name them."

The refusal works. The remedy beside it does not, and the mistake is a particular one.
`gateway/server.py` tests the **effective** destination list -- the one left after the operator's policy
has been folded in, and the comment above it says so deliberately -- and then blames the **job**:

    "this job asked for a restricted network but named no host it may reach, so there is nothing to
     allow. Name the hosts, or ask for no network at all."

A client that named three hosts, all of which the operator's policy removed, is told to name the hosts it
named. Two different states have been collapsed into one sentence, and only one of them is about the
client: *you named none*, and *the operator allows none of the ones you named*. The second needs the
operator, not the client, and the remedy as written sends the customer in a circle.

WHAT THIS FILE ASKS FOR, which is `C1`, `C2` and `C3` of this arc's profile: the three cases told apart,
each with its own statement; a remedy that matches the actual cause; and no disclosure of internal policy
content beyond what the client needs in order to act. The client's OWN hosts are not disclosure -- it
already knows them -- while the operator's list is.

Driven through `admit`, like `test_only_narrowing.py`, because what a customer needs is not that a
function narrows but that this gateway refuses and says why.
"""
from __future__ import annotations

import pytest

from agentnode_sdk.sandbox.contract import NetworkRules, Retention, SandboxPolicy
# The driver and the fixtures already exist and are the ones the narrowing tests use. Writing a second
# `_admit_asking` would be two slightly different definitions of "a real admission".
from tests.test_only_narrowing import _admit_asking, gateway  # noqa: F401


def _an_operator_allowing(*hosts):
    """An operator policy that allows exactly these destinations and nothing else."""
    return SandboxPolicy(
        network=NetworkRules(enabled=True, allowed_destinations=frozenset(hosts)),
        retention=Retention(),
    )


def _said(refusal) -> str:
    return "" if refusal is None else str(refusal)


class TestTheThreeCasesAreToldApart:
    """`C1`: no host requested; hosts requested but none permitted; a valid intersection."""

    def test_1_a_job_that_asked_for_a_restricted_network_and_named_nothing(self, gateway):
        """The case the current sentence is actually about, and it keeps its words."""
        refusal = _admit_asking(gateway, network="restricted", domains=(),
                                operator=_an_operator_allowing("example.com"))

        said = _said(refusal)
        assert refusal is not None, "a restricted network with no destination was admitted"
        assert "named no host" in said, (
            "the job that really named nothing was not told so; it was told %r" % said)

    def test_2_a_job_whose_hosts_the_operator_removed_is_not_told_it_named_none(self, gateway):
        """THE DEFECT. The client named a host; the operator allows a different one."""
        refusal = _admit_asking(gateway, network="restricted", domains=("wanted.example",),
                                operator=_an_operator_allowing("allowed.example"))

        said = _said(refusal)
        assert refusal is not None, "a job with no permitted destination was admitted"
        # SAFETY FIRST, then the cause: a red here must name the wrong statement, not the absence
        # of a refusal.
        assert "named no host" not in said, (
            "a client that named a host was told it named none: %r" % said)
        assert "Name the hosts" not in said, (
            "a client whose hosts the operator removed was told to name them: %r" % said)
        assert "wanted.example" in said, (
            "the refusal did not name the host this client asked for, so the client cannot tell "
            "which of its own destinations was removed: %r" % said)

    def test_3_and_a_valid_intersection_is_not_refused_at_all(self, gateway):
        refusal = _admit_asking(gateway, network="restricted", domains=("allowed.example",),
                                operator=_an_operator_allowing("allowed.example", "other.example"))

        assert refusal is None, (
            "a job asking for a host the operator allows was refused: %r" % _said(refusal))


class TestTheRemedyMatchesTheCause:
    """`C2`: the remedy has to be addressed to whoever can act on it."""

    def test_4_the_remedy_for_a_removed_host_points_at_the_operator(self, gateway):
        refusal = _admit_asking(gateway, network="restricted", domains=("wanted.example",),
                                operator=_an_operator_allowing("allowed.example"))

        said = _said(refusal).lower()
        assert "operator" in said or "whoever runs this sandbox" in said, (
            "the remedy does not say who can change this; it said %r" % _said(refusal))

    def test_5_and_the_remedy_for_naming_nothing_points_at_the_client(self, gateway):
        refusal = _admit_asking(gateway, network="restricted", domains=(),
                                operator=_an_operator_allowing("example.com"))

        said = _said(refusal)
        assert "Name the hosts" in said or "name the hosts" in said, (
            "a client that named nothing was not told to name hosts: %r" % said)


class TestItDoesNotDiscloseThePolicy:
    """`C3`: what the client needs in order to act, and not the operator's list."""

    def test_6_the_operators_other_destinations_are_not_named(self, gateway):
        """The client asked for one host. The operator allows two OTHER ones.

        Telling the client which hosts the operator allows is telling it about an allowlist it has
        no business reading -- and on a shared gateway that list is somebody else's configuration.
        """
        refusal = _admit_asking(
            gateway, network="restricted", domains=("wanted.example",),
            operator=_an_operator_allowing("secret-one.example", "secret-two.example"))

        said = _said(refusal)
        assert refusal is not None, "a job with no permitted destination was admitted"
        assert "secret-one.example" not in said and "secret-two.example" not in said, (
            "the refusal disclosed the operator's own allowlist: %r" % said)

    def test_7_and_it_says_how_many_of_the_clients_hosts_survived(self, gateway):
        """Nought, here -- and saying so is not disclosure: it is about the client's own list."""
        refusal = _admit_asking(
            gateway, network="restricted", domains=("a.example", "b.example"),
            operator=_an_operator_allowing("c.example"))

        said = _said(refusal)
        assert "a.example" in said and "b.example" in said, (
            "the refusal did not name the client's own destinations, so the client cannot tell "
            "which of them to ask about: %r" % said)
