import os
from crewai_tools import tool


@tool("Slack Poster")
def post_to_slack(channel: str, message: str) -> dict:
    """Post a message to a Slack channel."""
    import requests
    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        return {"ok": False, "error": "SLACK_BOT_TOKEN not configured"}
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}"},
        json={"channel": channel, "text": message},
        timeout=10,
    )
    data = resp.json()
    return {"ok": data.get("ok", False), "ts": data.get("ts", "")}
