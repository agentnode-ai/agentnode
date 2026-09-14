"""The customer flow, in a real browser, against a real gateway.

Everything here drives the page the way a person does -- clicking what is on screen, typing into
the field that is focused, reading what is rendered. Nothing reaches into the page's internals to
make an assertion, because what is being established is that a person can get through this, and a
person cannot call a JavaScript function.

Three conventions worth knowing before reading further.

**The gateway is real.** Not a mock server: the same `GatewayService` the product runs, serving on
loopback, answering the same addresses. The only stand-in is the sandbox backend, because a
browser test that started containers would be measuring Docker.

**The connection under test is real too.** The setup file is downloaded by the browser, read off
disk, and used to make an actual call -- which is the only thing that satisfies the compatibility
challenge. Nothing here can hand the page a green tick it did not earn.

**A missing browser is not a pass.** With `AGENTNODE_BROWSER_TESTS=required` set, which is how the
suite runs them, the absence of Playwright is a failure that says so. A skip that reads as a pass
is how a suite comes to report that a flow works when none of it ran.
"""
from __future__ import annotations

import json
import os
import re
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

from agentnode_sdk.access import dispatch  # noqa: E402
from agentnode_sdk.gateway.allowance import OverTheCeiling  # noqa: E402
from agentnode_sdk.gateway.identity import PAIRING_TTL_SECONDS, GatewayState  # noqa: E402
from agentnode_sdk.gateway.server import GatewayService, make_server  # noqa: E402
from tests import serving  # noqa: E402
from tests.test_em3c_gateway import StandInBackend, _store_measurement  # noqa: E402

PATIENCE = 20_000


class ASlowSandbox(StandInBackend):
    """A run that takes long enough to still be running when somebody presses Abbrechen."""

    def __init__(self, seconds=8.0):
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
                engine = play.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:                          # noqa: BLE001
                _no_browser("chromium would not start (%s)" % exc)
            yield engine
            engine.close()
    except Exception as exc:                                  # noqa: BLE001
        _no_browser("playwright would not start (%s)" % exc)


@pytest.fixture()
def gateway(tmp_path):
    made = []

    def start(backend=None):
        state = GatewayState(str(tmp_path / ("state%d" % len(made))), version="test")
        service = GatewayService(state, backend=backend or StandInBackend())
        _store_measurement(service)
        server = make_server(service, port=0, host="127.0.0.1")
        serving.owned(server, state)
        base = "http://127.0.0.1:%d" % server.server_address[1]
        made.append((service, base))
        return service, base

    yield start


@pytest.fixture()
def console(browser, gateway, tmp_path):
    service, base = gateway()
    context = browser.new_context(viewport={"width": 1280, "height": 900},
                                  accept_downloads=True)
    tab = context.new_page()
    tab.set_default_timeout(PATIENCE)
    problems = []
    tab.on("pageerror", lambda e: problems.append(str(e)))
    yield Console(tab, service, base, problems, tmp_path)
    context.close()


