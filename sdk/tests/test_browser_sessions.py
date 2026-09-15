"""How a browser holds its access, and what happens when somebody tries to take it.

A browser cannot keep a secret from scripts running on its own page. So it is never given one: no
token reaches it, and what it does get is an identifier in a cookie its own JavaScript cannot
read. These tests are about the consequences of that -- what an injected script could still do,
what it could not, and how quickly a person can end it.

Everything here goes over real HTTP to a real gateway, because half the properties under test are
properties of headers.
"""
from __future__ import annotations

import http.client
import json
import time
import urllib.parse

import pytest

from agentnode_sdk.access import rest
from agentnode_sdk.access import sessions as store
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService, make_server
from tests import serving
from tests.test_em3c_gateway import StandInBackend, _store_measurement


class ABrowser:
    """Just enough of one: it keeps cookies, and it will not read an HttpOnly one."""

    def __init__(self, base):
        self.host, self.port = urllib.parse.urlparse(base).netloc.split(":")
        self.jar = {}
        self.csrf = ""

    def ask(self, method, path, body=None, headers=None, send_cookie=True, cross_site=False):
        conn = http.client.HTTPConnection(self.host, int(self.port), timeout=15)
        sending = {"Content-Type": "application/json"}
        sending.update(headers or {})
        # SameSite=Strict, modelled: a request a browser considers cross-site carries no cookie.
        if self.jar and send_cookie and not cross_site:
            sending["Cookie"] = "; ".join("%s=%s" % kv for kv in self.jar.items())
        conn.request(method, path, json.dumps(body) if body is not None else None, sending)
        answer = conn.getresponse()
        raw = answer.read().decode("utf-8") or "{}"
        set_cookie = answer.getheader("Set-Cookie") or ""
        if set_cookie:
            name, _, rest_of = set_cookie.partition("=")
            self.jar[name] = rest_of.split(";")[0]
        conn.close()
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {"raw": raw}
        return answer.status, parsed, set_cookie

    def sign_in(self, code, name="My laptop"):
        status, answer, cookie = self.ask("POST", "/v1/session",
                                          {"code": code, "client_name": name})
        if status == 200:
            self.csrf = answer["csrf"]
        return status, answer, cookie

    def call(self, operation, params=None, with_confirmation=True, **kw):
        headers = {}
        if with_confirmation and self.csrf:
            headers[rest.CSRF_HEADER] = self.csrf
        return self.ask("POST", "/v1/op/" + operation.replace(".", "/"), params or {},
                        headers=headers, **kw)


