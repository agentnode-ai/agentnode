"""The cross-account refusals, asked through the surfaces a customer actually holds.

`test_two_accounts.py` asks the dispatcher, which is where the decision is made.
`test_two_accounts_every_door.py` asks the gateway's own HTTP doors -- REST, remote MCP and the
older translated routes. What neither of them touches is the code that ships to the customer's
machine: the SDK client, the command line built on it, and the local stdio bridge. Those are
separate programs with their own parameter handling, their own output and their own chances to
print an identifier belonging to somebody else.

Two things are asserted about each of them, because only the pair is evidence:

* the second account is REFUSED, and nothing that comes back names the first account;
* the first account is still SERVED, so a surface that refused everybody cannot pass.

The last class closes the coverage claim rather than repeating a check: it drives every door this
gateway can record and then reads the gateway's OWN audit, so "every surface was exercised" is
read out of the record instead of being asserted by the file that did the driving.

The browser gets a real browser in `test_two_accounts_in_a_browser.py`. What is here is the part
that belongs with the others: that a browser session is a way of PRESENTING one account's
identity, and never a second identity of its own.
"""
from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request

import pytest

from agentnode_sdk.access import client as sdk
from agentnode_sdk.access import contract, dispatch, rest, schemas
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService, make_server
from tests.test_em3c_gateway import StandInBackend, _store_measurement
from tests.test_two_accounts import _a_customer, _a_run_by


class Two:
    """Named fields rather than a dict, so a typo in a test is an error and not a `None`."""

    def __init__(self, **what) -> None:
        self.__dict__.update(what)

    def hers(self) -> set:
        """Every string that would identify the first account if it leaked into an answer."""
        return {self.alice.account_id, self.alice.device_id, self.her_run, self.her_session}

    def names_her(self, said: str) -> str:
        """The first identifier of hers that appears, or "" -- so a failure says which one."""
        for identifier in self.hers():
            if identifier and identifier in said:
                return identifier
        return ""


