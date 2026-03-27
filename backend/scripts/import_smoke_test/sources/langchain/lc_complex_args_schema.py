"""
BaseTool with a complex Pydantic args_schema model including nested models and validators.
Pattern from production-grade LangChain tools.
"""

from typing import Dict, List, Literal, Optional, Type

import requests
from langchain.tools import BaseTool
from pydantic import BaseModel, Field, field_validator, model_validator


class FilterCriteria(BaseModel):
    field: str = Field(..., description="Field name to filter on")
    operator: Literal["eq", "neq", "gt", "lt", "gte", "lte", "contains", "in"] = Field(
        ..., description="Comparison operator"
    )
    value: str = Field(..., description="Value to compare against (as string)")


class SortCriteria(BaseModel):
    field: str = Field(..., description="Field to sort by")
    direction: Literal["asc", "desc"] = Field(default="asc", description="Sort direction")


class DataQueryInput(BaseModel):
    dataset: str = Field(..., description="Dataset identifier to query")
    filters: List[FilterCriteria] = Field(
        default_factory=list, description="List of filter conditions (AND logic)"
    )
    sort: Optional[SortCriteria] = Field(default=None, description="Sort specification")
    fields: Optional[List[str]] = Field(
        default=None, description="Specific fields to return (None = all)"
    )
    limit: int = Field(default=50, ge=1, le=1000, description="Max results to return")
    offset: int = Field(default=0, ge=0, description="Number of results to skip")
    include_total: bool = Field(
        default=True, description="Whether to include total count in response"
    )

    @field_validator("dataset")
    @classmethod
    def dataset_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("dataset cannot be empty")
        return v.strip()

    @model_validator(mode="after")
    def validate_pagination(self) -> "DataQueryInput":
        if self.offset > 10_000:
            raise ValueError("offset cannot exceed 10,000")
        return self


class DataQueryTool(BaseTool):
    name: str = "data_query"
    description: str = (
        "Query a structured dataset with filters, sorting, and pagination. "
        "Returns matching records and optionally the total count."
    )
    args_schema: Type[BaseModel] = DataQueryInput
    api_base_url: str = "https://api.example.com/data"
    api_token: str = ""

    def _run(
        self,
        dataset: str,
        filters: List[FilterCriteria] = None,
        sort: Optional[SortCriteria] = None,
        fields: Optional[List[str]] = None,
        limit: int = 50,
        offset: int = 0,
        include_total: bool = True,
    ) -> dict:
        payload: Dict = {
            "dataset": dataset,
            "filters": [f.model_dump() for f in (filters or [])],
            "limit": limit,
            "offset": offset,
            "include_total": include_total,
        }
        if sort:
            payload["sort"] = sort.model_dump()
        if fields:
            payload["fields"] = fields

        headers = {"Authorization": f"Bearer {self.api_token}"}
        try:
            resp = requests.post(
                f"{self.api_base_url}/query",
                json=payload,
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "dataset": dataset,
                "records": data.get("records", []),
                "total": data.get("total"),
                "limit": limit,
                "offset": offset,
                "error": None,
            }
        except Exception as e:
            return {"dataset": dataset, "records": [], "total": None, "error": str(e)}

    async def _arun(self, **kwargs) -> dict:
        raise NotImplementedError
