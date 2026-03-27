"""
API request tool using crewAI @tool, requests, and environment variables.
Pattern from real CrewAI agent setups that call external APIs.
"""

import os
from typing import Optional

import requests
from crewai_tools import tool


WEATHER_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY", "")
WEATHER_BASE_URL = "https://api.openweathermap.org/data/2.5"


@tool("Weather API")
def get_weather_forecast(city: str, days: int = 3, units: str = "metric") -> dict:
    """
    Get weather forecast for a city using OpenWeatherMap API.

    Reads OPENWEATHERMAP_API_KEY from environment variables.

    Args:
        city: City name (e.g. "Paris", "Tokyo")
        days: Number of forecast days (1–5)
        units: Temperature unit — 'metric' (C), 'imperial' (F), or 'standard' (K)

    Returns:
        dict with current conditions and forecast
    """
    api_key = os.getenv("OPENWEATHERMAP_API_KEY") or WEATHER_API_KEY
    if not api_key:
        return {
            "error": "OPENWEATHERMAP_API_KEY environment variable not set",
            "city": city,
        }

    days = max(1, min(days, 5))

    try:
        # current weather
        current_resp = requests.get(
            f"{WEATHER_BASE_URL}/weather",
            params={"q": city, "appid": api_key, "units": units},
            timeout=10,
        )
        current_resp.raise_for_status()
        current = current_resp.json()

        # forecast
        forecast_resp = requests.get(
            f"{WEATHER_BASE_URL}/forecast",
            params={"q": city, "appid": api_key, "units": units, "cnt": days * 8},
            timeout=10,
        )
        forecast_resp.raise_for_status()
        forecast_data = forecast_resp.json()

        forecast_list = [
            {
                "datetime": item["dt_txt"],
                "temp": item["main"]["temp"],
                "feels_like": item["main"]["feels_like"],
                "description": item["weather"][0]["description"],
                "humidity": item["main"]["humidity"],
            }
            for item in forecast_data.get("list", [])
        ]

        return {
            "city": city,
            "country": current.get("sys", {}).get("country", ""),
            "current": {
                "temp": current["main"]["temp"],
                "feels_like": current["main"]["feels_like"],
                "description": current["weather"][0]["description"],
                "humidity": current["main"]["humidity"],
                "wind_speed": current["wind"]["speed"],
            },
            "forecast": forecast_list,
            "units": units,
            "error": None,
        }
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return {"error": f"City not found: {city}", "city": city}
        return {"error": str(e), "city": city}
    except Exception as e:
        return {"error": str(e), "city": city}
