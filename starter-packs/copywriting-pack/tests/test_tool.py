"""Tests for copywriting-pack."""


def test_run_aida_framework():
    from copywriting_pack.tool import run

    result = run(product="AgentNode", audience="developers", framework="aida", tone="persuasive")
    assert "headline" in result
    assert "body" in result
    assert "cta" in result
    assert result["framework"] == "aida"
    assert len(result["headline"]) > 0


def test_run_pas_framework():
    from copywriting_pack.tool import run

    result = run(product="TestApp", framework="pas")
    assert "headline" in result
    assert "body" in result