@pytest.fixture()
def two_customers(tmp_path):
    """A real gateway on a real socket: two real customers, a real run, a real browser session."""
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    server = make_server(service, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d" % server.server_address[1]
    alice, bob = _a_customer(service, "alice"), _a_customer(service, "bob")
    session, csrf = service.sessions.open(alice.device_id, label="alice's browser")
    try:
        yield Two(base=base, service=service, alice=alice, bob=bob,
                  her_run=_a_run_by(service, alice), her_session=session, her_csrf=csrf,
                  where=tmp_path)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        state.close()


# ------------------------------------------------------------------ the SDK


class TestTheSdkClient:
    """`agentnode_sdk.access.client.Sandbox` -- what a program on the customer's machine uses."""

    def test_another_accounts_run_does_not_exist(self, two_customers):
        theirs = sdk.Sandbox(two_customers.base, two_customers.bob.token)
        for ask in ("status", "result", "cancel"):
            with pytest.raises(sdk.TheSandboxRefused) as refused:
                getattr(theirs, ask)(two_customers.her_run)
            assert refused.value.refusal == "no_such_run", ask
            assert refused.value.what_to_do, (
                "%s refused without saying what to do about it" % ask)

    def test_and_the_refusal_carries_nothing_of_hers(self, two_customers):
        theirs = sdk.Sandbox(two_customers.base, two_customers.bob.token)
        with pytest.raises(sdk.TheSandboxRefused) as refused:
            theirs.result(two_customers.her_run)
        said = refused.value.in_words()
        # The run id is the one identifier the asker already supplied, so it is not a leak.
        leaked = {two_customers.alice.account_id, two_customers.alice.device_id,
                  two_customers.her_session}
        assert not [s for s in leaked if s and s in said], said

    def test_the_device_list_is_the_askers_own(self, two_customers):
        seen = sdk.Sandbox(two_customers.base, two_customers.bob.token).devices()
        assert {d["device_id"] for d in seen["devices"]} == {two_customers.bob.device_id}
        assert not two_customers.names_her(json.dumps(seen))

    def test_withdrawing_another_accounts_device_does_nothing(self, two_customers):
        theirs = sdk.Sandbox(two_customers.base, two_customers.bob.token)
        said = theirs.revoke(two_customers.alice.device_id)
        assert said["withdrawn"] is False and said["runs_stopping"] == []
        assert dispatch.identify(two_customers.service, two_customers.alice.token).authenticated
        assert two_customers.service.sessions.whose(two_customers.her_session) is not None

    def test_usage_counts_only_the_asker(self, two_customers):
        said = sdk.Sandbox(two_customers.base, two_customers.bob.token).usage()
        assert said["runs"] == 0 and said["account_runs"] == 0

    def test_nothing_it_offers_can_name_another_account(self):
        """Every method is one declared operation, and none of them takes an account at all."""
        import inspect

        offered = {name for name, _ in inspect.getmembers(sdk.Sandbox, inspect.isfunction)
                   if not name.startswith("_")}
        assert offered == {"ask", "speak_mcp", "capabilities", "prepare", "submit", "status",
                           "result", "cancel", "usage", "devices", "revoke"}, (
            "a method was added to the SDK surface without this test being told about it")
        for method in sorted(offered - {"ask", "speak_mcp"}):
            source = inspect.getsource(getattr(sdk.Sandbox, method))
            assert "account" not in source, (
                "%s mentions an account, and an account a caller can name is an account a "
                "caller can choose" % method)

    def test_and_the_owner_is_still_served_by_every_one_of_them(self, two_customers):
        """A client that refused everybody would pass every test above."""
        hers = sdk.Sandbox(two_customers.base, two_customers.alice.token)
        assert hers.status(two_customers.her_run)["run_id"] == two_customers.her_run
        assert {d["device_id"] for d in hers.devices()["devices"]} \
            == {two_customers.alice.device_id}
        assert hers.usage()["account_runs"] == 1


# ------------------------------------------------------------------ the local bridge


class TestTheLocalStdioBridge:
    """`client.bridge` -- the relay a local MCP client speaks to. It holds no authority."""

    def _through(self, two_customers, token, *messages):
        said = io.StringIO()
        sdk.bridge(sdk.Sandbox(two_customers.base, token),
                   io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n"), said)
        return [json.loads(line) for line in said.getvalue().splitlines() if line.strip()]

    def _call(self, operation, arguments, which=1):
        return {"jsonrpc": "2.0", "id": which, "method": "tools/call",
                "params": {"name": schemas.tool_name_for(operation), "arguments": arguments}}

    def test_it_cannot_ask_about_another_accounts_run(self, two_customers):
        back, = self._through(two_customers, two_customers.bob.token,
                              self._call("status", {"run_id": two_customers.her_run}))
        assert back["result"]["isError"] is True
        assert back["result"]["structuredContent"]["refused"] == "no_such_run"

    def test_the_tools_it_lists_are_the_sandboxs_and_manage_nobodys_access(self, two_customers):
        back, = self._through(two_customers, two_customers.bob.token,
                              {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        offered = {tool["name"] for tool in back["result"]["tools"]}
        for name in ("devices.revoke", "devices.rotate", "devices.invite", "devices.uninvite",
                     "devices.invitations", "sessions.end", "sessions.list"):
            assert schemas.tool_name_for(name) not in offered, (
                "%s reached a model through the bridge" % name)

    def test_and_calling_one_anyway_is_refused_at_the_far_end(self, two_customers):
        back, = self._through(two_customers, two_customers.bob.token,
                              self._call("devices.revoke",
                                         {"device_id": two_customers.alice.device_id}))
        assert "error" in back, back
        assert dispatch.identify(two_customers.service, two_customers.alice.token).authenticated

    def test_it_adds_no_authority_of_its_own(self, two_customers):
        """The relay carries the customer's credential and holds no second one."""
        with_nothing = self._through(two_customers, "",
                                     self._call("status", {"run_id": two_customers.her_run}))
        said = json.dumps(with_nothing)
        assert "not_authenticated" in said or "error" in said, said
        assert not two_customers.names_her(said.replace(two_customers.her_run, ""))

    def test_and_the_owner_is_still_served_through_it(self, two_customers):
        back, = self._through(two_customers, two_customers.alice.token,
                              self._call("status", {"run_id": two_customers.her_run}))
        assert not back["result"].get("isError"), back
        assert back["result"]["structuredContent"]["run_id"] == two_customers.her_run


# ------------------------------------------------------------------ the command line


def _saved(two_customers, monkeypatch, whose, token, name="sandbox"):
    """A connection saved the way `agentnode remote connect` saves one."""
    from agentnode_sdk.gateway import client as gc
    from agentnode_sdk.gateway.connections import ConnectionStore, SavedGateway

    monkeypatch.setenv("AGENTNODE_HOME", str(two_customers.where / ("home-" + whose)))
    hello = gc.hello(two_customers.base)
    saved = SavedGateway(name=name, url=two_customers.base, token=token,
                         gateway_id=str((hello.get("gateway") or {}).get("gateway_id") or ""),
                         fingerprint=str(hello.get("fingerprint") or ""))
    ConnectionStore().save(saved)
    return saved


def _argv(**what):
    return type("Args", (), dict({"name": "sandbox", "store": None}, **what))()


def _connection_for(saved):
    from agentnode_sdk.gateway.client import GatewayConnection

    return GatewayConnection(base_url=saved.url, token=saved.token,
                             gateway_id=saved.gateway_id, fingerprint=saved.fingerprint)


class TestTheCommandLine:
    """`agentnode remote ...` -- the program the customer runs in a terminal."""

    def test_asking_about_another_accounts_run_fails_and_names_nothing(
            self, two_customers, monkeypatch):
        """`cmd_status` prints; the refusal happens in the library it calls, so ask that."""
        from agentnode_sdk.gateway import client as gc

        saved = _saved(two_customers, monkeypatch, "bob", two_customers.bob.token)
        with pytest.raises(gc.GatewayClientError) as refused:
            gc.status_of(_connection_for(saved), two_customers.her_run)
        said = str(refused.value).replace(two_customers.her_run, "")
        assert not two_customers.names_her(said), said

    def test_and_nothing_it_prints_names_the_other_account(self, two_customers, monkeypatch,
                                                           capsys):
        from agentnode_sdk.cli import remote_commands

        _saved(two_customers, monkeypatch, "bob", two_customers.bob.token)
        remote_commands.cmd_status(_argv(verbose=True))
        printed = capsys.readouterr().out
        assert not two_customers.names_her(printed), printed

    def test_stopping_another_accounts_run_fails(self, two_customers, monkeypatch, capsys):
        from agentnode_sdk.cli import remote_commands

        _saved(two_customers, monkeypatch, "bob", two_customers.bob.token)
        code = remote_commands.cmd_cancel(_argv(run=two_customers.her_run, wait=0))
        printed = capsys.readouterr().out
        assert code == 1, printed
        assert "It stopped" not in printed
        assert not two_customers.service.runs[two_customers.her_run].cancel_requested.is_set(), (
            "one customer's terminal stopped another customer's run")

    def test_no_remote_command_takes_an_account_or_a_device_that_is_not_its_own(self):
        """A parameter naming somebody else is the shape this whole file is about."""
        import inspect

        from agentnode_sdk.cli import remote_commands

        for name, command in inspect.getmembers(remote_commands, inspect.isfunction):
            if not name.startswith("cmd_"):
                continue
            source = inspect.getsource(command)
            for asked in ("args.account", "args.device", "args.client", "args.owner"):
                assert asked not in source, (
                    "`agentnode remote %s` reads %s, which lets a person at a terminal name "
                    "somebody else" % (name[4:], asked))

    def test_and_the_owner_is_still_served(self, two_customers, monkeypatch):
        from agentnode_sdk.gateway import client as gc

        saved = _saved(two_customers, monkeypatch, "alice", two_customers.alice.token)
        assert gc.status_of(_connection_for(saved),
                            two_customers.her_run)["run_id"] == two_customers.her_run


# ------------------------------------------------------------------ the browser's session


class TestABrowserSessionIsOneAccountsIdentity:
    """Not a second identity. A session PRESENTS the device that opened it."""

    def test_it_reaches_exactly_the_account_that_opened_it(self, two_customers):
        hers = dispatch.identify_session(two_customers.service, two_customers.her_session,
                                         two_customers.her_csrf)
        assert hers.account_id == two_customers.alice.account_id
        seen = dispatch.dispatch("devices.list", {}, hers, service=two_customers.service)
        assert {d["device_id"] for d in seen["devices"]} == {two_customers.alice.device_id}

    def test_and_holding_it_does_not_reach_a_run_of_another_account(self, two_customers):
        service = two_customers.service
        theirs = _a_run_by(service, two_customers.bob)
        hers = dispatch.identify_session(service, two_customers.her_session,
                                         two_customers.her_csrf)
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("status", {"run_id": theirs}, hers, service=service)
        assert refused.value.refusal == "no_such_run"

    def test_another_account_cannot_list_or_end_it(self, two_customers):
        service = two_customers.service
        theirs = sdk.Sandbox(two_customers.base, two_customers.bob.token)
        assert theirs.ask("sessions.list")["sessions"] == []
        named = dispatch.dispatch("sessions.list", {}, two_customers.alice,
                                  service=service)["sessions"]
        assert named, "alice has no session to try to end"
        assert theirs.ask("sessions.end", session=named[0]["session"])["ended"] is False
        assert service.sessions.whose(two_customers.her_session) is not None, (
            "one customer ended another customer's browser session")

    def test_and_a_session_of_a_withdrawn_device_reaches_nothing(self, two_customers):
        """A session outlives the request that made it, so withdrawal has to reach it."""
        service = two_customers.service
        dispatch.dispatch("devices.revoke", {"device_id": two_customers.alice.device_id},
                          two_customers.alice, service=service)
        assert not dispatch.identify_session(service, two_customers.her_session,
                                             two_customers.her_csrf).authenticated


# ------------------------------------------------------------------ an invitation


class TestAnotherAccountsInvitation:
    """An invitation is how a customer adds a machine, so it is a way INTO an account."""

    def test_it_is_not_listed_and_cannot_be_withdrawn(self, two_customers):
        service = two_customers.service
        made = dispatch.dispatch("devices.invite", {}, two_customers.alice, service=service)
        theirs = sdk.Sandbox(two_customers.base, two_customers.bob.token)

        assert theirs.ask("devices.invitations")["invitations"] == []
        assert theirs.ask("devices.uninvite",
                          invitation=made["invitation"])["withdrawn"] is False

        joined = dispatch.identify(service, dispatch.before_anyone(
            "pair", {"code": made["code"], "client_name": "her laptop"},
            service=service)["token"])
        assert joined.account_id == two_customers.alice.account_id, (
            "the invitation stopped working, so the check above proved nothing")

    def test_and_none_of_it_is_offered_to_a_model(self):
        for name in ("devices.invite", "devices.uninvite", "devices.invitations"):
            declared = contract.find(name)
            assert declared is not None and declared.audience != contract.TOOL, name


# ------------------------------------------------------------------ the coverage itself


#: The doors this gateway can RECORD, and nothing else. `contract.CHANNELS` is a larger set,
#: because a person enrolling an AI connection names the SHAPE of their client -- a command line,
#: a local bridge -- and those arrive through one of the doors below. The gateway does not pretend
#: to tell a CLI from any other REST client: the only thing that could distinguish them is
#: something the client says about itself, and a program on the customer's machine describing
#: itself is not evidence of anything.
DOORS_THAT_ARE_RECORDED = ("browser", "rest", "mcp", "older_door")


def _audit_lines(service):
    with open(str(service.state.root) + "/audit.jsonl", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class TestProbingThroughTheOldestDoorLeavesATrace:
    """The door with the least modern client is the one a probe would pick.

    It was also, until this file was written, the only one where being refused left no record at
    all: its status route does not go through `dispatch`, so nothing above it audited. Somebody
    walking another account's run ids through it was refused every time and invisibly.
    """

    def _ask(self, two_customers, token, run_id):
        asking = urllib.request.Request(two_customers.base + "/v1/jobs/" + run_id)
        asking.add_header(rest.TOKEN_HEADER, token)
        try:
            with urllib.request.urlopen(asking, timeout=30) as answer:
                return answer.status, answer.read().decode("utf-8")
        except urllib.error.HTTPError as refused:
            return refused.code, refused.read().decode("utf-8")

    def test_the_refusal_is_recorded_against_the_account_that_tried(self, two_customers):
        status, _said = self._ask(two_customers, two_customers.bob.token,
                                  two_customers.her_run)
        assert status == 404
        theirs = [line for line in _audit_lines(two_customers.service)
                  if line["account"] == two_customers.bob.account_id
                  and line["operation"] == "status"]
        assert theirs, "a refused cross-account read through the oldest door left no record"
        assert theirs[-1]["via"] == "older_door"
        assert theirs[-1]["outcome"] == "no_such_run"

    def test_and_repeated_attempts_look_like_what_they_are(self, two_customers):
        """One refusal is an accident; the point of the record is that ten are not."""
        for _ in range(10):
            self._ask(two_customers, two_customers.bob.token, "f" * 32)
        refused = [line for line in _audit_lines(two_customers.service)
                   if line["account"] == two_customers.bob.account_id
                   and line["via"] == "older_door" and line["outcome"] != "carried_out"]
        assert len(refused) == 10, refused

    def test_and_what_the_door_served_is_recorded_too(self, two_customers):
        status, _said = self._ask(two_customers, two_customers.alice.token,
                                  two_customers.her_run)
        assert status == 200
        hers = [line for line in _audit_lines(two_customers.service)
                if line["account"] == two_customers.alice.account_id
                and line["operation"] == "status" and line["via"] == "older_door"]
        assert hers and hers[-1]["outcome"] == "carried_out"

    def test_but_cancelling_is_not_recorded_as_a_status_call(self, two_customers):
        """That door renders a record as a by-product. A client that never asked for one must
        not appear in the log as having asked, because a compatibility claim is read off these
        lines and would then be a claim about somebody else's client that nothing measured."""
        from agentnode_sdk.gateway import client as gc

        before = len([line for line in _audit_lines(two_customers.service)
                      if line["operation"] == "status"])
        hers = gc.GatewayConnection(base_url=two_customers.base,
                                    token=two_customers.alice.token)
        try:
            gc.cancel(hers, two_customers.her_run, settle=0)
        except gc.GatewayClientError:
            pass                                   # the answer is not what this test is about
        lines = _audit_lines(two_customers.service)
        assert len([line for line in lines if line["operation"] == "status"]) == before
        assert [line for line in lines if line["operation"] == "cancel"
                and line["via"] == "older_door"], "the cancel itself was not recorded"


class TestEveryDoorThisGatewayRecordsWasReallyDriven:
    """Read out of the gateway's own audit, not asserted by the file that did the driving."""

    def test_the_recorded_doors_are_exactly_the_ones_an_adapter_can_name(self):
        """If an adapter starts naming a fifth door, this file has stopped covering them all."""
        import pathlib
        import re

        import agentnode_sdk

        named = set()
        for path in pathlib.Path(agentnode_sdk.__file__).parent.rglob("*.py"):
            named |= set(re.findall(r'via="([a-z_]+)"', path.read_text(encoding="utf-8")))
        assert named == set(DOORS_THAT_ARE_RECORDED), (
            "the doors an adapter names are %s; the doors this file covers are %s"
            % (sorted(named), sorted(DOORS_THAT_ARE_RECORDED)))
        assert set(DOORS_THAT_ARE_RECORDED) <= set(contract.CHANNELS)

    def test_a_cross_account_attempt_over_each_of_them_is_refused_and_recorded(
            self, two_customers):
        theirs, hers = two_customers.bob.token, two_customers.her_run
        service = two_customers.service

        # rest
        asking = urllib.request.Request(
            two_customers.base + rest.NAMESPACE + "status",
            data=json.dumps({"run_id": hers}).encode("utf-8"), method="POST")
        asking.add_header("Content-Type", "application/json")
        asking.add_header(rest.TOKEN_HEADER, theirs)
        asking.add_header(rest.SPEAKS_HEADER, contract.PROTOCOL_VERSION)
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(asking, timeout=30)
        said = refused.value.read().decode("utf-8").replace(hers, "")
        assert not two_customers.names_her(said), said

        # mcp
        asking = urllib.request.Request(
            two_customers.base + rest.MCP_PATH, method="POST",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": schemas.tool_name_for("status"),
                                        "arguments": {"run_id": hers}}}).encode("utf-8"))
        asking.add_header("Content-Type", "application/json")
        asking.add_header(rest.TOKEN_HEADER, theirs)
        with urllib.request.urlopen(asking, timeout=30) as answer:
            assert json.loads(answer.read().decode("utf-8"))["result"]["isError"] is True

        # older_door
        asking = urllib.request.Request(two_customers.base + "/v1/jobs/" + hers)
        asking.add_header(rest.TOKEN_HEADER, theirs)
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(asking, timeout=30)
        assert refused.value.code == 404

        # browser -- the session belongs to the OTHER account, so bob's run is the foreign one
        not_hers = _a_run_by(service, two_customers.bob)
        looking = dispatch.identify_session(service, two_customers.her_session,
                                            two_customers.her_csrf)
        with pytest.raises(dispatch.Refused):
            dispatch.dispatch("status", {"run_id": not_hers}, looking, service=service)

        drove = set()
        with open(str(service.state.root) + "/audit.jsonl", encoding="utf-8") as fh:
            for line in fh:
                entry = json.loads(line)
                if entry.get("operation") == "status" and entry.get("outcome") != "carried_out":
                    drove.add(entry.get("via"))
        missing = set(DOORS_THAT_ARE_RECORDED) - drove
        assert not missing, (
            "no refused cross-account attempt was recorded through %s, so this file's claim to "
            "cover every door is not backed by the gateway's own record" % sorted(missing))


# ------------------------------------------------------------------ who owns a run


class TestARunHasAnOwnerAndAnOwnerlessOneBelongsToNobody:
    """Found by two real browsers, and the reason that file exists.

    Ownership was recorded by resolving the submitter's TOKEN. A browser session holds a cookie
    and no token -- that is the point of it -- so every run started from the web console was
    written with no owner at all. The ownership test then read

        if record.owner_client_id and record.owner_client_id != principal.client_id: refuse

    which asks the question only when there is an answer. An ownerless run was therefore readable
    and cancellable by every authenticated customer on the gateway.

    Both halves are covered here, because either one alone would let this back in: the run must
    GET an owner, and a run without one must belong to nobody.
    """

    def _her_browser(self, two_customers):
        service = two_customers.service
        return dispatch.identify_session(service, two_customers.her_session,
                                         two_customers.her_csrf)

    def test_a_run_started_from_a_browser_records_who_started_it(self, two_customers):
        service = two_customers.service
        run = _a_run_by(service, self._her_browser(two_customers))
        record = service.runs[run]
        assert record.owner_client_id == two_customers.alice.device_id
        assert record.owner_account_id == two_customers.alice.account_id

    def test_and_another_account_cannot_reach_it(self, two_customers):
        service = two_customers.service
        run = _a_run_by(service, self._her_browser(two_customers))
        for operation in ("status", "result", "cancel"):
            with pytest.raises(dispatch.Refused) as refused:
                dispatch.dispatch(operation, {"run_id": run}, two_customers.bob,
                                  service=service)
            assert refused.value.refusal == "no_such_run", operation

    def test_and_it_is_counted_against_her_account(self, two_customers):
        """The other consequence of the same cause: a run nobody owns is a run nobody is
        charged for, and an account ceiling that a whole surface walks around is not a ceiling."""
        service = two_customers.service
        before = dispatch.dispatch("usage", {}, two_customers.alice,
                                   service=service)["account_runs"]
        _a_run_by(service, self._her_browser(two_customers))
        after = dispatch.dispatch("usage", {}, two_customers.alice,
                                  service=service)["account_runs"]
        assert after == before + 1

    def test_and_that_devices_own_ceiling_is_folded_in(self, two_customers):
        """And the third: the user scope of the policy fold was looked up by token too, so a
        console user got the unrestricted middle scope whatever their device was allowed."""
        service = two_customers.service
        service.state.set_client_allowance(two_customers.alice.token, ["api.allowed.example"])
        asked = type("R", (), {"network": "unrestricted", "allowed_domains": (),
                               "wall_clock_s": 30})()
        by_identity = service.compose(asked, client_id=two_customers.alice.device_id)
        assert by_identity.network.allowed_destinations == frozenset(), (
            "the operator policy is network-off, so nothing may widen past it")

        service.state.set_client_allowance(two_customers.alice.token, [])
        shut = service.compose(asked, client_id=two_customers.alice.device_id)
        assert shut.network.enabled is False
        assert service.policy_of_client(two_customers.alice.device_id).network.enabled is False, (
            "a device recorded as allowed nothing was folded in as allowed everything")

    def test_a_run_with_no_recorded_owner_belongs_to_nobody(self, two_customers):
        """The half that keeps holding if the half above ever regresses.

        Constructed rather than submitted, because the point is what happens to a record that
        HAS no owner -- from an older gateway directory, from a path that forgets to pass one,
        from whatever comes next. Refused for its own account as well: this is not a scoping
        rule with a gap in it, it is a record that names nobody.
        """
        from agentnode_sdk.gateway.server import RunRecord

        service = two_customers.service
        orphan = "0" * 32
        service.runs[orphan] = RunRecord(run_id=orphan, job_id="", state="finished")

        for who in (two_customers.alice, two_customers.bob,
                    self._her_browser(two_customers)):
            with pytest.raises(dispatch.Refused) as refused:
                dispatch.dispatch("status", {"run_id": orphan}, who, service=service)
            assert refused.value.refusal == "no_such_run"

    def test_and_a_run_that_names_only_a_device_is_not_enough_either(self, two_customers):
        """Both fields, or nobody. A record with half an owner is a record from a path that
        did not finish writing one, and half a scoping rule is not a scoping rule."""
        from agentnode_sdk.gateway.server import RunRecord

        service = two_customers.service
        half = "1" * 32
        service.runs[half] = RunRecord(run_id=half, job_id="", state="finished")
        service.runs[half].owner_client_id = two_customers.alice.device_id

        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("status", {"run_id": half}, two_customers.alice, service=service)
        assert refused.value.refusal == "no_such_run"


# ------------------------------------------------------------------ somebody else's setup


class TestAnotherAccountsSetupCannotBeCollected:
    """A setup file carries a WORKING CREDENTIAL, and it is handed out over its own door.

    `/console/setup` is a form POST rather than one of the contract's addresses, because the
    answer is a download and the ticket must not travel in a URL. That means it is also the one
    write on this gateway that does not go through the dispatcher, so its scoping is its own and
    has to be shown rather than inferred from the others.
    """

    def _a_setup_begun_by(self, two_customers, who):
        return dispatch.dispatch("connections.enrol",
                                 {"way_in": contract.MCP, "label": "her AI"},
                                 who, service=two_customers.service)

    def _collect(self, two_customers, session, csrf, challenge, ticket):
        import urllib.parse

        body = urllib.parse.urlencode({"challenge": challenge, "ticket": ticket,
                                       "confirm": csrf}).encode("utf-8")
        asking = urllib.request.Request(two_customers.base + "/console/setup", data=body,
                                        method="POST")
        asking.add_header("Content-Type", "application/x-www-form-urlencoded")
        asking.add_header("Cookie", "%s=%s" % (rest.SESSION_COOKIE, session))
        try:
            with urllib.request.urlopen(asking, timeout=30) as answer:
                return answer.status, answer.read().decode("utf-8")
        except urllib.error.HTTPError as refused:
            return refused.code, refused.read().decode("utf-8")

    def test_the_other_account_is_refused_and_the_ticket_is_not_spent(self, two_customers):
        service = two_customers.service
        hers = self._a_setup_begun_by(two_customers, two_customers.alice)
        his_session, his_csrf = service.sessions.open(two_customers.bob.device_id,
                                                      label="bob's browser")

        status, said = self._collect(two_customers, his_session, his_csrf,
                                     hers["challenge"], hers["ticket"])
        assert status >= 400, said
        assert not two_customers.names_her(said), said

        # AND IT IS STILL HERS TO COLLECT. A refusal that consumed the ticket on the way past
        # would be a way for one customer to stop another finishing their setup, which is a
        # smaller thing than reading their data and still not theirs to do.
        status, got = self._collect(two_customers, two_customers.her_session,
                                    two_customers.her_csrf, hers["challenge"], hers["ticket"])
        assert status == 200, got
        assert "AGENTNODE_TOKEN" in got or "X-AgentNode-Token" in got, got[:200]

    def test_and_the_ticket_is_spent_exactly_once_by_its_owner(self, two_customers):
        """Not a tenancy question on its own -- but a second download is a second copy of a
        credential, so the check above has to be against a door that really is single-use."""
        hers = self._a_setup_begun_by(two_customers, two_customers.alice)
        first, _ = self._collect(two_customers, two_customers.her_session,
                                 two_customers.her_csrf, hers["challenge"], hers["ticket"])
        again, said = self._collect(two_customers, two_customers.her_session,
                                    two_customers.her_csrf, hers["challenge"], hers["ticket"])
        assert first == 200
        assert again >= 400, said

    def test_and_the_challenge_does_not_exist_to_the_other_account(self, two_customers):
        """Before the ticket: the enrolment itself is not visible across accounts."""
        hers = self._a_setup_begun_by(two_customers, two_customers.alice)
        theirs = sdk.Sandbox(two_customers.base, two_customers.bob.token)
        with pytest.raises(sdk.TheSandboxRefused) as refused:
            theirs.ask("connections.check", challenge=hers["challenge"])
        assert refused.value.refusal == "no_such_run"


# ------------------------------------------------------------------ what a refusal gives away


class TestWhatOneAccountCanLearnAboutAnother:
    """A refusal is an answer, and an answer that varies tells somebody something.

    What is established here: a thing that EXISTS BUT IS NOT YOURS is answered exactly as a
    thing that never existed -- same refusal name, same words, same status, byte for byte once
    the identifier the asker supplied is taken out. So walking run ids, session names,
    invitation names or challenges through any door distinguishes nothing.

    THROUGH TIMING, stated rather than declined. A refusal for something that exists and a
    refusal for something that does not are produced by the same code path, but not by the same
    amount of work: the ownership comparison happens AFTER the lookup, so a record that was found
    costs a dictionary hit and a string compare that a record that was not found does not. On a
    shared machine, over enough attempts, that difference is in principle measurable, and it would
    tell A whether a run id, session name or invitation name exists at all -- and nothing more
    than that: not whose it is, not what it contains, not what it did.

    **This gateway does not defend against that and does not claim to.** Equalising it means
    constant-time work on every refusal path, which is a different piece of engineering with its
    own proof, and it is not implied by anything below. What is asserted here is the part that
    can be asserted without measuring a machine: the ANSWERS are identical, so nothing short of a
    timing attack distinguishes them.

    What A cannot learn by any of these routes: who owns the thing, what it contains, whether it
    ran, what it used, or that account B exists at all.
    """

    def _refusal_for(self, two_customers, operation, params):
        theirs = sdk.Sandbox(two_customers.base, two_customers.bob.token)
        try:
            return "carried_out", theirs.ask(operation, **params)
        except sdk.TheSandboxRefused as refused:
            return refused.refusal, {"because": refused.because,
                                     "what_to_do": refused.what_to_do}

    def _without(self, said, *identifiers):
        text = json.dumps(said, sort_keys=True)
        for one in identifiers:
            text = text.replace(str(one), "<the thing that was asked about>")
        return text

    def test_a_run_of_anothers_reads_exactly_like_a_run_that_never_existed(self, two_customers):
        never = "e" * 32
        mine = self._refusal_for(two_customers, "status", {"run_id": two_customers.her_run})
        none = self._refusal_for(two_customers, "status", {"run_id": never})
        assert mine[0] == none[0] == "no_such_run"
        assert self._without(mine[1], two_customers.her_run) \
            == self._without(none[1], never)

    def test_and_so_does_a_session(self, two_customers):
        service = two_customers.service
        named = dispatch.dispatch("sessions.list", {}, two_customers.alice,
                                  service=service)["sessions"][0]["session"]
        mine = self._refusal_for(two_customers, "sessions.end", {"session": named})
        none = self._refusal_for(two_customers, "sessions.end", {"session": "n" * 16})
        assert self._without(mine[1], named) == self._without(none[1], "n" * 16), (mine, none)

    def test_and_so_does_an_invitation(self, two_customers):
        made = dispatch.dispatch("devices.invite", {}, two_customers.alice,
                                 service=two_customers.service)
        mine = self._refusal_for(two_customers, "devices.uninvite",
                                 {"invitation": made["invitation"]})
        none = self._refusal_for(two_customers, "devices.uninvite", {"invitation": "zzzzzzzz"})
        assert self._without(mine[1], made["invitation"]) == self._without(none[1], "zzzzzzzz")

    def test_and_so_does_a_device(self, two_customers):
        mine = self._refusal_for(two_customers, "devices.revoke",
                                 {"device_id": two_customers.alice.device_id})
        none = self._refusal_for(two_customers, "devices.revoke", {"device_id": "d" * 32})
        assert self._without(mine[1], two_customers.alice.device_id) \
            == self._without(none[1], "d" * 32), (mine, none)

    def test_and_the_counters_one_account_is_shown_are_only_its_own(self, two_customers):
        """A figure that moved when somebody ELSE did something is a channel like any other."""
        service = two_customers.service
        before = dispatch.dispatch("usage", {}, two_customers.bob, service=service)
        for _ in range(3):
            _a_run_by(service, two_customers.alice)
        after = dispatch.dispatch("usage", {}, two_customers.bob, service=service)
        assert before == after, (before, after)

    def test_and_the_operators_own_figures_are_not_on_a_customer_surface_at_all(self,
                                                                               two_customers):
        """Totals across the gateway would tell every customer about every other one."""
        theirs = sdk.Sandbox(two_customers.base, two_customers.bob.token)
        said = json.dumps(theirs.usage())
        for leaks in ("accounts", "customers", "total", "everyone", "gateway_runs"):
            assert leaks not in said, ("usage carries %r, which is about more than the asker"
                                       % leaks)


# ------------------------------------------------------------------ every write, refused


class TestEveryCrossAccountWriteIsRefused:
    """One class, walking the whole list the criterion names, each with a REFUSED attempt.

    Withdraw a device, rotate one, end a session, cancel a run, submit a run, consume or
    invalidate an invitation or an enrolment ticket, alter a ceiling, cause a suspension. Several
    of these are covered above from a surface's point of view; they are gathered here from the
    ACCOUNT's, so the list can be read against the list rather than reassembled by a reader.
    """

    def test_withdraw_a_device(self, two_customers):
        service = two_customers.service
        said = dispatch.dispatch("devices.revoke", {"device_id": two_customers.alice.device_id},
                                 two_customers.bob, service=service)
        assert said["withdrawn"] is False and said["runs_stopping"] == []
        assert dispatch.identify(service, two_customers.alice.token).authenticated

    def test_rotate_a_device(self, two_customers):
        """`devices.rotate` mints a SUCCESSOR credential, so reaching another account's device
        with it would be worse than withdrawing one: it hands over a working token."""
        declared = contract.find("devices.rotate")
        assert declared is not None
        assert not [f for f in declared.params if "device" in f.name or "account" in f.name], (
            "devices.rotate takes a parameter naming a device, so a caller can name one that is "
            "not theirs: %s" % [f.name for f in declared.params])

        # And what it actually rotates is the CALLER's own credential.
        service = two_customers.service
        before = two_customers.bob.token
        said = dispatch.dispatch("devices.rotate", {}, two_customers.bob, service=service)
        assert said["token"] != before
        assert dispatch.identify(service, said["token"]).device_id == two_customers.bob.device_id
        assert dispatch.identify(service, two_customers.alice.token).authenticated, (
            "rotating one customer's credential disturbed another's")

    def test_end_a_session(self, two_customers):
        service = two_customers.service
        named = dispatch.dispatch("sessions.list", {}, two_customers.alice,
                                  service=service)["sessions"]
        assert named, "alice has no session to try to end"
        said = dispatch.dispatch("sessions.end", {"session": named[0]["session"]},
                                 two_customers.bob, service=service)
        assert said["ended"] is False
        assert service.sessions.whose(two_customers.her_session) is not None

    def test_cancel_a_run(self, two_customers):
        service = two_customers.service
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("cancel", {"run_id": two_customers.her_run}, two_customers.bob,
                              service=service)
        assert refused.value.refusal == "no_such_run"
        assert not service.runs[two_customers.her_run].cancel_requested.is_set()

    def test_submit_a_run_into_another_account(self, two_customers):
        """There is no parameter for it, and naming one exactly right is refused rather than
        ignored -- an unknown field that is dropped silently is a field somebody will rely on."""
        service = two_customers.service
        declared = contract.find("submit")
        assert not [f for f in declared.params if "account" in f.name or "owner" in f.name]

        # A COMPLETE, otherwise-valid submission, plus the one stray field. The first version of
        # this sent an incomplete one, so `malformed` came back for a MISSING required parameter
        # whether or not unknown ones were refused -- and a counter-check found it: removing the
        # unknown-parameter check left the test green. It was not evidence for the mechanism it
        # was pointed at.
        import base64
        import hashlib
        import uuid

        code = b"print(1)\n"
        shown = dispatch.dispatch(
            "prepare",
            {"artifact_sha256": hashlib.sha256(code).hexdigest(),
             "artifact_bytes": len(code), "wall_clock_s": 30},
            two_customers.bob, service=service)
        whole = {"run_id": uuid.uuid4().hex,
                 "artifact": base64.b64encode(code).decode("ascii"),
                 "wall_clock_s": 30,
                 "accepted_disclosure": shown["accepted_disclosure"]}

        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("submit",
                              dict(whole, account_id=two_customers.alice.account_id),
                              two_customers.bob, service=service)
        assert refused.value.refusal == "malformed", refused.value.because
        assert "account_id" in refused.value.because, refused.value.because

        # The control: the SAME submission without the stray field is accepted, so what was
        # refused above was the field and not the request.
        his = dispatch.dispatch("submit", whole, two_customers.bob, service=service)["run_id"]
        assert service.runs[his].owner_account_id == two_customers.bob.account_id

    def test_consume_an_invitation(self, two_customers):
        service = two_customers.service
        made = dispatch.dispatch("devices.invite", {}, two_customers.alice, service=service)
        assert dispatch.dispatch("devices.uninvite", {"invitation": made["invitation"]},
                                 two_customers.bob, service=service)["withdrawn"] is False
        joined = dispatch.identify(service, dispatch.before_anyone(
            "pair", {"code": made["code"], "client_name": "hers"}, service=service)["token"])
        assert joined.account_id == two_customers.alice.account_id, (
            "the invitation stopped working, so the refusal above proved nothing")

    def test_consume_an_enrolment_ticket(self, two_customers):
        """The download ticket is a working credential. Driven over its real door in
        `TestAnotherAccountsSetupCannotBeCollected`; asserted here at the decision."""
        service = two_customers.service
        hers = dispatch.dispatch("connections.enrol",
                                 {"way_in": contract.MCP, "label": "her AI"},
                                 two_customers.alice, service=service)
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("connections.check", {"challenge": hers["challenge"]},
                              two_customers.bob, service=service)
        assert refused.value.refusal == "no_such_run"
        # UNSPENT, asked of the hash rather than of the ticket: the ticket is handed to its
        # owner once and never stored, so there is nothing left to compare it against. Spending
        # one clears the hash, which makes "still there" the same question it always was.
        assert service.connections.about(hers["challenge"])["ticket_sha256"], (
            "a refused attempt spent somebody else's ticket")

    def test_alter_a_ceiling_or_cause_a_suspension(self, two_customers):
        """Neither is a contract operation at all, so there is no spelling of either that a
        customer credential can reach. That is the whole mechanism and it is worth stating as
        one: an operation that does not exist cannot be scoped wrongly."""
        reachable = {op.name for op in contract.OPERATIONS}
        for forbidden in ("limits.set", "allowance.set", "accounts.suspend", "accounts.restore",
                          "policy.set", "stop", "resume"):
            assert forbidden not in reachable, forbidden

        service = two_customers.service
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("limits.set", {"runs_per_window": 9999}, two_customers.bob,
                              service=service)
        assert refused.value.refusal == "unknown_operation"

        from agentnode_sdk.gateway.allowance import read_allowance

        was = read_allowance(service.state.root)
        assert read_allowance(service.state.root) == was
