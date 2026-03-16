"""Tests for user-story-planner-pack."""


def test_run_agile_format():
    from user_story_planner_pack.tool import run

    result = run(feature_description="User login with email and password", format="agile")
    assert "stories" in result
    assert "epic" in result
    assert len(result["stories"]) >= 1

    story = result["stories"][0]
    assert "title" in story
    assert "as_a" in story
    assert "i_want" in story
    assert "so_that" in story
    assert "acceptance_criteria" in story


def test_run_returns_epic():
    from user_story_planner_pack.tool import run

    result = run(feature_description="Shopping cart checkout flow")
    assert len(result["epic"]) > 0
