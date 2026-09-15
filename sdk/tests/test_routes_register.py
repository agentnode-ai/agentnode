"""One decision point, and an honest list of everything that is not yet behind it.

`access/routes.py` claims which of this gateway's addresses go through the dispatcher, which run
before anybody has a credential, and which still decide for themselves. A claim like that is worth
exactly as much as whatever keeps it true, so nothing here trusts it: the register is compared
against the handler's own source, and a route that appears, disappears or changes its nature makes
the register wrong and this file red.

The other half is the shape of the doors themselves. A door that reaches into the gateway is a
door that can decide something, whatever its author intended, so `rest` and `mcp` are checked for
touching the service at all -- they may hand it to the dispatcher and may do nothing else with it.
"""
from __future__ import annotations

import ast
import inspect
import io
import os
import re

import pytest

from agentnode_sdk import console
from agentnode_sdk.access import mcp, rest, routes
from agentnode_sdk.gateway import server as gateway_server

#: What a path literal in the handler corresponds to in the register. Dynamic paths are matched by
#: prefix in the source, so one literal can stand for more than one address.
WHAT_THE_LITERALS_MEAN = {
    "/v1/health": ("/v1/health",),
    "/v1/hello": ("/v1/hello",),
    "/v1/pair": ("/v1/pair",),
    "/v1/session": ("/v1/session",),
    "/console/setup": ("/console/setup",),
    "/console/confirm": ("/console/confirm",),
    "/v1/jobs": ("/v1/jobs",),
    "/v1/token/rotate": ("/v1/token/rotate",),
    "/v1/jobs/": ("/v1/jobs/<run>", "/v1/jobs/<run>/cancel"),
    "/console": ("/console",),
    "/console/app.js": ("/console/app.js",),
    "/console/app.css": ("/console/app.css",),
    "/console/": ("/console",),
}

#: What counts as an address rather than any old string in the handler.
LOOKS_LIKE_AN_ADDRESS = ("/v1", "/console")


def the_console_page() -> str:
    """The page as it is SHIPPED. Not a copy of it kept beside this test, which would agree with
    itself while the file a browser loads said something else."""
    import pathlib

    import agentnode_sdk

    return (pathlib.Path(agentnode_sdk.__file__).parent / "console" / "app.js").read_text(
        encoding="utf-8")


def source_of(module):
    return io.open(inspect.getsourcefile(module), encoding="utf-8").read()


def the_handlers_own_paths() -> set:
    """Every address this gateway answers on, read from where each one is actually decided.

    Most are literals the request handler compares `self.path` against. The console's are not:
    the handler asks the console module whether a path is its own, so the table lives there and
    that is where this reads it from. Deriving it from the real table rather than from a string
    in the handler is the point -- a second page added to that table shows up here.
    """
    tree = ast.parse(source_of(gateway_server))
    found = set(console.FILES)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("do_GET", "do_POST", "_the_page"):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    if inner.value.startswith(LOOKS_LIKE_AN_ADDRESS):
                        found.add(inner.value)
    return found


class TestTheRegisterMatchesTheHandler:

    def test_every_address_the_handler_answers_on_is_in_the_register(self):
        """A route nobody wrote down is a route nobody reviewed."""
        for literal in sorted(the_handlers_own_paths()):
            assert literal in WHAT_THE_LITERALS_MEAN, (
                "the request handler answers on %r and the register has never heard of it. Add it "
                "to access/routes.py with what it does and why it is where it is." % literal)
            for path in WHAT_THE_LITERALS_MEAN[literal]:
                assert path in routes.BY_PATH, path

    def test_and_every_registered_address_still_exists(self):
        """The other direction: a register that lists routes the gateway no longer has is a
        register somebody will read and believe."""
        literals = the_handlers_own_paths()
        accounted = set()
        for literal in literals:
            accounted.update(WHAT_THE_LITERALS_MEAN.get(literal, ()))
        accounted.update(r.path for r in routes.REGISTER
                         if r.kind == routes.THROUGH_THE_DISPATCHER)
        missing = sorted(set(routes.BY_PATH) - accounted)
        assert not missing, (
            "the register lists %s, which the gateway does not answer on any more" % missing)

    def test_the_contract_routes_are_the_ones_the_adapter_claims(self):
        through = {r.path for r in routes.REGISTER if r.kind == routes.THROUGH_THE_DISPATCHER}
        assert through == {rest.NAMESPACE, rest.SCHEMA_PATH, rest.MCP_PATH}

    @pytest.mark.parametrize("route", [r for r in routes.REGISTER
                                       if r.kind == routes.THROUGH_THE_DISPATCHER],
                             ids=lambda r: r.path)
    def test_a_dispatcher_route_really_is_one(self, route):
        assert rest.ours(route.path + "capabilities" if route.path.endswith("/") else route.path)

    @pytest.mark.parametrize("route", [r for r in routes.REGISTER
                                       if r.kind not in (routes.THROUGH_THE_DISPATCHER,)],
                             ids=lambda r: r.path)
    def test_and_the_others_are_not_quietly_claimed_by_it(self, route):
        """If `ours()` ever widened to swallow a legacy path, the register would be describing one
        arrangement while the gateway ran another."""
        assert not rest.ours(route.path.replace("<run>", "abcd1234"))


