"""A server must be owned by something that lives at least as long as it does.

`serving.owned()` defaults to the RUNNING TEST as owner, which is right for a server the test
itself makes and wrong for anything meant to outlive it. Getting that wrong is not a small
untidiness: the session-scoped gateway registered its cleanup with whichever test happened to
trigger the fixture first, was shut down when that test ended, and every later test that used it
waited thirty seconds for a server that was no longer there. Thirty-six failures, and a suite that
took twenty-five minutes instead of seven.

Nothing about that looked like a lifetime bug from the failures. It looked like the gateway being
flaky -- which is exactly what the original symptom looked like, and is why this is a test rather
than a note to be careful.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

HERE = pathlib.Path(__file__).resolve().parent


def files_to_scan():
    return sorted(HERE.glob("test_*.py")) + [HERE / "real_answers.py"]


def _fixture_scope(decorator) -> str:
    """What scope a @pytest.fixture decorator asks for. Bare `@pytest.fixture` is function."""
    if isinstance(decorator, ast.Call):
        for keyword in decorator.keywords:
            if keyword.arg == "scope" and isinstance(keyword.value, ast.Constant):
                return str(keyword.value.value)
    return "function"


def _wider_than_a_test(node) -> bool:
    for decorator in getattr(node, "decorator_list", []):
        text = ast.dump(decorator)
        if "fixture" not in text:
            continue
        if _fixture_scope(decorator) != "function":
            return True
    return False


class TestNobodyAdoptsAServerIntoATestThatWillEndFirst:

    def test_no_bare_owned_call_inside_a_fixture_that_outlives_the_test(self):
        """The check that would have caught it.

        A `serving.owned(...)` with no explicit owner inside a class- or session-scoped fixture
        hands the server to whichever test came first. Such a fixture has to own it -- by passing
        its own owner, or by stopping it in its own teardown.
        """
        offenders = []
        for path in files_to_scan():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not _wider_than_a_test(node):
                    continue
                for inner in ast.walk(node):
                    if not isinstance(inner, ast.Call):
                        continue
                    called = ast.unparse(inner.func) if hasattr(ast, "unparse") else ""
                    if not called.endswith("serving.owned"):
                        continue
                    if any(k.arg == "owner" for k in inner.keywords):
                        continue
                    offenders.append("%s::%s line %d" % (path.name, node.name, inner.lineno))
        assert not offenders, (
            "these fixtures outlive the test that would be made to clean up after them:\n  "
            + "\n  ".join(offenders))

    def test_and_the_check_sees_one_when_there_is_one(self):
        """A check that cannot fire is not a check."""
        source = (
            "import pytest\n"
            "from tests import serving\n"
            "@pytest.fixture(scope='session')\n"
            "def a_gateway():\n"
            "    serving.owned(object())\n"
        )
        tree = ast.parse(source)
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and _wider_than_a_test(node):
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call) and ast.unparse(inner.func).endswith(
                            "serving.owned"):
                        found.append(node.name)
        assert found == ["a_gateway"], found

    def test_and_a_function_scoped_fixture_is_not_flagged(self):
        """The common, correct case must stay allowed, or the rule is just noise."""
        source = (
            "import pytest\n"
            "from tests import serving\n"
            "@pytest.fixture()\n"
            "def a_gateway():\n"
            "    serving.owned(object())\n"
        )
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                assert not _wider_than_a_test(node), "a plain fixture was treated as long-lived"


class TestTheSessionGatewayOwnsItself:

    def test_it_stops_its_own_server_and_closes_its_own_state(self):
        import inspect

        from tests import real_answers

        closing = inspect.getsource(real_answers.RealGateway.close)
        assert "serving.stop" in closing, (
            "the session gateway does not give back what it made by name")
        building = inspect.getsource(real_answers.RealGateway.__init__)
        assert "serving.owned" not in building, (
            "the session gateway hands its server to whichever test comes first")
        assert "self.thread" in building, "it keeps no handle, so nothing can join its thread"

    def test_and_stopping_it_is_the_whole_four_steps(self):
        import inspect

        from tests import serving

        stopping = inspect.getsource(serving.stop)
        for step in ("shutdown", "join", "server_close", "state.close"):
            assert step in stopping, step
