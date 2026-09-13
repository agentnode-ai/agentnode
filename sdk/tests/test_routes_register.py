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

import pytest

from agentnode_sdk.access import mcp, rest, routes
from agentnode_sdk.gateway import server as gateway_server

#: What a path literal in the handler corresponds to in the register. Dynamic paths are matched by
#: prefix in the source, so one literal can stand for more than one address.
WHAT_THE_LITERALS_MEAN = {
    "/v1/hello": ("/v1/hello",),
    "/v1/pair": ("/v1/pair",),
    "/v1/jobs": ("/v1/jobs",),
    "/v1/token/rotate": ("/v1/token/rotate",),
    "/v1/jobs/": ("/v1/jobs/<run>", "/v1/jobs/<run>/cancel"),
}


def source_of(module):
    return io.open(inspect.getsourcefile(module), encoding="utf-8").read()


def the_handlers_own_paths() -> set:
    """Every `/v1` literal the request handler compares `self.path` against."""
    tree = ast.parse(source_of(gateway_server))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("do_GET", "do_POST"):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    if inner.value.startswith("/v1"):
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
                                       if r.kind != routes.THROUGH_THE_DISPATCHER],
                             ids=lambda r: r.path)
    def test_and_the_others_are_not_quietly_claimed_by_it(self, route):
        """If `ours()` ever widened to swallow a legacy path, the register would be describing one
        arrangement while the gateway ran another."""
        assert not rest.ours(route.path.replace("<run>", "abcd1234"))


class TestTheOnesThatStillDecide:
    """Not a failure -- a measured quantity, with a reason attached to each one."""

    def test_each_has_a_named_cost_for_migrating_it(self):
        for route in routes.still_deciding():
            assert route.what_migrating_would_break.strip(), (
                "%s is listed as still deciding for itself with no account of what moving it "
                "would break. Without that, the entry is an excuse rather than a finding."
                % route.path)

    def test_the_summary_does_not_read_as_reassurance(self):
        said = routes.what_a_reader_should_know()
        for route in routes.still_deciding():
            assert route.path in said, "%s is missing from what a reader is told" % route.path

    def test_the_older_cancel_is_the_one_that_still_holds_its_caller(self):
        """Named explicitly, because it is the gap the contract's own cancel was built to close.

        The contract's `cancel` comes back at once. This one does not, and its callers -- the CLI's
        remote cancel and the consistency suite -- read a `settled` flag and a 200-versus-202 that
        only mean something for a cancel that waited. So moving it is a client migration, not a
        server change, and until those clients move the register has to say so rather than let the
        arrangement look finished.
        """
        assert routes.BY_PATH["/v1/jobs/<run>/cancel"].kind == routes.STILL_DECIDES_FOR_ITSELF
        handler = source_of(gateway_server)
        assert "record, settled = self.service.cancel(run_id)" in handler, (
            "the older cancel changed; the register's account of why it is still where it is "
            "needs to change with it")


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
