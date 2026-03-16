"""Home Assistant integration via its REST API."""

from __future__ import annotations


def run(
    ha_url: str,
    token: str,
    operation: str,
    entity_id: str = "",
    **kwargs,
) -> dict:
    """Interact with Home Assistant.

    Args:
        ha_url: Base URL of your Home Assistant instance (e.g. "http://192.168.1.100:8123").
        token: Long-lived access token.
        operation: One of "list_entities", "get_state", "turn_on", "turn_off", "call_service".
        entity_id: Entity ID (e.g. "light.living_room"). Required for most operations.
        **kwargs: Extra args for call_service: domain, service, data (dict).

    Returns:
        dict varying by operation.
    """
    import httpx

    base = ha_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    ops = {
        "list_entities": _list_entities,
        "get_state": _get_state,
        "turn_on": _turn_on,
        "turn_off": _turn_off,
        "call_service": _call_service,
    }

    if operation not in ops:
        raise ValueError(f"Unknown operation: {operation}. Choose from {list(ops)}")

    with httpx.Client(timeout=30.0) as client:
        return ops[operation](client, base, headers, entity_id, **kwargs)


def _list_entities(client, base: str, headers: dict, entity_id: str, **kwargs) -> dict:
    resp = client.get(f"{base}/api/states", headers=headers)
    resp.raise_for_status()
    states = resp.json()
    entities = []
    for s in states:
        entities.append({
            "entity_id": s.get("entity_id", ""),
            "state": s.get("state", ""),
            "friendly_name": s.get("attributes", {}).get("friendly_name", ""),
        })
    return {"entities": entities, "total": len(entities)}


def _get_state(client, base: str, headers: dict, entity_id: str, **kwargs) -> dict:
    if not entity_id:
        raise ValueError("entity_id is required for get_state")
    resp = client.get(f"{base}/api/states/{entity_id}", headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return {
        "entity_id": data.get("entity_id", ""),
        "state": data.get("state", ""),
        "attributes": data.get("attributes", {}),
        "last_changed": data.get("last_changed", ""),
        "last_updated": data.get("last_updated", ""),
    }


def _turn_on(client, base: str, headers: dict, entity_id: str, **kwargs) -> dict:
    if not entity_id:
        raise ValueError("entity_id is required for turn_on")
    domain = entity_id.split(".")[0]
    payload = {"entity_id": entity_id}
    resp = client.post(
        f"{base}/api/services/{domain}/turn_on",
        headers=headers,
        json=payload,
    )
    resp.raise_for_status()
    return {"status": "success", "operation": "turn_on", "entity_id": entity_id}


def _turn_off(client, base: str, headers: dict, entity_id: str, **kwargs) -> dict:
    if not entity_id:
        raise ValueError("entity_id is required for turn_off")
    domain = entity_id.split(".")[0]
    payload = {"entity_id": entity_id}
    resp = client.post(
        f"{base}/api/services/{domain}/turn_off",
        headers=headers,
        json=payload,
    )
    resp.raise_for_status()
    return {"status": "success", "operation": "turn_off", "entity_id": entity_id}


def _call_service(client, base: str, headers: dict, entity_id: str, **kwargs) -> dict:
    domain = kwargs.get("domain", "")
    service = kwargs.get("service", "")
    data = kwargs.get("data", {})

    if not domain or not service:
        raise ValueError("domain and service are required for call_service")

    payload: dict = {}
    if entity_id:
        payload["entity_id"] = entity_id
    if isinstance(data, dict):
        payload.update(data)

    resp = client.post(
        f"{base}/api/services/{domain}/{service}",
        headers=headers,
        json=payload,
    )
    resp.raise_for_status()
    return {
        "status": "success",
        "operation": "call_service",
        "domain": domain,
        "service": service,
        "entity_id": entity_id,
    }
