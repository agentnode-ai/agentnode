"""
Two @tool functions in a single file sharing a helper utility.
Common pattern when someone builds a mini-toolkit in one file.
"""

import time
from typing import Optional

import requests
from langchain.tools import tool


# shared helper — not a tool itself
def _make_request(url: str, params: dict, timeout: int = 10) -> dict:
    """Internal helper for making GET requests with retry logic."""
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt == 2:
                raise
            time.sleep(1)
    return {}


@tool
def get_weather(city: str) -> dict:
    """
    Get current weather for a city using Open-Meteo API (free, no key needed).

    Args:
        city: City name to get weather for (e.g. "London", "New York")

    Returns:
        dict with temperature, wind_speed, weather_code, and city name
    """
    # first geocode the city
    geo_data = _make_request(
        "https://geocoding-api.open-meteo.com/v1/search",
        {"name": city, "count": 1},
    )
    results = geo_data.get("results", [])
    if not results:
        return {"error": f"City not found: {city}", "city": city}

    lat = results[0]["latitude"]
    lon = results[0]["longitude"]

    weather_data = _make_request(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": lat,
            "longitude": lon,
            "current_weather": True,
        },
    )
    current = weather_data.get("current_weather", {})
    return {
        "city": city,
        "temperature_c": current.get("temperature"),
        "wind_speed_kmh": current.get("windspeed"),
        "weather_code": current.get("weathercode"),
        "error": None,
    }


@tool
def get_ip_info(ip_address: Optional[str] = None) -> dict:
    """
    Get geolocation info for an IP address.

    Args:
        ip_address: IP address to look up. If None, uses the caller's public IP.

    Returns:
        dict with country, city, org, and timezone info
    """
    target = ip_address or ""
    url = f"https://ipapi.co/{target}/json/" if target else "https://ipapi.co/json/"

    try:
        data = _make_request(url, {})
        return {
            "ip": data.get("ip"),
            "city": data.get("city"),
            "region": data.get("region"),
            "country": data.get("country_name"),
            "org": data.get("org"),
            "timezone": data.get("timezone"),
            "error": None,
        }
    except Exception as e:
        return {"error": str(e), "ip": ip_address}
