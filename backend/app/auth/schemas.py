import re
from uuid import UUID

from pydantic import BaseModel, EmailStr, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        v = v.lower()
        if not re.match(r"^[a-z0-9_-]{3,30}$", v):
            raise ValueError("Username must be 3-30 chars, only lowercase letters, digits, hyphens, underscores")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class RegisterResponse(BaseModel):
    id: UUID
    email: str
    username: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    totp_code: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str


class CreateApiKeyRequest(BaseModel):
    label: str | None = None


class ApiKeyResponse(BaseModel):
    id: UUID
    key: str
    key_prefix: str
    label: str | None


class MeResponse(BaseModel):
    id: UUID
    email: str
    username: str
    publisher: dict | None
    two_factor_enabled: bool


class Setup2FAResponse(BaseModel):
    secret: str
    qr_uri: str


class Verify2FARequest(BaseModel):
    totp_code: str


class Verify2FAResponse(BaseModel):
    two_factor_enabled: bool
