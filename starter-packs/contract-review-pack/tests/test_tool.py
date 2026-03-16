"""Tests for contract-review-pack."""


def test_run_finds_risks():
    from contract_review_pack.tool import run

    text = (
        "This agreement includes automatic renewal and the vendor shall have "
        "no liability for any damages. The indemnification clause requires "
        "unlimited indemnification from the client."
    )
    result = run(text=text, check_risks=True, extract_terms=True)

    assert "risks" in result
    assert "key_terms" in result
    assert "summary" in result
    assert "stats" in result
    assert result["stats"]["word_count"] > 0


def test_run_clean_contract():
    from contract_review_pack.tool import run

    result = run(text="This is a simple agreement between two parties.", check_risks=True)
    assert "summary" in result
    assert isinstance(result["risks"], list)
