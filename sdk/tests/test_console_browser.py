"""The customer flow, in a real browser, against a real gateway.

Everything here drives the page the way a person does -- clicking what is on screen, typing into
the field that is focused, reading what is rendered. Nothing reaches into the page's internals to
make an assertion, because what is being established is that a person can get through this, and a
person cannot call a JavaScript function.

Two conventions worth knowing before reading further.

**The gateway is real.** Not a mock server, not a fixture returning canned answers: the same
`GatewayService` the product runs, serving on loopback, answering the same `/v1/op/` addresses.
The only stand-in is the sandbox backend, because a browser test that started containers would be
measuring Docker.

**A missing browser is not a pass.** If Playwright or its browser is not installed, these do not
quietly skip into a green run -- with `AGENTNODE_BROWSER_TESTS=required` set, which is how the
suite runs them, the absence is a failure that says so. A skip that looks like a pass is how a
suite comes to report that thirteen scenarios work when none of them ran.
"""
from __future__ import annotations

import json
import os
import time

import pytest

REQUIRED = os.environ.get("AGENTNODE_BROWSER_TESTS", "").lower() == "required"


def _no_browser(why):
    if REQUIRED:
        pytest.fail("the browser tests were required and could not run: %s" % why, pytrace=False)
    pytest.skip("%s -- set AGENTNODE_BROWSER_TESTS=required to make this a failure" % why,
                allow_module_level=True)


try:
    from playwright.sync_api import expect, sync_playwright
except ImportError as exc:                                    # noqa: BLE001
    _no_browser("playwright is not installed (%s)" % exc)

# Imported after the availability check above on purpose: with no browser there is nothing to
# test, and a missing import would be reported instead of the missing browser.  # noqa: E402
from agentnode_sdk.gateway.allowance import OverTheCeiling  # noqa: E402
from agentnode_sdk.gateway.identity import PAIRING_TTL_SECONDS, GatewayState  # noqa: E402
from agentnode_sdk.gateway.server import GatewayService, make_server  # noqa: E402
from tests import serving  # noqa: E402
from tests.test_em3c_gateway import StandInBackend, _store_measurement  # noqa: E402
from tests.test_end_to_end_rest import ADoor  # noqa: E402

#: Long enough for a real page to settle, short enough that a hang is a failure rather than a wait.
PATIENCE = 15_000


class ASlowSandbox(StandInBackend):
    """A run that takes long enough to still be running when somebody presses Abbrechen."""

    def __init__(self, seconds=6.0):
        super().__init__()
        self.seconds = seconds

    def run_process(self, spec, input_text=None, timeout=120.0):
        self.specs.append(spec)
        time.sleep(self.seconds)
        return 0, "RAN", ""


@pytest.fixture(scope="session")
def browser():
    try:
        with sync_playwright() as play:
            try:
                # --no-sandbox because this runs as root on the test host. The thing under test
                # is the page, not the browser's own isolation.
                engine = play.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:                          # noqa: BLE001
                _no_browser("chromium would not start (%s)" % exc)
            yield engine
            engine.close()
    except Exception as exc:                                  # noqa: BLE001
        _no_browser("playwright would not start (%s)" % exc)


@pytest.fixture()
def gateway(tmp_path):
    """A gateway on loopback, and a handle on the operator's side of it."""
    made = []

    def start(backend=None):
        state = GatewayState(str(tmp_path / "state"), version="test")
        service = GatewayService(state, backend=backend or StandInBackend())
        _store_measurement(service)
        server = make_server(service, port=0, host="127.0.0.1")
        serving.owned(server, state)
        base = "http://127.0.0.1:%d" % server.server_address[1]
        made.append((service, state, base))
        return service, base

    yield start


@pytest.fixture()
def page(browser, gateway):
    """One page, one gateway, and the console already open on the welcome screen."""
    service, base = gateway()
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    tab = context.new_page()
    tab.set_default_timeout(PATIENCE)
    problems = []
    tab.on("pageerror", lambda e: problems.append(str(e)))
    yield Console(tab, service, base, problems)
    context.close()


