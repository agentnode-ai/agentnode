"""S1: the signed agentnode.capability.v1 manifest -- canonicalisation, signing, digests, immutability."""
from __future__ import annotations

import copy

import pytest

from agentnode_sdk.capability import manifest as M
from agentnode_sdk.signing_key import generate_ed25519_keypair


def _tool() -> dict:
    tool = {
        "name": "echo",
        "description": "echo back the text",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}},
                         "required": ["text"]},
        "output_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
    }
    tool["tool_schema_digest"] = M.tool_schema_digest(tool)
    return tool


def _manifest(name: str = "echo-server") -> dict:
    source = {"transport": "stdio", "command": ["uvx", "mcp-echo@1.2.3"],
              "package": {"kind": "pypi", "name": "mcp-echo", "version": "1.2.3"}}
    return {
        "schema": M.CAPABILITY_SCHEMA,
        "capability_id": M.capability_id("acme", name),
        "capability_version": "1.0.0",
        "publisher": "acme",
        "execution_model": "agentnode_executed_mcp",
        "source": source,
        "permissions": {"network": {"allowed_domains": []}, "env_keys": [],
                        "code_execution": True, "user_approval": "required"},
        "tools": [TOOL := _tool()],
        "artifact_digest": M.artifact_digest_for_stdio(source),
        "schema_digest": M.schema_digest([TOOL]),
        "signer_role": "importing_publisher",
    }


def test_valid_manifest_validates_and_round_trip_signs_and_verifies():
    man = _manifest()
    assert M.validate_manifest(man) == []
    priv, pub = generate_ed25519_keypair()
    signed = M.sign_manifest(man, priv, pub, role="importing_publisher")
    res = M.verify_manifest(signed)
    assert res.ok, res
    assert res.status is M.VerifyStatus.VALID
    assert res.role == "importing_publisher"
    assert res.key_id.startswith("ed25519:")


def test_tamper_of_a_signed_field_is_rejected():
    priv, pub = generate_ed25519_keypair()
    signed = M.sign_manifest(_manifest(), priv, pub, role="importing_publisher")
    tampered = copy.deepcopy(signed)
    # flip a security-relevant field AFTER signing
    tampered["permissions"]["code_execution"] = False
    res = M.verify_manifest(tampered)
    assert res.status is M.VerifyStatus.INVALID, res


def test_unknown_canonicalization_version_is_refused_not_guessed():
    priv, pub = generate_ed25519_keypair()
    signed = M.sign_manifest(_manifest(), priv, pub, role="importing_publisher")
    signed["signatures"][0]["canonicalization_version"] = 999
    res = M.verify_manifest(signed)
    assert res.status is M.VerifyStatus.UNKNOWN_CANON_VERSION, res


def test_key_id_not_derived_from_the_public_key_is_refused():
    priv, pub = generate_ed25519_keypair()
    signed = M.sign_manifest(_manifest(), priv, pub, role="importing_publisher")
    signed["signatures"][0]["key_id"] = "ed25519:deadbeefdeadbeef"
    res = M.verify_manifest(signed)
    assert res.status is M.VerifyStatus.KEY_ID_MISMATCH, res


def test_missing_signature_is_missing_not_valid():
    res = M.verify_manifest(_manifest())
    assert res.status is M.VerifyStatus.MISSING, res


def test_malformed_manifest_is_rejected_before_any_signature_trust():
    bad = _manifest()
    bad["schema"] = "something.else.v1"
    priv, pub = generate_ed25519_keypair()
    # sign refuses a malformed manifest, so craft the signature shape by hand
    with pytest.raises(ValueError):
        M.sign_manifest(bad, priv, pub, role="importing_publisher")
    assert M.verify_manifest(bad).status is M.VerifyStatus.MALFORMED


def test_publisher_must_match_namespace_in_capability_id():
    bad = _manifest()
    bad["publisher"] = "notacme"
    assert any("publisher must equal" in e for e in M.validate_manifest(bad))


def test_version_is_immutable_identical_true_added_tool_false():
    base = _manifest()
    same = copy.deepcopy(base)
    assert M.version_is_immutable(base, same) is True
    added = copy.deepcopy(base)
    added["tools"].append({**_tool(), "name": "echo2"})
    # adding a tool under the SAME version changes the signed payload -> not immutable-equal
    assert M.version_is_immutable(base, added) is False


def test_adding_a_tool_requires_the_tools_to_differ_in_canonical_bytes():
    base = _manifest()
    added = copy.deepcopy(base)
    added["tools"].append({**_tool(), "name": "echo2"})
    assert M.canonical_payload(base) != M.canonical_payload(added)


def test_tool_invocation_identity_binds_capability_version_artifact_tool_schema():
    man = _manifest()
    ident = M.tool_invocation_identity(man, "echo")
    assert ident["capability_id"] == "acme/echo-server"
    assert ident["capability_version"] == "1.0.0"
    assert ident["tool_name"] == "echo"
    assert ident["artifact_digest"] == man["artifact_digest"]
    assert ident["tool_schema_digest"]["algorithm"] == "sha256"
    with pytest.raises(ValueError):
        M.tool_invocation_identity(man, "no-such-tool")


def test_tool_schema_digest_detects_a_changed_schema():
    man = _manifest()
    err_before = M.validate_manifest(man)
    assert err_before == []
    man["tools"][0]["input_schema"]["properties"]["text"]["type"] = "number"
    # the stored tool_schema_digest no longer matches the (now changed) schema
    assert any("tool_schema_digest" in e for e in M.validate_manifest(man))


def test_schema_digest_binds_the_capability_schema_and_per_tool_schemas():
    base = _manifest()
    other = _manifest()
    # change a tool's input schema -> its tool_schema_digest changes -> schema_digest must change
    other["tools"][0]["input_schema"] = {"type": "object", "properties": {"a": {"type": "string"}}}
    other["tools"][0]["tool_schema_digest"] = M.tool_schema_digest(other["tools"][0])
    other["schema_digest"] = M.schema_digest(other["tools"])
    assert base["schema_digest"]["domain"] == "agentnode.capability.v1/schema+tools"
    assert base["schema_digest"]["value"] != other["schema_digest"]["value"]
    # and a manifest whose stored schema_digest ignores a tool change is rejected by validation
    tampered = _manifest()
    tampered["tools"][0]["input_schema"] = {"type": "object"}
    tampered["tools"][0]["tool_schema_digest"] = M.tool_schema_digest(tampered["tools"][0])
    # stored schema_digest still the old aggregate -> mismatch
    assert any("schema_digest does not match" in e for e in M.validate_manifest(tampered))


def test_signing_as_a_role_records_that_role_not_a_vague_signed():
    priv, pub = generate_ed25519_keypair()
    signed = M.sign_manifest(_manifest(), priv, pub, role="importing_publisher")
    assert signed["signatures"][0]["role"] == "importing_publisher"
    # a verifier asked for a role that did not sign gets MISSING, not VALID
    assert M.verify_manifest(signed, role="upstream_publisher").status is M.VerifyStatus.MISSING