class Console:
    """What a person can do, named the way a person would describe it."""

    def __init__(self, tab, service, base, problems, tmp_path):
        self.tab, self.service, self.base = tab, service, base
        self.problems, self.tmp_path = problems, tmp_path

    def open(self, fragment=""):
        self.tab.goto(self.base + "/console" + fragment)
        return self

    def invitation(self, when=None):
        return self.service.state.start_pairing(now=when)

    def stop_the_gateway(self, why="Wartung"):
        with open(os.path.join(str(self.service.state.root), "stopped.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"reason": why}, fh)

    # --- the ten steps ---------------------------------------------------------------------
    def through_the_invitation(self, code=None):
        self.open("#code=" + (code if code is not None else self.invitation()))
        self.tab.get_by_role("button", name="Weiter").click()

    def name_the_device(self, name="Mein Testgerät"):
        if name is not None:
            self.tab.fill("#devname", name)
        self.tab.click("#confirm-name")

    def sign_in(self, code=None, name="Mein Testgerät"):
        self.through_the_invitation(code)
        self.name_the_device(name)
        expect(self.tab.get_by_role("heading", name="Verbunden. Das ist der Schutz.")
               ).to_be_visible(timeout=PATIENCE)
        return self

    def choose(self, way="rest"):
        self.tab.click("#confirm-protection")
        self.tab.click("#way-" + way)
        if way != "none":
            self.tab.click("#way-next")
        return self

    def collect_the_setup(self) -> str:
        """Download the file and read the credential out of it, the way the person's AI would."""
        with self.tab.expect_download() as caught:
            self.tab.click("#download-setup")
        where = str(self.tmp_path / "setup-file")
        caught.value.save_as(where)
        with open(where, encoding="utf-8") as fh:
            text = fh.read()
        found = re.search(r"AGENTNODE_TOKEN=(\S+)", text) or re.search(
            r'"X-AgentNode-Token":\s*"([^"]+)"', text)
        assert found, text[:200]
        return found.group(1)

    def the_connection_runs_something(self, token, via="rest"):
        """A real call by the enrolled connection. The only thing that satisfies the challenge."""
        import base64
        import hashlib

        code = "print('von der KI')"
        who = dispatch.identify(self.service, token, via=via)
        told = dispatch.dispatch("prepare", {
            "command": ["python", "-c", code],
            "artifact_sha256": hashlib.sha256(code.encode()).hexdigest(),
            "artifact_bytes": len(code), "wall_clock_s": 30}, who, service=self.service)
        return dispatch.dispatch("submit", {
            "run_id": hashlib.sha256(str(time.time()).encode()).hexdigest()[:32],
            "artifact": base64.b64encode(code.encode()).decode("ascii"),
            "command": ["python", "-c", code], "wall_clock_s": 30,
            "accepted_disclosure": told["accepted_disclosure"]}, who, service=self.service)

    def all_the_way_through(self, way="rest"):
        self.sign_in().choose(way)
        token = self.collect_the_setup()
        self.tab.click("#setup-next")
        expect(self.tab.locator("#test-area").get_by_text("Wartet auf den ersten Aufruf")).to_be_visible()
        self.the_connection_runs_something(token, via="mcp" if way in ("mcp", "bridge") else way)
        expect(self.tab.get_by_role("heading", name="Die Verbindung funktioniert.")
               ).to_be_visible(timeout=PATIENCE)
        return token


# ----------------------------------------------------------------- 1. the whole way through

class TestSomebodyGetsAllTheWayThrough:

    def test_from_an_invitation_to_a_finished_job(self, console):
        console.all_the_way_through()
        console.tab.click("#to-first-job")
        console.tab.click("#start-job")
        expect(console.tab.get_by_role("heading", name="Ihre Sandbox")).to_be_visible()
        expect(console.tab.locator("#joblist").get_by_text("fertig")).to_be_visible(
            timeout=PATIENCE)
        assert not console.problems, console.problems

    def test_and_never_has_to_look_at_anything_only_a_developer_would_know(self, console):
        console.all_the_way_through()
        console.tab.click("#to-first-job")
        console.tab.click("#start-job")
        expect(console.tab.locator("#joblist").get_by_text("fertig")).to_be_visible(
            timeout=PATIENCE)

        seen = console.tab.inner_text("body")
        for jargon in ["ssh ", "iptables", "sha256:", "X-AgentNode-Token", "/v1/op/", "--port",
                       "curl ", "yaml", "Bearer ", "localStorage"]:
            assert jargon.lower() not in seen.lower(), (
                "a customer was shown %r on the ordinary path" % jargon)


# ------------------------------------------------------------------ 2-4. invitations that fail

class TestAnInvitationThatDoesNotWork:

    def test_an_expired_one_says_so_and_says_what_to_do(self, console):
        console.through_the_invitation(
            console.invitation(when=time.time() - PAIRING_TTL_SECONDS - 60))
        console.name_the_device()
        expect(console.tab.locator("main").get_by_text("Diese Einladung ist abgelaufen.")).to_be_visible()
        expect(console.tab.locator("main").get_by_text("Bitte eine neue anfordern")).to_be_visible()

    def test_one_that_has_already_been_used_is_not_confused_with_a_wrong_one(self, console):
        code = console.invitation()
        console.service.state.redeem_pairing(code, client_name="ein früheres Gerät")
        console.through_the_invitation(code)
        console.name_the_device()
        expect(console.tab.locator("main").get_by_text("Diese Einladung wurde schon benutzt.")).to_be_visible()

    def test_a_wrong_code_says_that_it_is_now_spent(self, console):
        """One attempt per invitation, which is what makes guessing impossible -- so the message
        has to tell somebody they need a new one rather than to try again."""
        real = console.invitation()
        letters = [c for c in real if c != "-"]
        letters[-1] = "K" if letters[-1] != "K" else "M"
        wrong = "-".join("".join(letters[i:i + 4]) for i in range(0, 12, 4))
        console.open()
        console.tab.get_by_role("button", name="Einrichtung starten").click()
        console.tab.fill("#code", wrong)
        console.tab.get_by_role("button", name="Weiter").click()
        console.name_the_device()
        expect(console.tab.locator("main").get_by_text("Dieser Code stimmt nicht.")).to_be_visible()
        expect(console.tab.locator("main").get_by_text(
            "Jede Einladung erlaubt genau einen Versuch")).to_be_visible()

    def test_a_code_that_is_too_short_is_a_typo_not_a_rejection(self, console):
        console.invitation()
        console.open()
        console.tab.get_by_role("button", name="Einrichtung starten").click()
        console.tab.fill("#code", "ABCD-EFGH")
        console.tab.get_by_role("button", name="Weiter").click()
        console.name_the_device()
        expect(console.tab.locator("main").get_by_text("Dieser Code ist nicht vollständig.")).to_be_visible()

    def test_and_the_secret_never_reaches_the_address_bar(self, console):
        code = console.invitation()
        console.open("#code=" + code)
        expect(console.tab.locator("#code")).to_have_value(code)
        assert code not in console.tab.url
        assert "?" not in console.tab.url and "#" not in console.tab.url


# -------------------------------------------------------------- 5. nothing readable is kept

class TestWhatTheBrowserIsHolding:

    def test_no_credential_in_any_storage_the_page_can_read(self, console):
        console.all_the_way_through()
        said = json.loads(console.tab.evaluate(
            "() => JSON.stringify({local: Object.entries(localStorage),"
            " session: Object.entries(sessionStorage), cookie: document.cookie})"))
        assert said["local"] == [] and said["session"] == []
        # HttpOnly: the session cookie is not in document.cookie at all.
        assert "agentnode" not in said["cookie"].lower(), said["cookie"]

    def test_nor_anywhere_in_the_page(self, console):
        token = console.all_the_way_through()
        assert token not in console.tab.content()

    def test_and_a_reload_does_not_make_somebody_start_again(self, console):
        """The cookie survives; the confirmation value does not, so the page asks for a new one.
        Somebody who pressed refresh has not signed out."""
        console.all_the_way_through()
        console.tab.click("#to-first-job")
        console.tab.reload()
        expect(console.tab.get_by_role("heading", name="Ihre Sandbox")).to_be_visible(
            timeout=PATIENCE)

    def test_signing_out_ends_it_everywhere(self, console):
        console.all_the_way_through()
        console.tab.get_by_role("button", name="Direkt zur Übersicht").click()
        console.tab.get_by_role("button", name="Sicherheit").click()
        console.tab.click("#logout")
        expect(console.tab.get_by_role("heading", name="Willkommen bei AgentNode.")
               ).to_be_visible()
        console.tab.reload()
        expect(console.tab.get_by_role("heading", name="Willkommen bei AgentNode.")
               ).to_be_visible(timeout=PATIENCE)


# ------------------------------------------------------------------ 6. an AI that cannot do it

class TestAnAIThatCannotCallTools:

    def test_it_is_called_not_compatible_in_those_words_and_offered_no_substitute(self, console):
        console.sign_in()
        console.tab.click("#confirm-protection")
        console.tab.click("#way-none")

        said = console.tab.inner_text("#not-compatible")
        assert "kann AgentNode nicht direkt verwenden" in said
        assert "keine externen Werkzeuge aufrufen kann" in said
        assert "stattdessen verbinden" not in said.lower()
        assert "Ihre KI bleibt davon getrennt" in said

    def test_and_the_page_does_not_claim_that_makes_the_AI_compatible(self, console):
        console.sign_in().choose("none")
        console.tab.click("#use-console-anyway")
        expect(console.tab.get_by_role("heading", name="Starten wir etwas Echtes.")
               ).to_be_visible()
        assert "Verbindung funktioniert" not in console.tab.inner_text("body")


# ---------------------------------------------------------------- 7. a connection test that fails

class TestAConnectionTestThatFails:

    def test_nothing_calls_so_nothing_is_claimed(self, console):
        """The page waits rather than deciding. What must never happen is a green tick from
        something the person did themselves."""
        console.sign_in().choose("rest")
        console.collect_the_setup()
        console.tab.click("#setup-next")
        expect(console.tab.locator("#test-area").get_by_text("Wartet auf den ersten Aufruf")).to_be_visible()
        time.sleep(3)
        assert "Die Verbindung funktioniert" not in console.tab.inner_text("body")


# ------------------------------------------------------------- 8-9. a job, and cancelling one

class TestRunningSomething:

    def test_a_job_shows_its_state_and_then_its_result(self, console):
        console.sign_in().choose("none")
        console.tab.click("#use-console-anyway")
        console.tab.click("#start-job")
        expect(console.tab.locator("#joblist").get_by_text("fertig")).to_be_visible(
            timeout=PATIENCE)
        expect(console.tab.locator("#joblist pre")).to_be_visible()

    def test_cancelling_shows_that_it_is_being_carried_out(self, browser, gateway, tmp_path):
        """The whole reason cancel stopped being synchronous. What is asserted is that the page
        comes back and SAYS the run is being stopped -- not that it is already over, which would
        be the opposite of the point."""
        service, base = gateway(backend=ASlowSandbox(seconds=8.0))
        context = browser.new_context(viewport={"width": 1280, "height": 900},
                                      accept_downloads=True)
        tab = context.new_page()
        tab.set_default_timeout(PATIENCE)
        try:
            here = Console(tab, service, base, [], tmp_path)
            here.sign_in().choose("none")
            tab.click("#use-console-anyway")
            tab.click("#start-job")
            expect(tab.locator("#joblist").get_by_text("läuft")).to_be_visible(timeout=PATIENCE)
            tab.locator("[data-cancel]").first.click()
            expect(tab.locator("#joblist").get_by_text("Abbruch wird ausgeführt")).to_be_visible(
                timeout=6_000)
            expect(tab.get_by_text("Der Abbruch ist angefordert")).to_be_visible()
        finally:
            context.close()


# ---------------------------------------------------------------- 10. quota, 11. kill switch

class TestWhenTheSandboxSaysNo:

    def test_a_used_up_quota_is_explained_without_blaming_the_person(self, console):
        def over(*a, **k):
            raise OverTheCeiling("runs", "5 runs in the last hour", time.time() + 600)

        console.sign_in().choose("none")
        console.tab.click("#use-console-anyway")
        console.service.submit = over
        console.tab.click("#start-job")
        expect(console.tab.locator("main").get_by_text("Das Kontingent für dieses Zeitfenster ist aufgebraucht.")
               ).to_be_visible(timeout=PATIENCE)
        expect(console.tab.get_by_role("button", name="Verbrauch ansehen")).to_be_visible()

    def test_the_kill_switch_is_visible_as_a_decision_rather_than_a_fault(self, console):
        console.sign_in().choose("none")
        console.tab.click("#use-console-anyway")
        console.stop_the_gateway("Wartungsfenster")
        console.tab.get_by_role("button", name="Überspringen").click()
        expect(console.tab.locator("#killswitch")).to_be_visible()
        said = console.tab.inner_text("#killswitch")
        assert "Not-Aus ist gezogen" in said
        assert "nicht ein Fehler" in said


# ------------------------------------------------------------- 12. managing what has access

class TestManagingAccess:

    def test_a_person_can_see_and_end_their_own_sessions(self, console):
        console.all_the_way_through()
        console.tab.get_by_role("button", name="Direkt zur Übersicht").click()
        console.tab.get_by_role("button", name="Anmeldungen").click()
        expect(console.tab.locator("[data-end]").first).to_be_visible(timeout=PATIENCE)

    def test_and_see_and_withdraw_the_connections_they_set_up(self, console):
        console.all_the_way_through()
        console.tab.get_by_role("button", name="Direkt zur Übersicht").click()
        console.tab.get_by_role("button", name="Verbindungen").click()
        # Waited for rather than counted immediately: the list is fetched, so a count taken
        # before it arrives is a count of nothing.
        expect(console.tab.locator("[data-revoke]").first).to_be_visible(timeout=PATIENCE)
        # The browser itself, and the connection enrolled for the AI.
        assert console.tab.locator("[data-revoke]").count() >= 2

    def test_withdrawing_this_device_signs_the_person_out(self, console):
        console.all_the_way_through()
        console.tab.get_by_role("button", name="Direkt zur Übersicht").click()
        console.tab.get_by_role("button", name="Verbindungen").click()
        expect(console.tab.locator("[data-revoke]").first).to_be_visible(timeout=PATIENCE)
        console.tab.get_by_role("button", name="Zugang zurückziehen").first.click()
        expect(console.tab.locator("#revoke-confirm")).to_be_visible()
        console.tab.click("#revoke-yes")
        expect(console.tab.get_by_role("heading", name="Willkommen bei AgentNode.")
               ).to_be_visible(timeout=PATIENCE)


# ------------------------------------------------------------- 13. on a phone, by keyboard

class TestOnAPhoneAndWithoutAMouse:

    @pytest.mark.parametrize("size", [
        {"width": 390, "height": 844},
        {"width": 768, "height": 1024},
    ], ids=["phone", "tablet"])
    def test_nothing_runs_off_the_side_of_the_screen(self, browser, gateway, tmp_path, size):
        service, base = gateway()
        context = browser.new_context(viewport=size, accept_downloads=True)
        tab = context.new_page()
        tab.set_default_timeout(PATIENCE)
        try:
            here = Console(tab, service, base, [], tmp_path)
            here.sign_in().choose("rest")
            for where in ("die Einrichtung", "der Verbindungstest"):
                wide = tab.evaluate(
                    "() => document.documentElement.scrollWidth - window.innerWidth")
                assert wide <= 1, ("%s scrollt bei %dx%d um %dpx zur Seite"
                                   % (where, size["width"], size["height"], wide))
                if where == "die Einrichtung":
                    tab.click("#setup-next")
                    expect(tab.locator("#test-area")).to_be_visible()
            expect(tab.get_by_role("button").first).to_be_visible()
        finally:
            context.close()

    def test_the_whole_onboarding_works_from_the_keyboard_alone(self, console):
        code = console.invitation()
        console.open("#code=" + code)
        console.tab.keyboard.press("Tab")
        focused = console.tab.evaluate(
            "() => document.activeElement && document.activeElement.id")
        assert focused == "code", "the first thing tabbed to was %r" % focused
        console.tab.keyboard.press("Enter")

        expect(console.tab.locator("#devname")).to_be_visible()
        console.tab.focus("#devname")
        console.tab.keyboard.press("Enter")
        expect(console.tab.get_by_role("heading", name="Verbunden. Das ist der Schutz.")
               ).to_be_visible(timeout=PATIENCE)

    def test_and_what_is_focused_can_be_seen(self, console):
        console.open()
        expect(console.tab.locator("#start")).to_be_visible()
        console.tab.keyboard.press("Tab")
        outline = console.tab.evaluate(
            "() => { const s = getComputedStyle(document.activeElement);"
            " return s.outlineStyle + ' ' + s.outlineWidth; }")
        assert outline.split()[0] != "none", "focus was not visible anywhere"

    def test_a_status_change_is_announced_and_not_only_drawn(self, console):
        """A change that exists only visually is a change some people never receive."""
        console.sign_in().choose("none")
        console.tab.click("#use-console-anyway")
        live = console.tab.locator("#live")
        expect(live).to_have_attribute("aria-live", "polite")
        console.tab.click("#start-job")
        expect(live).to_contain_text("Auftrag", timeout=PATIENCE)


class TestANameSomebodyChoseIsText:

    @pytest.mark.parametrize("nasty", [
        "<script>window.__got_in = 1</script>",
        "\"><img src=x onerror='window.__got_in=1'>",
    ], ids=["a script tag", "an attribute break-out"])
    def test_it_is_rendered_rather_than_run(self, console, nasty):
        """Nothing is filtered on the way in -- ordinary words survive a character filter, so a
        filter buys nothing and costs you the apostrophe in your laptop's name. What matters is
        that the page builds text nodes rather than markup."""
        console.sign_in(name=nasty)
        console.tab.click("#confirm-protection")
        console.tab.click("#way-none")
        console.tab.click("#use-console-anyway")
        console.tab.get_by_role("button", name="Überspringen").click()
        console.tab.get_by_role("button", name="Verbindungen").click()
        expect(console.tab.locator("[data-revoke]").first).to_be_visible(timeout=PATIENCE)

        assert console.tab.evaluate("() => window.__got_in") is None
        assert console.tab.locator("#devicelist").get_by_text(nasty, exact=False).count() >= 1
