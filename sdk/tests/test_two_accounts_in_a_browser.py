"""Two customers, two real browsers, one gateway.

`test_two_accounts*.py` establish that the DECISION is right, at the dispatcher and at every HTTP
door. A reviewer was right that neither of them is the console: the console holds a credential the
browser attaches on its own, renders whatever comes back, and keeps state between screens. Every
one of those is a place where one customer's data could appear in another customer's window
without any decision being wrong.

So this drives two separate browser contexts -- separate cookie jars, separate storage, the same
gateway -- signs each of them in through its own invitation, and then reads the SCREEN. What is
asserted is what a person would see.

Three of these tests reach into the page with `evaluate`, and they say so where they do it. They
are not a person's path: they ask what somebody who has this browser's credential can obtain with
it, which is a question about the credential rather than about the buttons, and a person cannot
ask it by clicking.
"""
from __future__ import annotations

import json

import pytest

# The gateway fixture and the page object, rather than copies of them. The import is also what
# makes a missing browser FAIL here under AGENTNODE_BROWSER_TESTS=required, in exactly the same
# way and for the same reason as in the file it comes from -- that module skips itself at import
# time when Playwright is absent, and importing it is how this one inherits that.
#
# `browser` is NOT imported. It is a session fixture in `conftest.py`, shared: two modules each
# holding their own `sync_playwright()` is one too many for a single thread.
from tests.test_console_browser import (  # noqa: F401
    PATIENCE,
    Console,
    gateway,
)
from tests.test_console_browser import expect  # noqa: F401


class Customer(Console):
    """One person, in one browser, with their own cookie jar."""

    def arrive(self, name):
        """Sign in and get to the part of the console a customer lives in."""
        self.sign_in(name=name)
        self.choose("none")
        self.tab.click("#use-console-anyway")
        self.tab.get_by_role("button", name="Überspringen").click()
        return self

    def look_at(self, tab_name):
        self.tab.get_by_role("button", name=tab_name).click()
        return self

    def connections(self):
        """Waits for the list to have ARRIVED, not merely for its box to be on screen.

        Both these tabs fetch and then render. Counting what is in the box before the fetch
        resolves reads zero, and zero is what a leak-free list also looks like -- so the wait is
        the difference between a test and a test that cannot fail.
        """
        self.look_at("Verbindungen")
        expect(self.tab.locator("#invite-device")).to_be_visible(timeout=PATIENCE)
        return self.tab.locator("#devicelist")

    def sign_ins(self):
        self.look_at("Anmeldungen")
        expect(self.tab.locator("#sign-out")).to_be_visible(timeout=PATIENCE)
        return self.tab.locator("#sessionlist")

    def start_a_job(self):
        """From the customer area, where `#new-job` is the button. `#start-job` belongs to the
        one-off first-job step of the wizard, which `arrive()` deliberately walks past."""
        self.look_at("Übersicht")
        self.tab.click("#new-job")
        expect(self.tab.locator("#joblist").get_by_text("fertig")).to_be_visible(
            timeout=PATIENCE)
        return self.tab.locator("#joblist")

    def my_account(self):
        """Which account this browser is in, asked of the GATEWAY rather than of the page.

        Two windows showing different things is what a person sees; two windows being different
        customers is what has to be true underneath, and the page is not the authority on that.
        """
        from agentnode_sdk.access import dispatch

        return dispatch.identify_client(self.service, self.device_id()).account_id

    def device_id(self):
        shown = self.tab.locator("#devicelist [data-device]").first
        return shown.get_attribute("data-device")


def _runs_shown_by(customer) -> int:
    """The run count on this person's usage screen, read the way a person reads it.

    Rendered either as a bare number or as "n von m", depending on whether a ceiling is set --
    so the figure is taken from the line, not from a fixed spelling of it.
    """
    import re

    expect(customer.tab.locator("#usagebox").get_by_text(
        "Aufträge in diesem Zeitfenster")).to_be_visible(timeout=PATIENCE)
    shown = customer.tab.inner_text("#usagebox")
    found = re.search(r"Aufträge in diesem Zeitfenster\s+(\d+)", shown)
    assert found, shown
    return int(found.group(1))


@pytest.fixture()
def two_browsers(browser, gateway, tmp_path):
    """One gateway, and as many independent browsers against it as a test asks for."""
    service, base = gateway()
    opened = []

    def somebody(named):
        where = tmp_path / named
        where.mkdir(parents=True, exist_ok=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900},
                                      accept_downloads=True)
        tab = context.new_page()
        tab.set_default_timeout(PATIENCE)
        problems = []
        tab.on("pageerror", lambda e: problems.append(str(e)))
        opened.append(context)
        return Customer(tab, service, base, problems, where)

    try:
        yield somebody, service, base
    finally:
        for context in opened:
            context.close()


