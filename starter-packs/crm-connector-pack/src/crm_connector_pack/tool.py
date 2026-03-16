"""CRM connector supporting HubSpot API v3."""

from __future__ import annotations


def run(
    provider: str,
    api_key: str,
    operation: str,
    **kwargs,
) -> dict:
    """Interact with a CRM platform.

    Args:
        provider: CRM provider (currently "hubspot").
        api_key: API key / private app token.
        operation: One of "list_contacts", "get_contact", "create_contact", "list_deals".
        **kwargs:
            contact_id (str): For get_contact.
            email (str): For create_contact.
            firstname (str): For create_contact.
            lastname (str): For create_contact.

    Returns:
        dict varying by operation.
    """
    if provider != "hubspot":
        raise ValueError(f"Unsupported provider: {provider}. Currently only 'hubspot' is supported.")

    import httpx

    base = "https://api.hubapi.com"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    ops = {
        "list_contacts": _list_contacts,
        "get_contact": _get_contact,
        "create_contact": _create_contact,
        "list_deals": _list_deals,
    }

    if operation not in ops:
        raise ValueError(f"Unknown operation: {operation}. Choose from {list(ops)}")

    with httpx.Client(timeout=30.0) as client:
        return ops[operation](client, base, headers, **kwargs)


def _list_contacts(client, base: str, headers: dict, **kwargs) -> dict:
    limit = int(kwargs.get("limit", 100))
    resp = client.get(
        f"{base}/crm/v3/objects/contacts",
        headers=headers,
        params={"limit": min(limit, 100), "properties": "email,firstname,lastname,phone,company"},
    )
    resp.raise_for_status()
    data = resp.json()
    contacts = []
    for item in data.get("results", []):
        props = item.get("properties", {})
        contacts.append({
            "id": item.get("id", ""),
            "email": props.get("email", ""),
            "firstname": props.get("firstname", ""),
            "lastname": props.get("lastname", ""),
            "phone": props.get("phone", ""),
            "company": props.get("company", ""),
            "created_at": item.get("createdAt", ""),
        })
    return {"contacts": contacts, "total": len(contacts)}


def _get_contact(client, base: str, headers: dict, **kwargs) -> dict:
    contact_id = kwargs.get("contact_id", "")
    if not contact_id:
        raise ValueError("contact_id is required for get_contact")
    resp = client.get(
        f"{base}/crm/v3/objects/contacts/{contact_id}",
        headers=headers,
        params={"properties": "email,firstname,lastname,phone,company,lifecyclestage"},
    )
    resp.raise_for_status()
    data = resp.json()
    props = data.get("properties", {})
    return {
        "id": data.get("id", ""),
        "email": props.get("email", ""),
        "firstname": props.get("firstname", ""),
        "lastname": props.get("lastname", ""),
        "phone": props.get("phone", ""),
        "company": props.get("company", ""),
        "lifecycle_stage": props.get("lifecyclestage", ""),
        "created_at": data.get("createdAt", ""),
        "updated_at": data.get("updatedAt", ""),
    }


def _create_contact(client, base: str, headers: dict, **kwargs) -> dict:
    email = kwargs.get("email", "")
    firstname = kwargs.get("firstname", "")
    lastname = kwargs.get("lastname", "")

    if not email:
        raise ValueError("email is required for create_contact")

    payload = {
        "properties": {
            "email": email,
            "firstname": firstname,
            "lastname": lastname,
        }
    }

    resp = client.post(
        f"{base}/crm/v3/objects/contacts",
        headers=headers,
        json=payload,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "status": "created",
        "id": data.get("id", ""),
        "email": email,
        "firstname": firstname,
        "lastname": lastname,
    }


def _list_deals(client, base: str, headers: dict, **kwargs) -> dict:
    limit = int(kwargs.get("limit", 100))
    resp = client.get(
        f"{base}/crm/v3/objects/deals",
        headers=headers,
        params={"limit": min(limit, 100), "properties": "dealname,amount,dealstage,closedate,pipeline"},
    )
    resp.raise_for_status()
    data = resp.json()
    deals = []
    for item in data.get("results", []):
        props = item.get("properties", {})
        deals.append({
            "id": item.get("id", ""),
            "name": props.get("dealname", ""),
            "amount": props.get("amount", ""),
            "stage": props.get("dealstage", ""),
            "close_date": props.get("closedate", ""),
            "pipeline": props.get("pipeline", ""),
            "created_at": item.get("createdAt", ""),
        })
    return {"deals": deals, "total": len(deals)}
