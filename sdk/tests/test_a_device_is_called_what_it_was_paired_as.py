"""O6 follow-up N: a device identified by its token carries the name it was paired with.

Pairing records the device's name as `client_name`. `identify` -- the path every token door takes -- read
`name`, which a pairing does not write, so a device identified by its token had no name: a person approving a
job through such a door was shown as a client id rather than as the device they named (bundle `o6-followups`,
E0022). `identify_client` already read `client_name` then `name`; `identify` now reads them in that order too.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


@pytest.fixture()
def paired(tmp_path):
    from agentnode_sdk.gateway.identity import GatewayState

    state = GatewayState(str(tmp_path / "state"), version="test")
    token = state._issue_token(client_name="probe-laptop")
    service = SimpleNamespace(state=state, require_private_state=lambda: None)
    try:
        yield service, token
    finally:
        state.close()


def test_n1_identified_by_its_token_it_has_its_name(paired):
    from agentnode_sdk.access.dispatch import identify

    service, token = paired
    who = identify(service, token)
    assert who.client_id, "not identified at all"
    assert who.device_name == "probe-laptop", "identified by its token, the device is called %r" % who.device_name


def test_n2_identified_by_a_signed_token_it_has_its_name(paired):
    from agentnode_sdk.access.dispatch import identify
    from agentnode_sdk.gateway.identity import client_token_secret
    from agentnode_sdk.gateway.protocol import sign

    service, token = paired
    payload = {"probe": "n2"}
    who = identify(service, token, proof=(payload, sign(client_token_secret(token), payload)))
    assert who.proved, "the signature was not accepted"
    assert who.device_name == "probe-laptop", "identified by a signed token, the device is called %r" % who.device_name


def test_n3_identified_by_who_it_is_it_has_its_name(paired):
    from agentnode_sdk.access.dispatch import identify_client

    service, token = paired
    who = identify_client(service, service.state.client_id_for(token))
    assert who.device_name == "probe-laptop"


def test_n4_a_record_that_only_has_the_older_field_keeps_its_name(paired):
    """A record written before `client_name` existed carries `name`; identify still reads it."""
    from agentnode_sdk.access.dispatch import identify
    from agentnode_sdk.gateway.identity import hash_token

    service, token = paired
    state = service.state
    tokens = state._read_tokens()
    record = tokens[hash_token(token)]
    record.pop("client_name", None)
    record["name"] = "older-laptop"
    state._write_tokens(tokens)
    assert json.loads((state.root / "tokens.json").read_text(encoding="utf-8"))[hash_token(token)]["name"] == \
        "older-laptop"
    who = identify(service, token)
    assert who.device_name == "older-laptop", "a record with only `name` is called %r" % who.device_name
