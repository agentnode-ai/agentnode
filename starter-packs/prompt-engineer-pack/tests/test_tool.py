"""Tests for prompt-engineer-pack."""


def test_run_chain_of_thought():
    from prompt_engineer_pack.tool import run

    result = run(task="Classify sentiment of customer reviews", technique="chain_of_thought")
    assert "prompt" in result
    assert result["technique"] == "chain_of_thought"
    assert result["tokens_estimate"] > 0
    assert isinstance(result["tips"], list)


def test_run_few_shot():
    from prompt_engineer_pack.tool import run

    result = run(
        task="Translate English to French",
        technique="few_shot",
        examples=[{"input": "Hello", "output": "Bonjour"}],
    )
    assert "prompt" in result
    assert len(result["prompt"]) > 0
