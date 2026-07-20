"""A1-M1: sealed python_distribution* fields — schema, pair invariant, integrity."""
from __future__ import annotations

import pytest

from agentnode_sdk import lock_integrity as li


def _agent(**over):
    e = {
        "version": "2.1.4",              # ANP manifest version (separate identity)
        "package_type": "agent",
        "runtime": "python",
        "entrypoint": "pkg.agent:run",
        "artifact_hash": "sha256:" + "a" * 64,
    }
    e.update(over)
    return e


# ---- pair invariant (§3) ----

def test_agent_both_absent_ok():
    li.validate_python_distribution_fields(_agent())


def test_agent_both_valid_ok():
    li.validate_python_distribution_fields(
        _agent(python_distribution="foo-bar", python_distribution_version="2.0.0")
    )


def test_only_one_present_rejected():
    with pytest.raises(li.PythonDistributionFieldError):
        li.validate_python_distribution_fields(_agent(python_distribution="foo-bar"))
    with pytest.raises(li.PythonDistributionFieldError):
        li.validate_python_distribution_fields(_agent(python_distribution_version="2.0.0"))


@pytest.mark.parametrize("ptype", ["toolpack", "mcp", "skill"])
def test_fields_forbidden_on_non_agent(ptype):
    e = _agent(package_type=ptype, python_distribution="foo-bar",
               python_distribution_version="2.0.0")
    with pytest.raises(li.PythonDistributionFieldError):
        li.validate_python_distribution_fields(e)


def test_non_canonical_name_rejected():
    with pytest.raises(li.PythonDistributionFieldError):
        li.validate_python_distribution_fields(
            _agent(python_distribution="Foo_Bar", python_distribution_version="2.0.0")
        )


def test_bad_version_rejected():
    with pytest.raises(li.PythonDistributionFieldError):
        li.validate_python_distribution_fields(
            _agent(python_distribution="foo-bar", python_distribution_version="not.a.version!")
        )


def test_empty_values_rejected():
    for over in ({"python_distribution": "", "python_distribution_version": "2.0.0"},
                 {"python_distribution": "foo", "python_distribution_version": "  "}):
        with pytest.raises(li.PythonDistributionFieldError):
            li.validate_python_distribution_fields(_agent(**over))


# ---- integrity / sealing ----

def test_fields_are_canonical_and_sensitive():
    assert "python_distribution" in li.CANONICAL_FIELDS
    assert "python_distribution_version" in li.CANONICAL_FIELDS
    assert "python_distribution" in li.SENSITIVE_FIELDS
    assert "python_distribution_version" in li.SENSITIVE_FIELDS
    # NOT mutable
    assert "python_distribution" not in li.MUTABLE_FIELDS


def test_legacy_entry_hash_unchanged():
    """An agent entry WITHOUT the new fields hashes identically to pre-M1 (the
    fields are appended and _build_canonical omits absent fields)."""
    e = _agent()
    # canonical omits the absent fields entirely
    canon = li._build_canonical(e, canonical_version=li.CANONICAL_VERSION)
    assert "python_distribution" not in canon
    assert "python_distribution_version" not in canon


def test_sealing_the_fields_changes_the_hash():
    base = li.compute_integrity(_agent())["hash"]
    sealed = li.compute_integrity(
        _agent(python_distribution="foo-bar", python_distribution_version="2.0.0")
    )["hash"]
    assert base != sealed


def test_tampering_a_sealed_field_is_detected():
    e = li.seal_entry(
        _agent(python_distribution="foo-bar", python_distribution_version="2.0.0")
    )
    assert li.verify_entry("s", e).status == "verified"
    e["python_distribution"] = "evil-swap"
    assert li.verify_entry("s", e).status == "mismatch"


def test_reseal_invents_no_fields_for_legacy_entry():
    """Reseal of a legacy agent entry must NOT synthesize the new fields from the
    environment — sealing only recomputes over present fields."""
    e = _agent()
    sealed = li.seal_entry(e)
    assert "python_distribution" not in sealed
    assert "python_distribution_version" not in sealed
    assert li.verify_entry("s", sealed).status == "verified"
