import re
from uuid import UUID

from pydantic import BaseModel, EmailStr, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str
    password: str
    invite_code: str | None = None

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
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")
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
    refresh_token: str | None = None  # Optional: can come from httpOnly cookie


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None  # Returned for CLI clients


class LogoutResponse(BaseModel):
    message: str


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
    is_admin: bool = False


class Setup2FARequest(BaseModel):
    # P1-S4: reauth required to enrol 2FA. If an attacker gains access to
    # an open session they could otherwise enrol their own 2FA secret and
    # lock the real user out.
    current_password: str


class Setup2FAResponse(BaseModel):
    secret: str
    provisioning_uri: str


class Verify2FARequest(BaseModel):
    code: str


class Verify2FAResponse(BaseModel):
    two_factor_enabled: bool


class ApiKeyListItem(BaseModel):
    id: UUID
    key_prefix: str
    label: str | None
    created_at: str
    last_used_at: str | None


class ApiKeyListResponse(BaseModel):
    keys: list[ApiKeyListItem]


# --- Sprint 2: Account management ---


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")
        return v


class ChangePasswordResponse(BaseModel):
    message: str


class RequestPasswordResetRequest(BaseModel):
    email: EmailStr


class RequestPasswordResetResponse(BaseModel):
    message: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")
        return v


class ResetPasswordResponse(BaseModel):
    message: str


class RequestEmailVerificationResponse(BaseModel):
    message: str


class VerifyEmailRequest(BaseModel):
    token: str


class VerifyEmailResponse(BaseModel):
    email_verified: bool


class UpdateProfileRequest(BaseModel):
    username: str | None = None
    email: EmailStr | None = None
    # P1-S5: required when `email` is being changed. Ignored for username-only
    # updates. The service enforces the requirement; the field is optional at
    # the schema level so existing clients that only update username don't break.
    current_password: str | None = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.lower()
        if not re.match(r"^[a-z0-9_-]{3,30}$", v):
            raise ValueError("Username must be 3-30 chars, only lowercase letters, digits, hyphens, underscores")
        return v


class UpdateProfileResponse(BaseModel):
    id: UUID
    email: str
    username: str
    email_verified: bool
