from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal

from app.builder.schemas import CodeFile


class ConvertRequest(BaseModel):
    platform: Literal["langchain", "crewai"]
    content: str = Field(..., min_length=10, max_length=30_000)


class ToolParam(BaseModel):
    name: str
    type_hint: str  # "str", "int", "float", "bool", "Any"
    required: bool
    default_repr: str | None = None  # e.g. "5", "'hello'", "None"
    annotation_missing: bool = False
    description: str


class DetectedTool(BaseModel):
    name: str  # snake_case function name
    original_name: str  # original name from class/decorator
    description: str
    params: list[ToolParam]
    has_return_dict: bool


class ConversionConfidence(BaseModel):
    level: Literal["high", "medium", "low"]
    reasons: list[str]


class WarningItem(BaseModel):
    message: str
    category: Literal["blocking", "review", "info"]


class ConvertResponse(BaseModel):
    code_files: list[CodeFile]
    manifest_yaml: str
    manifest_json: dict
    confidence: ConversionConfidence
    draft_ready: bool
    requires_manual_review: bool
    warnings: list[str]  # flat list (backward compat)
    grouped_warnings: list[WarningItem]  # categorized: blocking / review / info
    changes: list[str]
    detected_dependencies: list[str]
    unknown_imports: list[str]
    detected_tools: list[DetectedTool]
    package_id: str