@pytest.fixture()
def alice_and_bob(two_browsers):
    somebody, service, base = two_browsers
    alice = somebody("alice").arrive("Alices Laptop")
    bob = somebody("bob").arrive("Bobs Rechner")
    return alice, bob, service, base


# ------------------------------------------------------------------ two customers, two windows


class TestTwoBrowsersAreTwoCustomers:

    def test_each_invitation_produced_its_own_account(self, alice_and_bob):
        alice, bob, _service, _base = alice_and_bob
        alice.connections()
        bob.connections()
        assert alice.my_account() and bob.my_account()
        assert alice.my_account() != bob.my_account(), (
            "two people who each redeemed their own invitation ended up in one account, so the "
            "first customer on this sandbox silently owns everybody who joins after them")

    def test_neither_console_shows_the_others_machine(self, alice_and_bob):
        alice, bob, _service, _base = alice_and_bob
        hers = alice.connections()
        expect(hers.get_by_text("Alices Laptop")).to_be_visible()
        assert hers.get_by_text("Bobs Rechner").count() == 0

        his = bob.connections()
        expect(his.get_by_text("Bobs Rechner")).to_be_visible()
        assert his.get_by_text("Alices Laptop").count() == 0

    def test_and_the_device_identifiers_on_the_two_screens_do_not_overlap(self, alice_and_bob):
        alice, bob, _service, _base = alice_and_bob
        alice.connections()
        bob.connections()
        hers = set(alice.tab.locator("#devicelist [data-device]").evaluate_all(
            "nodes => nodes.map(n => n.dataset.device)"))
        his = set(bob.tab.locator("#devicelist [data-device]").evaluate_all(
            "nodes => nodes.map(n => n.dataset.device)"))
        assert hers and his and not (hers & his), (hers, his)

    def test_neither_console_shows_the_others_sign_ins(self, alice_and_bob):
        alice, bob, _service, _base = alice_and_bob
        assert alice.sign_ins().locator("[data-session]").count() == 1
        assert bob.sign_ins().locator("[data-session]").count() == 1

    def test_neither_console_shows_the_others_jobs(self, alice_and_bob):
        """Alice starts one from her own screen. Bob's screen must not learn that it happened."""
        alice, bob, _service, _base = alice_and_bob
        alice.start_a_job()

        bob.look_at("Übersicht")
        expect(bob.tab.get_by_text("Noch keine Aufträge.")).to_be_visible(timeout=PATIENCE)

    def test_and_neither_console_shows_the_others_usage(self, alice_and_bob):
        alice, bob, _service, _base = alice_and_bob
        alice.start_a_job()

        bob.look_at("Verbrauch")
        assert _runs_shown_by(bob) == 0, bob.tab.inner_text("#usagebox")

        # The control. A usage screen that always said nought would pass the line above, so the
        # customer who DID start it has to be shown that she did -- usage is what a ceiling
        # refuses on, and a figure nobody can see is a refusal nobody can understand.
        alice.look_at("Verbrauch")
        assert _runs_shown_by(alice) == 1, alice.tab.inner_text("#usagebox")


# ------------------------------------------------------------------ what one window can do


