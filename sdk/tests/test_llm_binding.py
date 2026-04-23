"""Edge-case tests for LLM binding in the agent runner.

Covers:
- call_llm with empty / system-only messages
- call_llm_text when LLM returns empty string
- call_llm when callable LLM raises various exception types
- call_llm with dict-style LLM binding but missing 'client' key
- call_llm with system_prompt=None and system_prompt=""
- allowed_tool_context with edge-case allowed_packages states
- ToolContext.has_tools with empty vs populated tool_specs
- LLMResult with all-None optional fields
- LLM call counter tracking across successes and failures
- system_prompt passthrough with dict-style {client, provider, model} binding
"""

import pytest

from agentnode_sdk.runtimes.agent_runner import (
    AgentContext,
    LLMResult,
    ToolContext,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(**overrides):
    """Build an AgentContext with sensible defaults."""
    defaults = {
        "goal": "Test goal",
        "allowed_packages": ["pack-a", "pack-b"],
        "max_tool_calls": 10,
        "max_iterations": 5,
        "stop_on_consecutive_errors": 3,
        "_agent_slug": "test-agent",
    }
    defaults.update(overrides)
    return AgentContext(**defaults)


def _echo_llm(messages, **kwargs):
    """Callable LLM that echoes the messages back as content."""
    return LLMResult(content=str(messages), model="echo")


def _empty_llm(messages, **kwargs):
    """Callable LLM that returns an empty string."""
    return LLMResult(content="", model="empty")


# ---------------------------------------------------------------------------
# 1. call_llm with empty messages list
# ---------------------------------------------------------------------------

class TestCallLLMEmptyMessages:
    def test_empty_messages_forwarded_to_callable(self):
        """An empty messages list should be forwarded to the LLM without error."""
        received = {}

        def capture(messages, **kwargs):
            received["messages"] = messages
            return LLMResult(content="ok")

        ctx = _make_context(llm=capture)
        result = ctx.call_llm([])
        assert result.content == "ok"
        # No system_prompt set, so messages stay empty
        assert received["messages"] == []

    def test_empty_messages_with_system_prompt(self):
        """Empty messages + system_prompt should produce one system message."""
        received = {}

        def capture(messages, **kwargs):
            received["messages"] = messages
            return LLMResult(content="ok")

        ctx = _make_context(llm=capture, system_prompt="Be helpful.")
        ctx.call_llm([])
        assert len(received["messages"]) == 1
        assert received["messages"][0]["role"] == "system"
        assert received["messages"][0]["content"] == "Be helpful."


# ---------------------------------------------------------------------------
# 2. call_llm with only system messages (no user message)
# ---------------------------------------------------------------------------

class TestCallLLMOnlySystemMessages:
    def test_system_only_no_system_prompt(self):
        """Messages with only a system role and no system_prompt should pass through."""
        received = {}

        def capture(messages, **kwargs):
            received["messages"] = messages
            return LLMResult(content="ok")

        msgs = [{"role": "system", "content": "I am a system message"}]
        ctx = _make_context(llm=capture)
        ctx.call_llm(msgs)
        assert received["messages"] == msgs

    def test_system_only_with_system_prompt_no_double(self):
        """When messages already have a system message and system_prompt is set,
        the system_prompt should NOT be prepended (no doubling)."""
        received = {}

        def capture(messages, **kwargs):
            received["messages"] = messages
            return LLMResult(content="ok")

        msgs = [{"role": "system", "content": "Existing system msg"}]
        ctx = _make_context(llm=capture, system_prompt="New system msg")
        ctx.call_llm(msgs)
        # Should not prepend because a system message already exists
        assert len(received["messages"]) == 1
        assert received["messages"][0]["content"] == "Existing system msg"


# ---------------------------------------------------------------------------
# 3. call_llm_text when LLM returns empty string
# ---------------------------------------------------------------------------

class TestCallLLMTextEmptyReturn:
    def test_returns_empty_string(self):
        """call_llm_text should return '' when the LLM returns empty content."""
        ctx = _make_context(llm=_empty_llm)
        result = ctx.call_llm_text([{"role": "user", "content": "hi"}])
        assert result == ""
        assert isinstance(result, str)

    def test_counter_still_incremented(self):
        """Even when LLM returns empty, the call counter should increase."""
        ctx = _make_context(llm=_empty_llm)
        assert ctx.llm_calls_made == 0
        ctx.call_llm_text([{"role": "user", "content": "hi"}])
        assert ctx.llm_calls_made == 1


# ---------------------------------------------------------------------------
# 4. call_llm when callable LLM raises different exception types
# ---------------------------------------------------------------------------

class TestCallLLMExceptionBubbling:
    @pytest.mark.parametrize(
        "exc_type,exc_msg",
        [
            (ValueError, "bad value"),
            (RuntimeError, "runtime failure"),
            (ConnectionError, "network down"),
        ],
    )
    def test_exception_propagates(self, exc_type, exc_msg):
        """Exceptions from callable LLMs should propagate without wrapping."""
        def failing_llm(messages, **kwargs):
            raise exc_type(exc_msg)

        ctx = _make_context(llm=failing_llm)
        with pytest.raises(exc_type, match=exc_msg):
            ctx.call_llm([{"role": "user", "content": "hi"}])

    @pytest.mark.parametrize(
        "exc_type",
        [ValueError, RuntimeError, ConnectionError],
    )
    def test_counter_not_incremented_on_failure(self, exc_type):
        """The llm_calls_made counter should NOT increment when the LLM raises."""
        def failing_llm(messages, **kwargs):
            raise exc_type("fail")

        ctx = _make_context(llm=failing_llm)
        with pytest.raises(exc_type):
            ctx.call_llm([{"role": "user", "content": "hi"}])
        assert ctx.llm_calls_made == 0


# ---------------------------------------------------------------------------
# 5. call_llm with dict-style LLM binding but missing 'client' key
# ---------------------------------------------------------------------------

class TestDictLLMMissingClient:
    def test_missing_client_key_raises_runtime_error(self):
        """A dict LLM binding without 'client' should raise RuntimeError."""
        ctx = _make_context(llm={"provider": "openai", "model": "gpt-4"})
        with pytest.raises(RuntimeError, match="must include a 'client' key"):
            ctx.call_llm([{"role": "user", "content": "hi"}])

    def test_empty_dict_raises_runtime_error(self):
        """A completely empty dict should raise RuntimeError for missing 'client'."""
        ctx = _make_context(llm={})
        with pytest.raises(RuntimeError, match="must include a 'client' key"):
            ctx.call_llm([{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# 6. call_llm with system_prompt=None (should not inject system message)
# ---------------------------------------------------------------------------

class TestSystemPromptNone:
    def test_no_system_injection(self):
        """system_prompt=None should not inject any system message."""
        received = {}

        def capture(messages, **kwargs):
            received["messages"] = messages
            return LLMResult(content="ok")

        ctx = _make_context(llm=capture, system_prompt=None)
        user_msgs = [{"role": "user", "content": "hi"}]
        ctx.call_llm(user_msgs)
        # Only the user message should be present
        assert len(received["messages"]) == 1
        assert received["messages"][0]["role"] == "user"

    def test_system_prompt_property_is_none(self):
        """The system_prompt property should return None when not set."""
        ctx = _make_context(system_prompt=None)
        assert ctx.system_prompt is None

    def test_default_system_prompt_is_none(self):
        """When system_prompt is not passed, it defaults to None."""
        ctx = _make_context()
        assert ctx.system_prompt is None


# ---------------------------------------------------------------------------
# 7. call_llm with system_prompt="" (empty string)
# ---------------------------------------------------------------------------

class TestSystemPromptEmptyString:
    def test_empty_string_does_not_inject(self):
        """system_prompt='' is falsy, so it should NOT inject a system message."""
        received = {}

        def capture(messages, **kwargs):
            received["messages"] = messages
            return LLMResult(content="ok")

        ctx = _make_context(llm=capture, system_prompt="")
        user_msgs = [{"role": "user", "content": "hi"}]
        ctx.call_llm(user_msgs)
        # Empty string is falsy, so system message should NOT be prepended
        assert len(received["messages"]) == 1
        assert received["messages"][0]["role"] == "user"

    def test_system_prompt_property_returns_empty(self):
        """The system_prompt property should return '' when set to ''."""
        ctx = _make_context(system_prompt="")
        assert ctx.system_prompt == ""


# ---------------------------------------------------------------------------
# 8. allowed_tool_context with various allowed_packages states
# ---------------------------------------------------------------------------

class TestAllowedToolContextEdgeCases:
    def test_empty_allowed_packages(self):
        """Empty allowed_packages should return a ToolContext with no tools."""
        ctx = _make_context(allowed_packages=[])
        tc = ctx.allowed_tool_context()
        assert isinstance(tc, ToolContext)
        assert tc.allowed_packages == []
        assert tc.tool_specs == []
        assert tc.has_tools is False

    def test_nonexistent_packages(self):
        """Packages not in the lockfile should result in empty tool_specs.

        allowed_tool_context loads from lockfile; nonexistent packages are
        silently skipped, producing a ToolContext with allowed_packages set
        but no tool_specs.
        """
        ctx = _make_context(
            allowed_packages=["nonexistent-package-xyz", "also-not-real"]
        )
        tc = ctx.allowed_tool_context()
        assert isinstance(tc, ToolContext)
        # The allowed_packages list is preserved
        assert tc.allowed_packages == ["nonexistent-package-xyz", "also-not-real"]
        # But no tool specs were loaded (packages don't exist in lockfile)
        assert tc.tool_specs == []
        assert tc.has_tools is False


# ---------------------------------------------------------------------------
# 9. ToolContext.has_tools with empty tool_specs list vs populated
# ---------------------------------------------------------------------------

class TestToolContextHasTools:
    def test_empty_tool_specs(self):
        tc = ToolContext(allowed_packages=["pack-a"], tool_specs=[])
        assert tc.has_tools is False

    def test_populated_tool_specs(self):
        specs = [
            {"name": "pack-a:run", "description": "Run", "input_schema": {}},
        ]
        tc = ToolContext(allowed_packages=["pack-a"], tool_specs=specs)
        assert tc.has_tools is True

    def test_default_construction(self):
        """Default ToolContext() has no tools."""
        tc = ToolContext()
        assert tc.has_tools is False
        assert tc.allowed_packages == []
        assert tc.tool_specs == []

    def test_multiple_tool_specs(self):
        specs = [
            {"name": "pack-a:run", "description": "Run", "input_schema": {}},
            {"name": "pack-b:parse", "description": "Parse", "input_schema": {}},
        ]
        tc = ToolContext(allowed_packages=["pack-a", "pack-b"], tool_specs=specs)
        assert tc.has_tools is True
        assert len(tc.tool_specs) == 2


# ---------------------------------------------------------------------------
# 10. LLMResult with all None optional fields
# ---------------------------------------------------------------------------

class TestLLMResultAllNone:
    def test_all_optional_none(self):
        """LLMResult with only content and all optionals as None."""
        r = LLMResult(content="hello")
        assert r.content == "hello"
        assert r.tool_calls is None
        assert r.usage is None
        assert r.model is None
        assert r.finish_reason is None

    def test_explicit_none(self):
        """Passing explicit None for all optional fields."""
        r = LLMResult(
            content="test",
            tool_calls=None,
            usage=None,
            model=None,
            finish_reason=None,
        )
        assert r.content == "test"
        assert r.tool_calls is None
        assert r.usage is None
        assert r.model is None
        assert r.finish_reason is None

    def test_empty_content_with_all_none(self):
        """LLMResult with empty content and all None optionals."""
        r = LLMResult(content="")
        assert r.content == ""
        assert r.tool_calls is None
        assert r.usage is None
        assert r.model is None
        assert r.finish_reason is None


# ---------------------------------------------------------------------------
# 11. call_llm counter tracking across multiple calls including one that fails
# ---------------------------------------------------------------------------

class TestLLMCallCounterWithFailures:
    def test_mixed_success_and_failure(self):
        """Counter should increment only on success, not on failure."""
        call_count = {"n": 0}

        def sometimes_fails(messages, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("boom")
            return LLMResult(content=f"call-{call_count['n']}")

        ctx = _make_context(llm=sometimes_fails)

        # Call 1: success
        r1 = ctx.call_llm([{"role": "user", "content": "1"}])
        assert r1.content == "call-1"
        assert ctx.llm_calls_made == 1

        # Call 2: fails
        with pytest.raises(RuntimeError, match="boom"):
            ctx.call_llm([{"role": "user", "content": "2"}])
        # Counter should NOT have incremented
        assert ctx.llm_calls_made == 1

        # Call 3: success again
        r3 = ctx.call_llm([{"role": "user", "content": "3"}])
        assert r3.content == "call-3"
        assert ctx.llm_calls_made == 2

    def test_multiple_successes(self):
        """Counter should increment by 1 for each successful call."""
        counter = {"n": 0}

        def counting_llm(messages, **kwargs):
            counter["n"] += 1
            return LLMResult(content=f"resp-{counter['n']}")

        ctx = _make_context(llm=counting_llm)
        assert ctx.llm_calls_made == 0

        for i in range(5):
            ctx.call_llm([{"role": "user", "content": str(i)}])
            assert ctx.llm_calls_made == i + 1

    def test_all_failures_counter_stays_zero(self):
        """If every call fails, counter should remain at 0."""
        def always_fails(messages, **kwargs):
            raise ValueError("always fails")

        ctx = _make_context(llm=always_fails)

        for _ in range(3):
            with pytest.raises(ValueError):
                ctx.call_llm([{"role": "user", "content": "hi"}])

        assert ctx.llm_calls_made == 0


# ---------------------------------------------------------------------------
# 12. system_prompt passthrough with dict-style {client, provider, model}
# ---------------------------------------------------------------------------

class TestSystemPromptWithDictLLM:
    def test_system_prompt_prepended_to_dict_llm(self):
        """When LLM is a dict, system_prompt should be prepended before
        dispatching to the provider."""
        received_messages = {}

        class FakeChat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    received_messages["messages"] = kwargs["messages"]
                    # Return a minimal OpenAI-like response
                    return _FakeOpenAIResponse("ok")

        fake_client = type("FakeClient", (), {"chat": FakeChat})()
        ctx = _make_context(
            llm={"client": fake_client, "provider": "openai", "model": "gpt-4"},
            system_prompt="You are an assistant.",
        )

        result = ctx.call_llm([{"role": "user", "content": "hello"}])
        assert result.content == "ok"

        # Verify system_prompt was prepended
        msgs = received_messages["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are an assistant."
        assert msgs[1]["role"] == "user"

    def test_no_system_prompt_with_dict_llm(self):
        """When system_prompt is None and LLM is a dict, no system message
        should be injected."""
        received_messages = {}

        class FakeChat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    received_messages["messages"] = kwargs["messages"]
                    return _FakeOpenAIResponse("ok")

        fake_client = type("FakeClient", (), {"chat": FakeChat})()
        ctx = _make_context(
            llm={"client": fake_client, "provider": "openai", "model": "gpt-4"},
            system_prompt=None,
        )

        ctx.call_llm([{"role": "user", "content": "hello"}])
        msgs = received_messages["messages"]
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"

    def test_existing_system_message_not_doubled_dict_llm(self):
        """When messages already have a system message and system_prompt is set,
        system_prompt should not duplicate it (dict-style LLM)."""
        received_messages = {}

        class FakeChat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    received_messages["messages"] = kwargs["messages"]
                    return _FakeOpenAIResponse("ok")

        fake_client = type("FakeClient", (), {"chat": FakeChat})()
        ctx = _make_context(
            llm={"client": fake_client, "provider": "openai", "model": "gpt-4"},
            system_prompt="New system",
        )

        ctx.call_llm([
            {"role": "system", "content": "Existing system"},
            {"role": "user", "content": "hello"},
        ])
        msgs = received_messages["messages"]
        # Should not have added a second system message
        system_msgs = [m for m in msgs if m["role"] == "system"]
        assert len(system_msgs) == 1
        assert system_msgs[0]["content"] == "Existing system"


# ---------------------------------------------------------------------------
# Fake OpenAI response object for dict-style LLM tests
# ---------------------------------------------------------------------------

class _FakeOpenAIResponse:
    """Minimal OpenAI-compatible response for testing."""

    def __init__(self, content, model="gpt-4", finish_reason="stop"):
        self.model = model
        self.usage = None
        self.choices = [_FakeChoice(content, finish_reason)]


class _FakeChoice:
    def __init__(self, content, finish_reason="stop"):
        self.finish_reason = finish_reason
        self.message = _FakeMessage(content)


class _FakeMessage:
    def __init__(self, content):
        self.content = content
        self.tool_calls = None
