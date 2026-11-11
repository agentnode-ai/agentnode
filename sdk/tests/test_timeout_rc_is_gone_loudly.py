"""#67: `agentnode_sdk.conformance.runner.TIMEOUT_RC` (0.24.1) stays gone -- loudly, with the way on.

0.24.1 reported a timeout as exit code -1, and `TIMEOUT_RC = -1` named it. 0.25.0 reports no exit code
for a run the sandbox stopped (`Outcome.exit_code is None`) and says why in `Outcome.reason`
(`TIMED_OUT`, EM3C-E4-CLASSIFY-0001). An alias `TIMEOUT_RC = -1` would make old code import again
and then never match: a timeout would pass for an ordinary ending, silently. So the name is not
brought back. What changes is the failure: it names the replacement instead of a bare "no attribute".
"""
from __future__ import annotations

import importlib

import pytest


def test_importing_the_old_name_still_fails():
    # `from ... import` turns any AttributeError of a module __getattr__ into the interpreter's own
    # ImportError and drops its message (measured on 3.11.9). Raising ImportError instead would make
    # hasattr()/getattr(..., default) raise for this name. So the import fails exactly as before;
    # the guidance is on attribute access (below) and in sdk/CHANGELOG.md.
    with pytest.raises(ImportError):
        from agentnode_sdk.conformance.runner import TIMEOUT_RC  # noqa: F401


def test_attribute_access_fails_the_same_way():
    runner = importlib.import_module("agentnode_sdk.conformance.runner")
    with pytest.raises(AttributeError) as caught:
        runner.TIMEOUT_RC
    said = str(caught.value)
    assert "Outcome.reason" in said and "TIMED_OUT" in said and "SUPERSEDED_TIMEOUT_RC" in said
    assert not hasattr(runner, "TIMEOUT_RC")


def test_no_value_is_handed_out_under_the_old_name():
    # the whole point: nobody can compare against -1 by the old name and get a silent False
    runner = importlib.import_module("agentnode_sdk.conformance.runner")
    assert "TIMEOUT_RC" not in vars(runner)
    assert getattr(runner, "TIMEOUT_RC", "absent") == "absent"


def test_the_rest_of_the_module_is_unchanged():
    runner = importlib.import_module("agentnode_sdk.conformance.runner")
    assert runner.SUPERSEDED_TIMEOUT_RC == -1
    assert runner.OOM_KILLED_RC == 137
    assert runner.TIMEOUT_MARKER == "[sandbox timed out after"
    with pytest.raises(AttributeError) as caught:
        runner.SOMETHING_THAT_NEVER_EXISTED
    assert "Outcome.reason" not in str(caught.value)


def test_a_timeout_really_has_no_exit_code_in_this_version():
    # the premise of refusing the alias, held here so the refusal cannot outlive its reason
    from agentnode_sdk.gateway.protocol import TIMED_OUT
    from agentnode_sdk.sandbox.backend import Outcome

    o = Outcome(None, "", "[sandbox timed out after 1s]", reason=TIMED_OUT)
    rc, _out, _err = o
    assert rc is None and o.reason == TIMED_OUT
    assert rc != -1
