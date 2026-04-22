from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, field_validator, model_validator


class BuilderGenerateRequest(BaseModel):
    description: str = Field(..., min_length=10, max_length=1000)
    package_type: str = Field(default="toolpack", pattern=r'^(toolpack|agent)$')


_SAFE_PATH_RE = re.compile(r'^[a-zA-Z0-9/_.\-]+$')


class CodeFile(BaseModel):
    path: str = Field(..., max_length=300)
    content: str = Field(..., max_length=500_000)

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        if "\x00" in v:
            raise ValueError("Path must not contain null bytes")
        if ".." in v:
            raise ValueError("Path must not contain '..'")
        if v.startswith("/") or v.startswith("\\"):
            raise ValueError("Path must be relative, not absolute")
        # Reject Windows drive letters like C: or D:
        if len(v) >= 2 and v[1] == ":" and v[0].isalpha():
            raise ValueError("Path must be relative, not absolute")
        if v.startswith("."):
            raise ValueError("Path must not start with '.' (no hidden files)")
        if not _SAFE_PATH_RE.match(v):
            raise ValueError(
                "Path contains invalid characters; only alphanumeric, '/', '.', '_', '-' are allowed"
            )
        return v


class BuilderMetadata(BaseModel):
    package_id: str
    package_name: str
    tool_count: int
    detected_capability_ids: list[str]
    detected_framework: str
    publish_ready: bool
    warnings: list[str]


class BuilderArtifactRequest(BaseModel):
    package_id: str = Field(
        ...,
        max_length=60,
        pattern=r'^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]$',
    )
    manifest_json: dict
    code_files: list[CodeFile] = Field(..., max_length=50)

    @model_validator(mode="after")
    def validate_manifest_size(self) -> "BuilderArtifactRequest":
        if len(json.dumps(self.manifest_json)) > 200_000:
            raise ValueError("manifest_json is too large (max 200 KB serialized)")
        return self


class BuilderGenerateResponse(BaseModel):
    manifest_yaml: str
    manifest_json: dict
    code_files: list[CodeFile]
    metadata: BuilderMetadata
