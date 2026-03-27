from langchain.tools import BaseTool


class WeatherLookup(BaseTool):
    name = "weather_lookup"
    description = "Look up current weather for a city"

    def _run(self, city: str) -> dict:
        import requests
        resp = requests.get(
            "https://api.weather.example.com/current",
            params={"city": city},
            timeout=10,
        )
        data = resp.json()
        return {"city": city, "temp_c": data.get("temp"), "condition": data.get("condition")}