class TestTheOnesThatStillDecide:
    """Not a failure -- a measured quantity, with a reason attached to each one."""

    def test_every_translator_says_what_its_clients_read(self):
        """A translator has to preserve something specific, and saying what stops it being
        rewritten into something that merely compiles."""
        for route in routes.REGISTER:
            if route.kind == routes.TRANSLATES:
                assert route.keeps.strip(), (
                    "%s translates, and nothing says what its clients read -- so nothing says "
                    "what the translation must not lose." % route.path)

    def test_the_summary_says_plainly_where_this_stands(self):
        said = routes.what_a_reader_should_know()
        for route in routes.still_deciding():
            assert route.path in said, "%s is missing from what a reader is told" % route.path

    def test_no_older_route_carries_out_a_cancellation_itself(self):
        """The gap the contract's cancel was built to close, now closed at both ends.

        The older door used to call the gateway's cancel inline and hold its caller for the
        settle window. It hands the request to the dispatcher now, and the waiting moved into
        the client, where it holds nobody but itself.
        """
        handler = source_of(gateway_server)
        assert "record, settled = self.service.cancel(run_id)" not in handler, (
            "an older route is carrying out a cancellation itself again")
        assert not routes.still_deciding(), (
            "these decide for themselves: %s"
            % [r.path for r in routes.still_deciding()])


class TestTheDoorsDoNotReachPastTheDispatcher:
    """A door that can touch the gateway can decide something, whatever it was written to do."""

    @pytest.mark.parametrize("module", [rest, mcp], ids=lambda m: m.__name__)
    def test_the_service_is_passed_along_and_never_used(self, module):
        tree = ast.parse(source_of(module))
        reached = sorted({
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name) and node.value.id == "service"
        })
        assert not reached, (
            "%s reaches into the service for %s. An adapter translates a protocol; the moment it "
            "reads the gateway it is somewhere a rule could live." % (module.__name__, reached))

    @pytest.mark.parametrize("module", [rest, mcp], ids=lambda m: m.__name__)
    def test_and_nothing_in_the_gateway_is_imported_by_name(self, module):
        text = source_of(module)
        for forbidden in ("GatewayState", "GatewayService", "worker", "runtime", "Runtime"):
            assert forbidden not in text, (
                "%s names %s. The adapters are meant to know the contract and the transport, and "
                "nothing about what is behind them." % (module.__name__, forbidden))


class TestWhatTheReaderIsTold:

    def test_the_migration_note_is_written_down_where_a_person_would_look(self):
        """The instruction was explicit that existing paths must not break silently. A note that
        lives only in a test is not a note."""
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        note = os.path.join(here, "docs", "managed-access-migration.md")
        assert os.path.exists(note), (
            "there is no migration note at %s, so a client of the older routes has nowhere to "
            "read what is changing under them" % note)
        text = io.open(note, encoding="utf-8").read()
        for route in routes.still_deciding():
            assert route.path in text, "%s is not mentioned in the migration note" % route.path


