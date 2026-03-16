"""Trello project board management via the Trello REST API."""

from __future__ import annotations


def run(api_key: str, operation: str, **kwargs) -> dict:
    """Manage Trello boards, lists, and cards.

    Args:
        api_key: Trello API key. Also requires 'token' in kwargs for authentication.
        operation: One of "list_boards", "get_board", "list_cards", "create_card", "move_card".
        **kwargs:
            token (str): Trello user token (required).
            board_id (str): Board ID (for "get_board").
            list_id (str): List ID (for "list_cards", "create_card", "move_card").
            name (str): Card name (for "create_card").
            description (str): Card description (for "create_card").
            card_id (str): Card ID (for "move_card").

    Returns:
        dict varying by operation.
    """
    import httpx

    token = kwargs.pop("token", "")
    if not token:
        raise ValueError("token is required (Trello user token)")

    base = "https://api.trello.com/1"
    auth_params = {"key": api_key, "token": token}

    ops = {
        "list_boards": _list_boards,
        "get_board": _get_board,
        "list_cards": _list_cards,
        "create_card": _create_card,
        "move_card": _move_card,
    }

    if operation not in ops:
        raise ValueError(f"Unknown operation: {operation}. Choose from {list(ops)}")

    with httpx.Client(timeout=30.0) as client:
        return ops[operation](client, base, auth_params, **kwargs)


def _list_boards(client, base: str, auth: dict, **kwargs) -> dict:
    resp = client.get(
        f"{base}/members/me/boards",
        params={**auth, "fields": "name,desc,url,closed,dateLastActivity"},
    )
    resp.raise_for_status()
    boards = []
    for b in resp.json():
        boards.append({
            "id": b.get("id", ""),
            "name": b.get("name", ""),
            "description": b.get("desc", ""),
            "url": b.get("url", ""),
            "closed": b.get("closed", False),
            "last_activity": b.get("dateLastActivity", ""),
        })
    return {"boards": boards, "total": len(boards)}


def _get_board(client, base: str, auth: dict, **kwargs) -> dict:
    board_id = kwargs.get("board_id", "")
    if not board_id:
        raise ValueError("board_id is required for get_board")

    resp = client.get(
        f"{base}/boards/{board_id}",
        params={**auth, "fields": "name,desc,url,closed", "lists": "open"},
    )
    resp.raise_for_status()
    data = resp.json()

    # Fetch lists for this board
    lists_resp = client.get(
        f"{base}/boards/{board_id}/lists",
        params={**auth, "fields": "name,pos,closed"},
    )
    lists_resp.raise_for_status()
    lists = []
    for lst in lists_resp.json():
        lists.append({
            "id": lst.get("id", ""),
            "name": lst.get("name", ""),
            "position": lst.get("pos", 0),
            "closed": lst.get("closed", False),
        })

    return {
        "id": data.get("id", ""),
        "name": data.get("name", ""),
        "description": data.get("desc", ""),
        "url": data.get("url", ""),
        "lists": lists,
    }


def _list_cards(client, base: str, auth: dict, **kwargs) -> dict:
    list_id = kwargs.get("list_id", "")
    if not list_id:
        raise ValueError("list_id is required for list_cards")

    resp = client.get(
        f"{base}/lists/{list_id}/cards",
        params={**auth, "fields": "name,desc,pos,due,labels,idList,url"},
    )
    resp.raise_for_status()
    cards = []
    for c in resp.json():
        cards.append({
            "id": c.get("id", ""),
            "name": c.get("name", ""),
            "description": c.get("desc", ""),
            "due": c.get("due"),
            "url": c.get("url", ""),
            "labels": [lb.get("name", "") for lb in c.get("labels", [])],
        })
    return {"cards": cards, "total": len(cards), "list_id": list_id}


def _create_card(client, base: str, auth: dict, **kwargs) -> dict:
    list_id = kwargs.get("list_id", "")
    name = kwargs.get("name", "")
    description = kwargs.get("description", "")

    if not list_id:
        raise ValueError("list_id is required for create_card")
    if not name:
        raise ValueError("name is required for create_card")

    resp = client.post(
        f"{base}/cards",
        params=auth,
        json={"idList": list_id, "name": name, "desc": description},
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "status": "created",
        "id": data.get("id", ""),
        "name": data.get("name", ""),
        "url": data.get("url", ""),
        "list_id": list_id,
    }


def _move_card(client, base: str, auth: dict, **kwargs) -> dict:
    card_id = kwargs.get("card_id", "")
    list_id = kwargs.get("list_id", "")

    if not card_id:
        raise ValueError("card_id is required for move_card")
    if not list_id:
        raise ValueError("list_id is required for move_card (destination list)")

    resp = client.put(
        f"{base}/cards/{card_id}",
        params=auth,
        json={"idList": list_id},
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "status": "moved",
        "card_id": card_id,
        "new_list_id": list_id,
        "card_name": data.get("name", ""),
    }