class TestWhatOneWindowsCredentialCanReach:
    """Asked from INSIDE the browser, with its own cookie. Not a person's path.

    A person cannot type another account's run id into this console -- there is no field for it,
    which is worth having and is not the same as the credential being scoped. This asks the
    question the buttons cannot: given this browser's session, what comes back.
    """

    def _asking_for(self, customer, run_id):
        return customer.tab.evaluate(
            """async (runId) => {
                 const r = await fetch('/v1/op/status', {
                   method: 'POST', cache: 'no-store', credentials: 'same-origin',
                   headers: {'Content-Type': 'application/json'},
                   body: JSON.stringify({run_id: runId})});
                 return {status: r.status, body: await r.text()};
               }""", run_id)

    def _a_run_of(self, customer):
        customer.start_a_job()
        return customer.tab.locator("#joblist [data-run]").first.get_attribute("data-run")

    def test_another_accounts_run_does_not_exist_to_this_browser(self, alice_and_bob):
        alice, bob, _service, _base = alice_and_bob
        hers = self._a_run_of(alice)
        assert hers, "alice's own console did not show a run id to ask about"

        said = self._asking_for(bob, hers)
        assert said["status"] >= 400, said
        assert json.loads(said["body"])["refused"] == "no_such_run"

    def test_and_her_own_browser_still_reaches_it(self, alice_and_bob):
        """A console that refused everybody would pass the test above."""
        alice, _bob, _service, _base = alice_and_bob
        hers = self._a_run_of(alice)
        said = self._asking_for(alice, hers)
        assert said["status"] == 200, said
        assert json.loads(said["body"])["run_id"] == hers

    def test_no_durable_credential_is_left_in_either_browser(self, alice_and_bob):
        """The cookie is the credential and the page cannot read it. Nothing else is kept."""
        alice, bob, _service, _base = alice_and_bob
        for who in (alice, bob):
            kept = who.tab.evaluate(
                "() => JSON.stringify({l: {...localStorage}, s: {...sessionStorage},"
                " c: document.cookie})")
            body = json.loads(kept)
            assert body["c"] == "", (
                "the session cookie is readable by scripts on the page, so an injected script "
                "could take it: %r" % body["c"])
            said = json.dumps(body)
            assert "token" not in said.lower(), said
            assert len(said) < 400, said

    def test_one_browsers_cookie_in_the_other_browser_is_still_only_one_account(
            self, two_browsers):
        """It presents the account it belongs to -- it does not add itself to the one it lands in.

        A session is a way of presenting an identity. Carrying the cookie somewhere else carries
        that identity with it, which is what a stolen cookie IS; what must not happen is the two
        being merged, or the receiving window keeping what it had as well.
        """
        somebody, service, _base = two_browsers
        alice = somebody("alice").arrive("Alices Laptop")
        alice.connections()
        hers = alice.my_account()

        stolen = [c for c in alice.tab.context.cookies() if c["name"].endswith("agentnode")]
        assert stolen, "no session cookie was set at all"

        thief = somebody("thief")
        thief.tab.context.add_cookies([dict(c, domain=c["domain"], path=c["path"])
                                       for c in stolen])
        thief.open()
        thief.connections()
        assert thief.my_account() == hers
        expect(thief.tab.locator("#devicelist").get_by_text("Alices Laptop")).to_be_visible()
        assert thief.tab.locator("#devicelist [data-device]").count() == 1


# ------------------------------------------------------------------ one window changing things


class TestWhatOneWindowChangesStaysInThatAccount:

    def test_signing_out_of_one_browser_leaves_the_other_signed_in(self, alice_and_bob):
        alice, bob, _service, _base = alice_and_bob
        alice.sign_ins()
        alice.tab.click("#sign-out")
        expect(alice.tab.get_by_role("button", name="Einrichtung starten")).to_be_visible(
            timeout=PATIENCE)

        bob.connections()
        expect(bob.tab.locator("#devicelist").get_by_text("Bobs Rechner")).to_be_visible()

    def test_withdrawing_your_own_machine_does_not_touch_the_other_account(self,
                                                                          alice_and_bob):
        alice, bob, _service, _base = alice_and_bob
        bob.connections()
        his_device = bob.device_id()

        alice.connections()
        alice.tab.locator("[data-revoke]").first.click()
        alice.tab.click("#revoke-yes")
        expect(alice.tab.get_by_role("button", name="Einrichtung starten")).to_be_visible(
            timeout=PATIENCE)

        bob.look_at("Verbindungen")
        expect(bob.tab.locator("#devicelist").get_by_text("Bobs Rechner")).to_be_visible(
            timeout=PATIENCE)
        assert bob.device_id() == his_device

    def test_and_a_withdrawn_browser_stops_working_on_its_very_next_action(self,
                                                                          alice_and_bob):
        """Not at the next reload. The session is re-established on every request."""
        alice, _bob, service, _base = alice_and_bob
        alice.connections()
        assert service.state.revoke_client(alice.device_id()), "nothing was withdrawn"

        said = alice.tab.evaluate(
            """async () => {
                 const r = await fetch('/v1/op/devices/list', {
                   cache: 'no-store', credentials: 'same-origin'});
                 return {status: r.status, body: await r.text()};
               }""")
        assert said["status"] >= 400, said
        assert json.loads(said["body"])["refused"] == "not_authenticated"


# ------------------------------------------------------------------ adding your second machine


