from __future__ import annotations

from pydantic import BaseModel, Field


class BuilderGenerateRequest(BaseModel):
    description: str = Field(..., min_length=10, max_length=1000)


class CodeFile(BaseModel):
    path: str
    content: str


class BuilderMetadata(BaseModel):
    package_id: str
    package_name: str
    tool_count: int
    detected_capability_ids: list[str]
    detected_framework: str
    publish_ready: bool
    warnings: list[str]


class BuilderArtifactRequest(BaseModel):
    package_id: str
    manifest_json: dict
    code_files: list[CodeFile]


class BuilderGenerateResponse(BaseModel):
    manifest_yaml: str
    manifest_json: dict
    code_files: list[CodeFile]
    metadata: BuilderMetadata
