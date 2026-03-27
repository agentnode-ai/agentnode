"""
REST API client tool using BaseTool with self.api_key and self.base_url.
Self references in _run body — should break on import.
"""

from typing import Any, Dict, Optional, Type

import requests
from langchain.tools import BaseTool
from pydantic import BaseModel, Field


class APIRequestInput(BaseModel):
    endpoint: str = Field(..., description="API endpoint path, e.g. /v1/users")
    method: str = Field(default="GET", description="HTTP method: GET, POST, PUT, DELETE")
    payload: Optional[Dict[str, Any]] = Field(default=None, description="JSON request body")
    params: Optional[Dict[str, str]] = Field(default=None, description="Query string parameters")


class APIClientTool(BaseTool):
    name: str = "api_client"
    description: str = (
        "Make HTTP requests to a configured REST API. "
        "Supports GET, POST, PUT, DELETE methods."
    )
    args_schema: Type[BaseModel] = APIRequestInput

    api_key: str = Field(..., description="API key for authentication")
    base_url: str = Field(..., description="Base URL of the API, e.g. https://api.example.com")
    timeout: int = Field(default=30, description="Request timeout in seconds")

    def _run(
        self,
        endpoint: str,
        method: str = "GET",
        payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, str]] = None,
    ) -> dict:
        """Make authenticated request using self.api_key and self.base_url."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = self.base_url.rstrip("/") + "/" + endpoint.lstrip("/")

        try:
            resp = requests.request(
                method.upper(),
                url,
                headers=headers,
                json=payload,
                params=params,
                timeout=self.timeout,
            )
            return {
                "status_code": resp.status_code,
                "url": url,
                "method": method,
                "response": resp.json() if resp.content else {},
                "error": None,
            }
        except requests.JSONDecodeError:
            return {
                "status_code": resp.status_code,
                "url": url,
                "method": method,
                "response": resp.text,
                "error": None,
            }
        except Exception as e:
            return {
                "status_code": None,
                "url": url,
                "method": method,
                "response": None,
                "error": str(e),
            }

    async def _arun(self, **kwargs) -> dict:
        raise NotImplementedError