class TestThePageIsNotAWayIn:
    """The console is served without a credential, so what it can reach matters more than usual."""

    def test_it_cannot_reach_the_gateway_at_all(self):
        """Read as CODE, not as text. The module's own prose explains what it deliberately does
        not touch, and a substring search would make saying so the thing that fails."""
        tree = ast.parse(source_of(console))
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
            elif isinstance(node, ast.alias):
                used.update(node.name.split("."))
                if node.asname:
                    used.add(node.asname)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                used.update((getattr(node, "module", "") or "").split("."))
        forbidden = {"GatewayState", "GatewayService", "dispatch", "worker", "runtime",
                     "Runtime", "token", "identity", "state"}
        reached = sorted(used & forbidden)
        assert not reached, (
            "the console module uses %s. It hands back a file; the moment it can reach anything "
            "it stops being a page and starts being a door." % reached)

    @pytest.mark.parametrize("attempt", [
        "/console/../../etc/passwd",
        "/console/index.html",
        "/console/../identity.json",
        "/console%2f..%2fstate",
        "/console/state/audit.jsonl",
        "/consolex",
    ])
    def test_it_serves_exactly_one_page_and_nothing_reachable_from_it(self, attempt):
        """`FILES` is a table, not a directory. A path joined to whatever a request supplied is
        how a static route becomes a way to read the state directory."""
        assert not console.ours(attempt), attempt

    def test_the_addresses_it_claims_are_the_ones_the_register_has(self):
        for path in console.FILES:
            assert path.rstrip("/") or path
            assert routes.BY_PATH.get(path.rstrip("/") or path)


class TestThePageSendsEveryOperationTheWayItIsDeclared:
    """The console keeps its own table of which operations travel as POST, and it drifted.

    `devices.invite` was declared, the button was added, and `NEEDS_POST` was not -- so the one
    thing a customer does to add their second machine sent a GET and was answered "Diese Anfrage
    war nicht in Ordnung." Nothing red: the tests behind that feature drove the dispatcher, and
    the dispatcher was right. A real browser found it.

    The table stays (a page that fetched the contract before its first call would pay a round
    trip on every load). What does not stay is the possibility of it being wrong: the rule is
    read off the contract here, and the table is compared against it.
    """

    #: Never reached through `call()`. They happen before anybody has a session -- `hello` and
    #: `pair` on the way in, `open_session` as the thing that creates one -- so they are sent by
    #: the code that bootstraps rather than by the operation helper, and the table is not about
    #: them.
    BEFORE_THERE_IS_A_SESSION = ("hello", "pair", "open_session")

    def _table(self) -> set:
        """What the page believes must be POSTed, read out of the page itself."""
        block = re.search(r"var NEEDS_POST = \{(.*?)\};", the_console_page(), re.S)
        assert block, "the console no longer has a NEEDS_POST table; this test is looking for it"
        return set(re.findall(r'"?([a-z_]+(?:\.[a-z_]+)?)"?\s*:\s*1', block.group(1)))

    def _called(self) -> set:
        """What the page actually asks for, read out of the page itself."""
        return set(re.findall(r'call\(\s*"([^"]+)"', the_console_page()))

    def test_every_operation_the_page_calls_is_one_this_gateway_declares(self):
        from agentnode_sdk.access import contract

        called = self._called()
        assert called, "no `call(...)` was found at all, so this test is reading the wrong file"
        unknown = sorted(name for name in called if contract.find(name) is None)
        assert not unknown, (
            "the console asks for %s, which this gateway does not declare" % unknown)

    def test_and_is_sent_by_the_method_the_contract_gives_it(self):
        """The contract's rule, applied here rather than remembered there."""
        from agentnode_sdk.access import contract

        table = self._table()
        wrong = []
        for name in sorted(self._called()):
            declared = contract.find(name)
            must_post = bool(declared.changes or declared.params)
            if must_post != (name in table):
                wrong.append("%s must be sent as %s and the page sends it as %s"
                             % (name, "POST" if must_post else "GET",
                                "POST" if name in table else "GET"))
        assert not wrong, (
            "the console's NEEDS_POST table disagrees with the contract:\n  " + "\n  ".join(wrong))

    def test_and_the_table_names_nothing_that_is_not_an_operation(self):
        from agentnode_sdk.access import contract

        strangers = sorted(name for name in self._table()
                           if contract.find(name) is None
                           and name not in self.BEFORE_THERE_IS_A_SESSION)
        assert not strangers, (
            "NEEDS_POST names %s, which this gateway does not declare -- an entry for an "
            "operation that does not exist is an entry nobody will notice going stale"
            % strangers)