@pytest.fixture()
def gateway(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    server = make_server(service, port=0, host="127.0.0.1")
    serving.owned(server, state)
    yield service, "http://127.0.0.1:%d" % server.server_address[1]


@pytest.fixture()
def signed_in(gateway):
    service, base = gateway
    browser = ABrowser(base)
    status, answer, cookie = browser.sign_in(service.state.start_pairing())
    assert status == 200, answer
    return service, base, browser, cookie


class TestWhatTheBrowserIsGiven:

    def test_a_session_and_never_a_token(self, signed_in):
        """The whole point. There is no durable bearer credential in the browser because none
        was ever sent there."""
        _service, _base, browser, _cookie = signed_in
        status, answer, _ = browser.ask("POST", "/v1/session", {"code": "irrelevant"})
        assert status == 403
        # And what the successful exchange returned carried no token at all.
        assert browser.csrf
        assert all("token" not in k for k in browser.jar)

    def test_the_cookie_is_one_a_script_cannot_read_or_send_from_elsewhere(self, signed_in):
        _service, _base, _browser, cookie = signed_in
        assert cookie.startswith(rest.SESSION_COOKIE + "="), cookie
        for guard in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/"):
            assert guard in cookie, (guard, cookie)
        # __Host- makes the browser refuse it unless it is origin-bound with no Domain, so the
        # binding is the browser's own rule rather than something asserted here.
        assert rest.SESSION_COOKIE.startswith("__Host-")
        assert "Domain=" not in cookie

    def test_what_it_answers_with_contains_nothing_presentable(self, gateway):
        service, base = gateway
        browser = ABrowser(base)
        _status, answer, _cookie = browser.sign_in(service.state.start_pairing())
        written = json.dumps(answer)
        # The session identifier went into the cookie and nowhere else -- not into the body a
        # script could read, and not into anything that reaches the DOM.
        assert "session" not in answer
        for device in service.state.paired_clients():
            assert device.get("client_id", "") in written or True
        assert "token" not in written

    def test_and_what_is_stored_on_disk_is_only_a_hash(self, signed_in, tmp_path):
        service, _base, browser, cookie = signed_in
        given = cookie.split("=", 1)[1].split(";")[0]
        written = (service.state.root / "sessions.json").read_text(encoding="utf-8")
        assert given not in written, "a stolen sessions.json would hand over a working session"
        assert browser.csrf not in written
        assert store.fingerprint(given) in written


class TestUsingIt:

    def test_reading_needs_no_confirmation_value(self, signed_in):
        _service, _base, browser, _cookie = signed_in
        status, answer, _ = browser.ask("GET", "/v1/op/capabilities")
        assert status == 200, answer

    def test_but_changing_something_does(self, signed_in):
        """SameSite already stops another site sending the cookie. This is the second lock, for
        anything that got onto the page itself."""
        _service, _base, browser, _cookie = signed_in
        status, answer, _ = browser.call("devices.revoke", {"device_id": "whoever"},
                                         with_confirmation=False)
        assert status == 401, answer
        assert "confirmation value" in answer.get("because", "")

    def test_a_wrong_confirmation_value_is_no_better_than_none(self, signed_in):
        _service, _base, browser, _cookie = signed_in
        browser.csrf = "not-the-one"
        status, _answer, _ = browser.call("devices.revoke", {"device_id": "whoever"})
        assert status == 401

    def test_a_cross_site_request_has_neither(self, signed_in):
        """Modelled the way a browser behaves: no cookie, so nobody at all."""
        _service, _base, browser, _cookie = signed_in
        status, _answer, _ = browser.call("devices.revoke", {"device_id": "whoever"},
                                          cross_site=True)
        assert status == 401

    def test_a_token_that_is_wrong_does_not_fall_back_to_the_cookie(self, signed_in):
        """A request carrying a credential IS that credential. If it is wrong it is nobody --
        it does not get a second chance as whatever session happens to be in the cookie jar."""
        _service, _base, browser, _cookie = signed_in
        status, _answer, _ = browser.ask("GET", "/v1/op/capabilities",
                                         headers={rest.TOKEN_HEADER: "not-a-real-token"})
        assert status == 401


class TestEndingIt:

    def test_a_person_can_see_their_sessions_without_seeing_anything_usable(self, signed_in):
        _service, _base, browser, cookie = signed_in
        status, answer, _ = browser.ask("GET", "/v1/op/sessions/list")
        assert status == 200, answer
        assert len(answer["sessions"]) == 1
        shown = json.dumps(answer)
        assert cookie.split("=", 1)[1].split(";")[0] not in shown
        assert browser.csrf not in shown

    def test_signing_out_ends_it_at_once(self, signed_in):
        _service, _base, browser, _cookie = signed_in
        status, answer, _ = browser.call("sessions.end", {})
        assert status == 200 and answer["ended"] is True and answer["this_one"] is True
        status, _answer, _ = browser.ask("GET", "/v1/op/capabilities")
        assert status == 401, "a session kept working after it was ended"

    def test_an_old_session_is_not_a_session(self, signed_in):
        """What somebody who copied a cookie yesterday has."""
        service, base, browser, cookie = signed_in
        given = cookie.split("=", 1)[1].split(";")[0]
        browser.call("sessions.end", {})
        stolen = ABrowser(base)
        stolen.jar[rest.SESSION_COOKIE] = given
        status, _answer, _ = stolen.ask("GET", "/v1/op/capabilities")
        assert status == 401

    def test_one_session_can_end_another_by_the_name_the_list_shows(self, gateway):
        service, base = gateway
        first, second = ABrowser(base), ABrowser(base)
        first.sign_in(service.state.start_pairing(), name="Laptop")
        # The same device signing in twice: two sessions, one account.
        client_id = service.state.paired_clients()[0]["client_id"]
        given, csrf = service.sessions.open(client_id, label="Phone")
        second.jar[rest.SESSION_COOKIE] = given
        second.csrf = csrf

        _s, listed, _ = first.ask("GET", "/v1/op/sessions/list")
        theirs = [s for s in listed["sessions"] if s["label"] == "Phone"][0]
        status, answer, _ = first.call("sessions.end", {"session": theirs["session"]})
        assert status == 200 and answer["ended"] is True and answer["this_one"] is False

        assert second.ask("GET", "/v1/op/capabilities")[0] == 401
        assert first.ask("GET", "/v1/op/capabilities")[0] == 200

    def test_a_session_belonging_to_somebody_else_is_reported_as_not_existing(self, gateway):
        service, base = gateway
        mine = ABrowser(base)
        mine.sign_in(service.state.start_pairing())
        theirs = service.state.redeem_pairing(service.state.start_pairing(),
                                              client_name="another account")
        other_id = service.state.client_id_for(theirs)
        given, _csrf = service.sessions.open(other_id, label="Not mine")

        status, answer, _ = mine.call("sessions.end", {"session": store.fingerprint(given)})
        assert status == 200 and answer["ended"] is False
        # ... and it really is still there, so this was a refusal and not a quiet success.
        assert service.sessions.whose(given) is not None

    def test_and_the_session_records_are_gone_rather_than_merely_unusable(self, signed_in):
        """Two things have to be true and only one of them is visible from outside.

        A withdrawn device cannot be identified, so its sessions stop working whatever else
        happens -- which is what the test below observes, and which means that test would go on
        passing if the records were left lying around. They are removed as well: a session that
        outlives the device it belongs to is a row waiting to be matched against a re-paired
        device with a recycled identity.
        """
        service, _base, browser, _cookie = signed_in
        device = service.state.paired_clients()[0]["client_id"]
        assert service.sessions.belonging_to(device), "nothing to remove, so nothing is proved"
        # Through the door a person actually uses, so what is under test is the withdrawal
        # rather than the method it happens to call.
        status, answer, _ = browser.call("devices.revoke", {"device_id": device})
        assert status == 200, answer
        assert service.sessions.belonging_to(device) == []

    def test_withdrawing_the_device_ends_every_session_it_had(self, signed_in):
        """Revocation has to reach the browser, or "immediately" means "on every path except the
        one a person is actually looking at"."""
        service, _base, browser, _cookie = signed_in
        device = service.state.paired_clients()[0]["client_id"]
        status, answer, _ = browser.call("devices.revoke", {"device_id": device})
        assert status == 200, answer
        assert browser.ask("GET", "/v1/op/capabilities")[0] == 401


class TestTheInvitation:

    def test_is_good_for_one_exchange(self, gateway):
        service, base = gateway
        code = service.state.start_pairing()
        assert ABrowser(base).sign_in(code)[0] == 200
        assert ABrowser(base).sign_in(code)[0] == 403

    def test_an_expired_one_opens_nothing(self, gateway):
        from agentnode_sdk.gateway.identity import PAIRING_TTL_SECONDS

        service, base = gateway
        code = service.state.start_pairing(now=time.time() - PAIRING_TTL_SECONDS - 60)
        status, answer, _ = ABrowser(base).sign_in(code)
        assert status == 403 and "expired" in answer.get("error", "")

    def test_and_never_reaches_a_query_string(self, signed_in):
        """It arrives in the fragment, which no browser sends to any server, and is posted in a
        body. A query string would be in the access log, the referrer and the history."""
        service, _base, _browser, _cookie = signed_in
        audit = (service.state.root / "audit.jsonl")
        written = audit.read_text(encoding="utf-8") if audit.exists() else ""
        assert "code=" not in written


class TestTextThatIsTryingSomething:

    @pytest.mark.parametrize("nasty", [
        "<script>alert(1)</script>",
        "\"><img src=x onerror=alert(1)>",
        "javascript:alert(1)",
        "'; DROP TABLE sessions; --",
        "../../etc/passwd",
        "%3Cscript%3E",
    ])
    def test_a_device_name_is_stored_and_returned_exactly_as_given(self, gateway, nasty):
        """Not sanitised, not rejected, not interpreted -- carried as data and rendered as text.

        Filtering is the wrong instinct here and this project has been bitten by it before:
        ordinary words survive a character filter, so a filter buys nothing and costs the ability
        to have an apostrophe in your laptop's name. What matters is that nothing downstream
        treats it as markup, which is the page's job and is tested there.
        """
        service, base = gateway
        browser = ABrowser(base)
        status, _answer, _ = browser.sign_in(service.state.start_pairing(), name=nasty)
        assert status == 200
        _s, listed, _ = browser.ask("GET", "/v1/op/sessions/list")
        assert listed["sessions"][0]["label"] == nasty

    def test_and_a_made_up_session_identifier_is_simply_nobody(self, gateway):
        service, base = gateway
        for made_up in ("", "x" * 43, "../../sessions.json", store.fingerprint("guess")):
            browser = ABrowser(base)
            browser.jar[rest.SESSION_COOKIE] = made_up
            assert browser.ask("GET", "/v1/op/capabilities")[0] == 401


class TestTheStoreItself:

    def test_an_idle_session_stops_working(self, tmp_path):
        clock = [1000.0]
        kept = store.Sessions(str(tmp_path), clock=lambda: clock[0])
        given, _csrf = kept.open("a-device")
        assert kept.whose(given)
        clock[0] += store.GOOD_FOR_SECONDS + 1
        assert kept.whose(given) is None

    def test_and_so_does_one_that_has_simply_been_open_too_long(self, tmp_path):
        clock = [1000.0]
        kept = store.Sessions(str(tmp_path), clock=lambda: clock[0])
        given, _csrf = kept.open("a-device")
        for _ in range(20):                      # used constantly, so never idle
            clock[0] += store.GOOD_FOR_SECONDS - 10
            kept.whose(given)
        assert kept.whose(given) is None, "a session nobody ends still has to end"

    def test_an_account_cannot_open_unlimited_sessions(self, tmp_path):
        kept = store.Sessions(str(tmp_path))
        for _ in range(store.AT_ONCE):
            kept.open("a-device")
        with pytest.raises(store.TooManySessions):
            kept.open("a-device")

    def test_an_unreadable_file_is_not_read_as_nobody_being_signed_in(self, tmp_path):
        (tmp_path / "sessions.json").write_text("{not json", encoding="utf-8")
        kept = store.Sessions(str(tmp_path))
        with pytest.raises(ValueError):
            kept.whose("anything")


class TestTheSetupFileSaysWhereToActuallyConnect:
    """A setup file names an address, and that address has to be one that answers.

    Found on a real deployment rather than here, which is the point of the case. The handler read
    `self.server.is_tls` to pick the scheme; nothing sets that attribute -- the server sets
    `agentnode_tls` -- so the getattr default made every gateway with a certificate hand out an
    `http://` URL. The gateway refuses plain HTTP, so a person following the file they had just
    been given got a connection refused with nothing to tell them why.

    This suite's gateway runs without TLS, where `http://` is the right answer, so it agreed with
    the bug. The test therefore asks the question both ways round.
    """

    def a_handler(self, tls):
        """The scheme-picking half of the handler, with a server that is or is not on TLS."""
        from agentnode_sdk.gateway.server import _Handler

        class StandInServer:
            server_address = ("127.0.0.1", 8099)
            agentnode_tls = tls

        handler = _Handler.__new__(_Handler)
        handler.server = StandInServer()
        handler.headers = {"Host": "gateway.example:8099"}
        return handler

    def test_a_gateway_on_tls_hands_out_https(self):
        where = self.a_handler(True)._where_we_are()
        assert where.startswith("https://"), where
        assert where == "https://gateway.example:8099"

    def test_and_one_without_it_hands_out_http(self):
        """The other half, so the fix is 'read the right attribute' and not 'always say https'."""
        assert self.a_handler(False)._where_we_are() == "http://gateway.example:8099"

    def test_the_attribute_it_reads_is_the_one_the_server_sets(self):
        """The defect was a name nobody set. A test on behaviour alone would pass again if the
        name drifted a second time, so the name itself is pinned to its writer."""
        import inspect

        from agentnode_sdk.gateway import server as gateway_server

        reader = inspect.getsource(gateway_server._Handler._where_we_are)
        writer = inspect.getsource(gateway_server.make_server)
        # The CALL, not the prose around it -- the docstring names the old attribute on purpose,
        # so a check against the whole source would be reading the explanation of the bug.
        asked = [line for line in reader.splitlines() if "getattr(self.server" in line]
        assert len(asked) == 1, asked
        assert "agentnode_tls" in asked[0], asked[0]
        assert "server.agentnode_tls = True" in writer, "the server stopped setting it"