class TestAddingASecondMachineFromTheConsole:
    """The customer-usability half of tenancy: a person adds their own machine, in a browser."""

    def test_the_code_from_one_console_joins_that_account_in_another_browser(self,
                                                                            two_browsers):
        somebody, _service, _base = two_browsers
        alice = somebody("alice").arrive("Alices Laptop")
        bob = somebody("bob").arrive("Bobs Rechner")

        alice.connections()
        alice.tab.click("#invite-device")
        expect(alice.tab.locator("#invitation-code")).to_be_visible(timeout=PATIENCE)
        code = alice.tab.inner_text("#invitation-code").strip()
        assert code, "no code was shown"

        her_laptop = somebody("her-laptop")
        her_laptop.through_the_invitation(code)
        her_laptop.name_the_device("Alices zweiter Rechner")
        expect(her_laptop.tab.get_by_role("heading", name="Verbunden. Das ist der Schutz.")
               ).to_be_visible(timeout=PATIENCE)
        her_laptop.choose("none")
        her_laptop.tab.click("#use-console-anyway")
        her_laptop.tab.get_by_role("button", name="Überspringen").click()

        joined = her_laptop.connections()
        expect(joined.get_by_text("Alices Laptop")).to_be_visible(timeout=PATIENCE)
        expect(joined.get_by_text("Alices zweiter Rechner")).to_be_visible()
        assert joined.get_by_text("Bobs Rechner").count() == 0

        his = bob.connections()
        assert his.locator("[data-device]").count() == 1, (
            "Bob's console grew a device when Alice added one to her own account")

    def test_nobody_is_asked_for_an_account_anywhere_on_that_path(self, two_browsers):
        """The account comes from who asked. A field for it would be a field to get wrong."""
        somebody, _service, _base = two_browsers
        alice = somebody("alice").arrive("Alices Laptop")
        alice.connections()
        alice.tab.click("#invite-device")
        expect(alice.tab.locator("#invitation-code")).to_be_visible(timeout=PATIENCE)

        seen = alice.tab.inner_text("body")
        for jargon in ["acct-", "account_id", "Konto-ID", "YAML", "yaml", "X-AgentNode-Token"]:
            assert jargon not in seen, (
                "a customer adding their second machine was shown %r" % jargon)
        assert alice.tab.locator("input").count() == 0, (
            "there is a field on this screen; the only thing a person should do here is read a "
            "code and type it on the other machine")

    def test_the_code_is_shown_once_and_the_screen_says_so(self, two_browsers):
        somebody, _service, _base = two_browsers
        alice = somebody("alice").arrive("Alices Laptop")
        alice.connections()
        alice.tab.click("#invite-device")
        expect(alice.tab.locator("#invitation-code")).to_be_visible(timeout=PATIENCE)
        code = alice.tab.inner_text("#invitation-code").strip()
        expect(alice.tab.locator("#the-invitation")).to_contain_text("Wird nur jetzt angezeigt")

        alice.tab.click("#invitation-done")
        expect(alice.tab.locator("#devicelist").get_by_text("Offene Einladungen")).to_be_visible(
            timeout=PATIENCE)
        assert code not in alice.tab.inner_text("#devicelist"), (
            "the list of outstanding invitations shows the code, which makes the list as good "
            "as the invitation")

    def test_and_an_invitation_can_be_taken_back_before_it_is_used(self, two_browsers):
        somebody, _service, _base = two_browsers
        alice = somebody("alice").arrive("Alices Laptop")
        alice.connections()
        alice.tab.click("#invite-device")
        expect(alice.tab.locator("#invitation-code")).to_be_visible(timeout=PATIENCE)
        code = alice.tab.inner_text("#invitation-code").strip()
        alice.tab.click("#invitation-done")

        expect(alice.tab.locator("[data-invitation]").first).to_be_visible(timeout=PATIENCE)
        alice.tab.locator("[data-invitation]").first.get_by_role(
            "button", name="Zurückziehen").click()
        expect(alice.tab.locator("#devicelist")).not_to_contain_text("Offene Einladungen",
                                                                    timeout=PATIENCE)

        nobody = somebody("nobody")
        nobody.through_the_invitation(code)
        nobody.name_the_device("Zu spät")
        # WHAT MUST BE TRUE: they are not let in, and Alice's account did not grow a machine.
        expect(nobody.tab.locator("main").get_by_role("alert")).to_be_visible(timeout=PATIENCE)
        assert nobody.tab.get_by_role(
            "heading", name="Verbunden. Das ist der Schutz.").count() == 0

        alice.connections()
        assert alice.tab.locator("#devicelist [data-device]").count() == 1

        # WHAT IS NOT ASSERTED, and why: the WORDING. A withdrawn invitation is deleted rather
        # than remembered -- the file keeps a hash and drops it, which is what stops a copy of
        # the file being a working key -- so this gateway genuinely cannot tell a withdrawn code
        # from one it never issued, and the message a person gets comes from the operator's own
        # pairing state instead. That is a known limit of the wording, recorded here rather than
        # pinned by a test that would make it look intended.
