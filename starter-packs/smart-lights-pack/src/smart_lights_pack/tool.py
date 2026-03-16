"""Philips Hue smart lights control via the bridge REST API."""

from __future__ import annotations


def run(
    bridge_ip: str,
    api_key: str,
    operation: str,
    light_id: str = "",
    **kwargs,
) -> dict:
    """Control Philips Hue lights.

    Args:
        bridge_ip: IP address of the Hue bridge.
        api_key: Hue bridge API key (username).
        operation: One of "list_lights", "get_state", "turn_on", "turn_off", "set_scene".
        light_id: Light ID (e.g. "1"). Required for single-light operations.
        **kwargs:
            brightness (int 1-254): Brightness level for turn_on.
            color (str): Hex color (e.g. "#ff0000") for turn_on.
            scene_name (str): Scene name for set_scene.

    Returns:
        dict varying by operation.
    """
    import httpx

    base = f"http://{bridge_ip}/api/{api_key}"

    ops = {
        "list_lights": _list_lights,
        "get_state": _get_state,
        "turn_on": _turn_on,
        "turn_off": _turn_off,
        "set_scene": _set_scene,
    }

    if operation not in ops:
        raise ValueError(f"Unknown operation: {operation}. Choose from {list(ops)}")

    with httpx.Client(timeout=15.0) as client:
        return ops[operation](client, base, light_id, **kwargs)


def _list_lights(client, base: str, light_id: str, **kwargs) -> dict:
    resp = client.get(f"{base}/lights")
    resp.raise_for_status()
    data = resp.json()
    lights = []
    for lid, info in data.items():
        lights.append({
            "id": lid,
            "name": info.get("name", ""),
            "type": info.get("type", ""),
            "on": info.get("state", {}).get("on", False),
            "brightness": info.get("state", {}).get("bri", 0),
            "reachable": info.get("state", {}).get("reachable", False),
        })
    return {"lights": lights, "total": len(lights)}


def _get_state(client, base: str, light_id: str, **kwargs) -> dict:
    if not light_id:
        raise ValueError("light_id is required for get_state")
    resp = client.get(f"{base}/lights/{light_id}")
    resp.raise_for_status()
    data = resp.json()
    state = data.get("state", {})
    return {
        "light_id": light_id,
        "name": data.get("name", ""),
        "on": state.get("on", False),
        "brightness": state.get("bri", 0),
        "hue": state.get("hue", 0),
        "saturation": state.get("sat", 0),
        "color_mode": state.get("colormode", ""),
        "reachable": state.get("reachable", False),
    }


def _turn_on(client, base: str, light_id: str, **kwargs) -> dict:
    if not light_id:
        raise ValueError("light_id is required for turn_on")

    body: dict = {"on": True}

    brightness = kwargs.get("brightness")
    if brightness is not None:
        body["bri"] = max(1, min(254, int(brightness)))

    color = kwargs.get("color")
    if color and isinstance(color, str):
        xy = _hex_to_xy(color)
        body["xy"] = xy

    resp = client.put(f"{base}/lights/{light_id}/state", json=body)
    resp.raise_for_status()
    return {
        "status": "success",
        "operation": "turn_on",
        "light_id": light_id,
        "settings": body,
    }


def _turn_off(client, base: str, light_id: str, **kwargs) -> dict:
    if not light_id:
        raise ValueError("light_id is required for turn_off")
    resp = client.put(f"{base}/lights/{light_id}/state", json={"on": False})
    resp.raise_for_status()
    return {"status": "success", "operation": "turn_off", "light_id": light_id}


def _set_scene(client, base: str, light_id: str, **kwargs) -> dict:
    scene_name = kwargs.get("scene_name", "")
    if not scene_name:
        raise ValueError("scene_name is required for set_scene")

    # Fetch available scenes
    resp = client.get(f"{base.rsplit('/lights', 1)[0]}/scenes" if "/lights" in base else f"{base}/scenes")
    # base doesn't include /lights, so this is fine:
    resp2 = client.get(f"{base}/scenes")
    resp2.raise_for_status()
    scenes = resp2.json()

    # Find matching scene by name (case-insensitive)
    scene_id = None
    for sid, info in scenes.items():
        if info.get("name", "").lower() == scene_name.lower():
            scene_id = sid
            break

    if not scene_id:
        available = [info.get("name", sid) for sid, info in scenes.items()]
        raise ValueError(f"Scene '{scene_name}' not found. Available: {available}")

    # Activate scene via group 0 (all lights)
    resp3 = client.put(f"{base}/groups/0/action", json={"scene": scene_id})
    resp3.raise_for_status()
    return {"status": "success", "operation": "set_scene", "scene_name": scene_name, "scene_id": scene_id}


def _hex_to_xy(hex_color: str) -> list[float]:
    """Convert a hex colour string to CIE xy coordinates for Hue API."""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16) / 255.0
    g = int(hex_color[2:4], 16) / 255.0
    b = int(hex_color[4:6], 16) / 255.0

    # Apply gamma correction
    r = ((r + 0.055) / 1.055) ** 2.4 if r > 0.04045 else r / 12.92
    g = ((g + 0.055) / 1.055) ** 2.4 if g > 0.04045 else g / 12.92
    b = ((b + 0.055) / 1.055) ** 2.4 if b > 0.04045 else b / 12.92

    # Convert to XYZ using Wide RGB D65 matrix
    x = r * 0.664511 + g * 0.154324 + b * 0.162028
    y = r * 0.283881 + g * 0.668433 + b * 0.047685
    z = r * 0.000088 + g * 0.072310 + b * 0.986039

    total = x + y + z
    if total == 0:
        return [0.0, 0.0]

    return [round(x / total, 4), round(y / total, 4)]
