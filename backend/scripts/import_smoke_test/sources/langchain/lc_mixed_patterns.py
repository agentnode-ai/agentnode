"""
Mixed file: both a @tool function and a BaseTool subclass.
Users sometimes add a quick @tool alongside a more complete BaseTool — messy but real.
"""

from typing import Any, Optional, Type

import requests
from langchain.tools import BaseTool, tool
from pydantic import BaseModel, Field


# --- Quick @tool function ---

@tool
def ping_url(url: str) -> dict:
    """
    Check whether a URL is reachable (HEAD request).

    Args:
        url: URL to check

    Returns:
        dict with reachable, status_code, latency_ms
    """
    import time
    try:
        start = time.time()
        resp = requests.head(url, timeout=5, allow_redirects=True)
        latency_ms = round((time.time() - start) * 1000, 1)
        return {
            "url": url,
            "reachable": True,
            "status_code": resp.status_code,
            "latency_ms": latency_ms,
        }
    except Exception as e:
        return {"url": url, "reachable": False, "status_code": None, "error": str(e)}


# --- More complete BaseTool ---

class HTTPRequestInput(BaseModel):
    url: str = Field(..., description="URL to send the request to")
    method: str = Field(default="GET", description="HTTP method")
    headers: Optional[dict] = Field(default=None, description="Optional request headers")
    body: Optional[str] = Field(default=None, description="Optional request body (JSON string)")


class HTTPRequestTool(BaseTool):
    name: str = "http_request"
    description: str = (
        "Make an arbitrary HTTP request to a given URL. "
        "Supports custom headers and body. Returns response status and content."
    )
    args_schema: Type[BaseModel] = HTTPRequestInput

    def _run(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[dict] = None,
        body: Optional[str] = None,
    ) -> dict:
        import json as _json
        try:
            parsed_body = _json.loads(body) if body else None
            resp = requests.request(
                method.upper(),
                url,
                headers=headers or {},
                json=parsed_body,
                timeout=15,
            )
            try:
                response_data = resp.json()
            except Exception:
                response_data = resp.text

            return {
                "url": url,
                "method": method,
                "status_code": resp.status_code,
                "response": response_data,
                "error": None,
            }
        except Exception as e:
            return {"url": url, "method": method, "status_code": None, "response": None, "error": str(e)}

    async def _arun(self, **kwargs: Any) -> dict:
        raise NotImplementedError
