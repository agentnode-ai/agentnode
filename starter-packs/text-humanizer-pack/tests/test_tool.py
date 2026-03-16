"""Tests for text-humanizer-pack."""


def test_run_casual_style():
    from text_humanizer_pack.tool import run

    result = run(
        text="It is important to note that the system will facilitate synergy.",
        style="casual",
    )
    assert "original" in result
    assert "humanized" in result
    assert result["style"] == "casual"
    assert len(result["humanized"]) > 0


def test_run_preserves_meaning():
    from text_humanizer_pack.tool import run

    result = run(text="The meeting is scheduled for Monday.", style="casual")
    assert "humanized" in result
    assert isinstance(result["changes"], list)
