"""Tests for web-design-pack."""


def test_run_modern_style():
    from web_design_pack.tool import run

    result = run(brand_name="TestBrand", primary_color="#3b82f6", style="modern")
    assert "colors" in result
    assert "typography" in result
    assert "spacing" in result
    assert "css_variables" in result
    assert "tailwind_config" in result
    assert "primary" in result["colors"]


def test_run_minimal_args():
    from web_design_pack.tool import run

    result = run()
    assert "colors" in result
    assert "border_radius" in result