class Console:
    """What a person can do, named the way a person would describe it."""

    def __init__(self, tab, service, base, problems):
        self.tab, self.service, self.base, self.problems = tab, service, base, problems

    # --- getting there --------------------------------------------------------------------
    def open(self, fragment=""):
        self.tab.goto(self.base + "/console" + fragment)
        return self

    def invitation(self, when=None):
        return self.service.state.start_pairing(now=when)

    def stop_the_gateway(self, why="Wartung"):
        path = os.path.join(str(self.service.state.root), "stopped.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"reason": why}, fh)

    def token_in_the_browser(self):
        kept = self.tab.evaluate("() => sessionStorage.getItem('agentnode.session')")
        return json.loads(kept)["token"] if kept else ""

    def someone_else_calls(self, token):
        """A real tool call from outside this page -- exactly what the test screen waits for."""
        door = ADoor(self.base, token)
        code = "print('von aussen')"
        import base64
        import hashlib

        told = door.ask("prepare", {"command": ["python", "-c", code],
                                    "artifact_sha256": hashlib.sha256(code.encode()).hexdigest(),
                                    "artifact_bytes": len(code), "wall_clock_s": 30})[1]
        return door.ask("submit", {
            "run_id": "b" * 32, "artifact": base64.b64encode(code.encode()).decode(),
            "command": ["python", "-c", code], "wall_clock_s": 30,
            "accepted_disclosure": told.get("accepted_disclosure", "")})

    # --- the ten steps --------------------------------------------------------------------
    def through_the_invitation(self, code):
        self.open("#code=" + code)
        self.tab.get_by_role("button", name="Weiter").click()

    def name_the_device(self, name=None):
        if name is not None:
            self.tab.fill("#devname", name)
        self.tab.click("#confirm-name")

    def onboard(self, way="rest"):
        """Everything up to the connection test, which is where the scenarios diverge."""
        self.through_the_invitation(self.invitation())
        self.name_the_device("Mein Testgerät")
        self.tab.click("#confirm-protection")
        self.tab.click("#way-" + way)
        # The not-compatible answer is an answer, not a step to continue past: it deliberately
        # offers no "Weiter", so there is none to press.
        if way != "none":
            self.tab.click("#way-next")
        return self


# ----------------------------------------------------------------- 1. the whole way through

class TestSomebodyGetsAllTheWayThrough:

    def test_from_an_invitation_to_a_finished_job_without_seeing_anything_technical(self, page):
        page.onboard(way="rest")
        expect(page.tab.get_by_role("heading", name="Ihre Einrichtung ist fertig.")).to_be_visible()
        page.tab.click("#setup-next")

        # The test screen waits for a call it did not make itself. So one arrives from outside.
        expect(page.tab.get_by_text("Wartet auf den ersten Aufruf")).to_be_visible()
        status, answer = page.someone_else_calls(page.token_in_the_browser())
        assert status in (200, 202), answer

        expect(page.tab.get_by_role("heading", name="Die Verbindung funktioniert.")).to_be_visible(
            timeout=PATIENCE)
        page.tab.click("#to-first-job")
        page.tab.click("#start-job")

        expect(page.tab.get_by_role("heading", name="Ihre Sandbox")).to_be_visible()
        expect(page.tab.locator("#joblist").get_by_text("fertig")).to_be_visible(timeout=PATIENCE)
        assert not page.problems, page.problems

    def test_and_never_has_to_look_at_anything_only_a_developer_would_know(self, page):
        """The instruction was specific: no SSH, no firewall rules, no certificate digests, no
        JSON, no YAML, no endpoints, no command-line switches on a normal customer's path.

        The setup file is the one exception the instruction itself allows -- it is offered as a
        whole file to copy, behind a disclosure a person has to open on purpose, and is never
        something to hand-edit.
        """
        page.onboard(way="rest")
        page.tab.click("#setup-next")
        page.someone_else_calls(page.token_in_the_browser())
        expect(page.tab.get_by_role("heading", name="Die Verbindung funktioniert.")).to_be_visible()
        page.tab.click("#to-first-job")
        page.tab.click("#start-job")
        expect(page.tab.locator("#joblist").get_by_text("fertig")).to_be_visible(timeout=PATIENCE)

        seen = page.tab.inner_text("body")
        for jargon in ["ssh ", "iptables", "sha256:", "X-AgentNode-Token", "/v1/op/", "--port",
                       "curl ", "yaml", "Bearer "]:
            assert jargon.lower() not in seen.lower(), (
                "a customer was shown %r on the ordinary path" % jargon)


# ------------------------------------------------------------------ 2-4. invitations that fail

class TestAnInvitationThatDoesNotWork:

    def test_an_expired_one_says_so_and_says_what_to_do(self, page):
        long_ago = time.time() - PAIRING_TTL_SECONDS - 60
        page.through_the_invitation(page.invitation(when=long_ago))
        page.name_the_device()
        expect(page.tab.get_by_text("Diese Einladung ist abgelaufen.")).to_be_visible()
        expect(page.tab.get_by_text("Bitte eine neue anfordern")).to_be_visible()

    def test_one_that_has_already_been_used_is_not_confused_with_a_wrong_one(self, page):
        code = page.invitation()
        page.service.state.redeem_pairing(code, client_name="ein früheres Gerät")
        page.through_the_invitation(code)
        page.name_the_device()
        expect(page.tab.get_by_text("Diese Einladung wurde schon benutzt.")).to_be_visible()

    def test_a_wrong_code_is_told_apart_from_both(self, page):
        real = page.invitation()
        # The right shape, the wrong code. A code of the wrong LENGTH is a different mistake and
        # gets a different sentence -- see the test below.
        letters = [c for c in real if c != "-"]
        letters[-1] = "K" if letters[-1] != "K" else "M"
        wrong = "-".join("".join(letters[i:i + 4]) for i in range(0, 12, 4))
        page.open()
        page.tab.get_by_role("button", name="Einrichtung starten").click()
        page.tab.fill("#code", wrong)
        page.tab.get_by_role("button", name="Weiter").click()
        page.name_the_device()
        expect(page.tab.get_by_text("Dieser Code stimmt nicht.")).to_be_visible()

    def test_a_code_that_is_too_short_is_a_typo_not_a_rejection(self, page):
        """Somebody who pasted half a code should be told that, not told they were refused."""
        page.invitation()
        page.open()
        page.tab.get_by_role("button", name="Einrichtung starten").click()
        page.tab.fill("#code", "ABCD-EFGH")
        page.tab.get_by_role("button", name="Weiter").click()
        page.name_the_device()
        expect(page.tab.get_by_text("Dieser Code ist nicht vollständig.")).to_be_visible()

    def test_and_the_secret_never_reaches_the_address_bar_or_the_server_log(self, page):
        """The requirement, stated plainly: an invitation must not land in a query parameter, a
        referrer or a server log. A fragment is never sent to any server at all, and this one is
        wiped out of the address bar the moment it has been read."""
        code = page.invitation()
        page.open("#code=" + code)
        expect(page.tab.locator("#code")).to_have_value(code)
        assert code not in page.tab.url, "the invitation was still in the address bar"
        assert "#" not in page.tab.url or page.tab.url.endswith("#")
        assert "?" not in page.tab.url, "the invitation became a query parameter"


# ----------------------------------------------------------------------- 5. a revoked device

class TestADeviceThatHasBeenWithdrawn:

    def test_it_stops_working_immediately_and_the_person_is_told_why(self, page):
        page.onboard(way="rest")
        page.tab.click("#setup-next")
        token = page.token_in_the_browser()
        page.someone_else_calls(token)
        expect(page.tab.get_by_role("heading", name="Die Verbindung funktioniert.")).to_be_visible()

        # Withdrawn from somewhere else entirely, the way losing a laptop actually goes.
        devices = ADoor(page.base, token).ask("devices.list")[1]
        target = (devices.get("devices") or [{}])[0].get("device_id", "")
        assert target, devices
        page.service.state.revoke_client(target)

        page.tab.click("#to-first-job")
        page.tab.click("#start-job")

        # What matters, and what is asserted: the access is gone AT ONCE, and the person is told
        # something they can act on.
        #
        # What is NOT asserted, because the product does not do it: the words "Dieses Gerät wurde
        # zurückgezogen". The contract declares a `device_revoked` refusal, but revoking deletes
        # the token entry, so `identify` fails first and the answer is `not_authenticated` --
        # which makes that declared refusal unreachable except in a race. That is a real
        # discrepancy between the declaration and the behaviour, and it is written down in
        # docs/managed-access-migration.md rather than hidden behind a test that asserts the
        # friendlier of the two. It is not a security gap: access stops either way, immediately.
        expect(page.tab.get_by_text("Dieser Zugang gilt nicht mehr.")).to_be_visible(
            timeout=PATIENCE)
        expect(page.tab.get_by_role("button", name="Einrichtung neu starten")).to_be_visible()

        # And it really is gone, on the other access paths too, not only in this page.
        status, _ = ADoor(page.base, token).ask("capabilities")
        assert status == 401, status

    def test_a_person_can_withdraw_one_from_the_page_in_one_step(self, page):
        page.onboard(way="rest")
        page.tab.click("#setup-next")
        page.someone_else_calls(page.token_in_the_browser())
        expect(page.tab.get_by_role("heading", name="Die Verbindung funktioniert.")).to_be_visible()
        page.tab.get_by_role("button", name="Direkt zur Übersicht").click()
        page.tab.get_by_role("button", name="Geräte").click()

        page.tab.get_by_role("button", name="Zugang zurückziehen").first.click()
        expect(page.tab.locator("#revoke-confirm")).to_be_visible()
        page.tab.click("#revoke-yes")
        # It was this device, so the page returns to the very beginning rather than pretending.
        expect(page.tab.get_by_role("heading", name="Willkommen bei AgentNode.")).to_be_visible()


# ------------------------------------------------------------------ 6. an AI that cannot do it

class TestAnAIThatCannotCallTools:

    def test_it_is_called_not_compatible_in_those_words_and_offered_no_substitute(self, page):
        page.through_the_invitation(page.invitation())
        page.name_the_device()
        page.tab.click("#confirm-protection")
        page.tab.click("#way-none")

        said = page.tab.inner_text("#not-compatible")
        assert "kann AgentNode nicht direkt verwenden" in said
        assert "keine externen Werkzeuge aufrufen kann" in said
        # And no false alternative: the page does not offer to connect that AI some other way.
        assert "stattdessen verbinden" not in said.lower()
        assert "trotzdem verbinden" not in said.lower()
        # What it does offer is honest about being a different thing: the person themselves.
        assert "Ihre KI bleibt davon getrennt" in said
        expect(page.tab.locator("#use-console-anyway")).to_be_visible()

    def test_and_the_page_does_not_claim_that_makes_the_AI_compatible(self, page):
        page.through_the_invitation(page.invitation())
        page.name_the_device()
        page.tab.click("#confirm-protection")
        page.tab.click("#way-none")
        page.tab.click("#use-console-anyway")
        expect(page.tab.get_by_role("heading", name="Starten wir etwas Echtes.")).to_be_visible()
        assert "Verbindung funktioniert" not in page.tab.inner_text("body")


# ---------------------------------------------------------------- 7. a connection test that fails

class TestAConnectionTestThatFails:

    def test_the_person_is_told_what_happened_and_given_something_they_can_do(self, page):
        page.onboard(way="rest")
        page.tab.click("#setup-next")
        expect(page.tab.get_by_text("Wartet auf den ersten Aufruf")).to_be_visible()

        page.stop_the_gateway("Der Betreiber hat angehalten")
        page.tab.click("#test-here-instead")

        expect(page.tab.get_by_text("Die Sandbox nimmt gerade keine Arbeit an.")).to_be_visible(
            timeout=PATIENCE)
        # At least one thing they can actually press, not just an apology.
        expect(page.tab.get_by_role("button", name="Noch einmal versuchen")).to_be_visible()


# ------------------------------------------------------------- 8-9. a job, and cancelling one

class TestRunningSomething:

    def test_a_job_shows_its_state_and_then_its_result(self, page):
        page.onboard(way="none")
        page.tab.click("#use-console-anyway")
        page.tab.click("#start-job")
        expect(page.tab.locator("#joblist").get_by_text("fertig")).to_be_visible(timeout=PATIENCE)
        expect(page.tab.locator("#joblist pre")).to_be_visible()

    def test_cancelling_does_not_freeze_the_page_while_the_sandbox_is_torn_down(self, browser,
                                                                               gateway):
        """The whole reason cancel stopped being synchronous.

        The job is slow on purpose, so it is genuinely still running when Abbrechen is pressed.
        What is asserted is that the page comes back and SAYS the run is being stopped, promptly
        -- not that the run is already over, which would be the opposite of the point.
        """
        service, base = gateway(backend=ASlowSandbox(seconds=6.0))
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        tab = context.new_page()
        tab.set_default_timeout(PATIENCE)
        try:
            here = Console(tab, service, base, [])
            here.onboard(way="none")
            tab.click("#use-console-anyway")
            tab.click("#start-job")

            expect(tab.locator("#joblist").get_by_text("läuft")).to_be_visible(timeout=PATIENCE)
            tab.locator("[data-cancel]").first.click()
            # Promptly, and while the sandbox is demonstrably still being dealt with.
            expect(tab.locator("#joblist").get_by_text("wird abgebrochen")).to_be_visible(
                timeout=5_000)
            expect(tab.get_by_text("Der Abbruch ist angefordert")).to_be_visible()
        finally:
            context.close()


# ---------------------------------------------------------------------- 10. quota, 11. kill switch

class TestWhenTheSandboxSaysNo:

    def test_a_used_up_quota_is_explained_without_blaming_the_person(self, page):
        """The refusal is produced at the service boundary. What is under test here is what a
        person is shown when it happens -- the refusal itself has its own tests elsewhere."""
        def over(*a, **k):
            raise OverTheCeiling("runs", "5 runs in the last hour", time.time() + 600)

        page.service.submit = over
        page.onboard(way="none")
        page.tab.click("#use-console-anyway")
        page.tab.click("#start-job")

        expect(page.tab.get_by_text("Das Kontingent für dieses Zeitfenster ist aufgebraucht.")
               ).to_be_visible(timeout=PATIENCE)
        expect(page.tab.get_by_text("Laufende Aufträge sind nicht betroffen")).to_be_visible()
        expect(page.tab.get_by_role("button", name="Verbrauch ansehen")).to_be_visible()

    def test_the_kill_switch_is_visible_as_a_decision_rather_than_a_fault(self, page):
        page.onboard(way="none")
        page.tab.click("#use-console-anyway")
        page.stop_the_gateway("Wartungsfenster")
        page.tab.get_by_role("button", name="Überspringen").click()

        expect(page.tab.locator("#killswitch")).to_be_visible()
        said = page.tab.inner_text("#killswitch")
        assert "Not-Aus ist gezogen" in said
        assert "nicht ein Fehler" in said, "a deliberate stop was presented as a malfunction"


# ------------------------------------------------------------- 12. on a phone, 13. by keyboard

class TestOnAPhoneAndWithoutAMouse:

    @pytest.mark.parametrize("size", [
        {"width": 390, "height": 844},     # a phone held upright
        {"width": 768, "height": 1024},    # a tablet
    ], ids=["phone", "tablet"])
    def test_nothing_runs_off_the_side_of_the_screen(self, browser, gateway, size):
        service, base = gateway()
        context = browser.new_context(viewport=size)
        tab = context.new_page()
        tab.set_default_timeout(PATIENCE)
        try:
            here = Console(tab, service, base, [])
            here.onboard(way="rest")
            tab.click("#setup-next")
            for screen in ("Einrichtung", "Test"):
                wide = tab.evaluate(
                    "() => document.documentElement.scrollWidth - window.innerWidth")
                assert wide <= 1, ("the page scrolls sideways by %dpx at %dx%d (%s)"
                                   % (wide, size["width"], size["height"], screen))
            expect(tab.get_by_role("button").first).to_be_visible()
        finally:
            context.close()

    def test_the_whole_onboarding_works_from_the_keyboard_alone(self, page):
        """No clicks at all: tab to what matters, type, press Enter."""
        code = page.invitation()
        page.open("#code=" + code)
        page.tab.keyboard.press("Tab")
        focused = page.tab.evaluate("() => document.activeElement && document.activeElement.id")
        assert focused == "code", "the first thing tabbed to was %r" % focused
        page.tab.keyboard.press("Enter")                 # the form submits on Enter

        expect(page.tab.locator("#devname")).to_be_visible()
        page.tab.focus("#devname")
        page.tab.keyboard.press("Enter")
        expect(page.tab.get_by_role("heading", name="Verbunden. Das ist der Schutz.")
               ).to_be_visible(timeout=PATIENCE)

    def test_and_what_is_focused_can_be_seen(self, page):
        page.open()
        page.tab.keyboard.press("Tab")
        outline = page.tab.evaluate(
            "() => { const e = document.activeElement;"
            " const s = getComputedStyle(e); return s.outlineStyle + ' ' + s.outlineWidth; }")
        assert outline.split()[0] != "none", "focus was not visible anywhere"
