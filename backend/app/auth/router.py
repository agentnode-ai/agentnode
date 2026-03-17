from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.shared.rate_limit import check_login_rate_limits, rate_limit
from app.auth.schemas import (
    ApiKeyListResponse,
    ApiKeyResponse,
    ChangePasswordRequest,
    ChangePasswordResponse,
    CreateApiKeyRequest,
    LoginRequest,
    LogoutResponse,
    MeResponse,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    RegisterResponse,
    RequestEmailVerificationResponse,
    RequestPasswordResetRequest,
    RequestPasswordResetResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
    Setup2FAResponse,
    TokenResponse,
    UpdateProfileRequest,
    UpdateProfileResponse,
    Verify2FARequest,
    Verify2FAResponse,
    VerifyEmailRequest,
    VerifyEmailResponse,
)
from app.auth.security import (
    clear_auth_cookies,
    create_access_token,
    create_refresh_token,
    decode_token,
    revoke_refresh_jti,
    set_auth_cookies,
    store_refresh_token,
    validate_refresh_jti,
)
from app.auth.service import (
    change_password,
    create_api_key_for_user,
    get_user_with_publisher,
    list_api_keys_for_user,
    login_user,
    register_user,
    request_email_verification,
    request_password_reset,
    reset_password,
    revoke_api_key_for_user,
    setup_2fa,
    update_profile,
    verify_2fa,
    verify_email,
)
from app.database import get_session
from app.shared.exceptions import AppError

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResponse, status_code=201, dependencies=[Depends(rate_limit(10, 60))])
async def register(body: RegisterRequest, session: AsyncSession = Depends(get_session)):
    user = await register_user(session, body.email, body.username, body.password)
    return RegisterResponse(id=user.id, email=user.email, username=user.username)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    await check_login_rate_limits(request, response, body.email)
    redis = request.app.state.redis
    result = await login_user(session, body.email, body.password, body.totp_code, redis=redis)
    # Set httpOnly cookies for web clients
    set_auth_cookies(response, result["access_token"], result["refresh_token"])
    # Set non-httpOnly admin flag for frontend Navbar
    if result.get("is_admin"):
        from app.config import settings
        response.set_cookie(
            key="is_admin", value="1", httponly=False,
            secure=settings.COOKIE_SECURE, samesite=settings.COOKIE_SAMESITE,
            domain=settings.COOKIE_DOMAIN or None,
            max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400, path="/",
        )

    # New login alert (fire-and-forget)
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    ua = request.headers.get("user-agent", "unknown")
    from app.shared.email import send_new_login_alert_email
    await send_new_login_alert_email(body.email, ip, ua)

    return TokenResponse(**result)


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(body: RefreshRequest, request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    # Accept refresh token from body (CLI) or cookie (web)
    token = body.refresh_token or request.cookies.get("refresh_token")
    if not token:
        raise AppError("AUTH_INVALID_CREDENTIALS", "No refresh token provided", 401)

    try:
        payload = decode_token(token)
    except Exception:
        clear_auth_cookies(response)
        raise AppError("AUTH_TOKEN_EXPIRED", "Refresh token is invalid or expired", 401)

    if payload.get("token_type") != "refresh":
        raise AppError("AUTH_INVALID_CREDENTIALS", "Expected refresh token", 401)

    user_id = payload.get("sub")
    if not user_id:
        raise AppError("AUTH_INVALID_CREDENTIALS", "Invalid token payload", 401)

    redis = request.app.state.redis
    old_jti = payload.get("jti")

    # Validate JTI if present (new tokens have it, legacy tokens don't)
    if old_jti:
        valid = await validate_refresh_jti(redis, old_jti)
        if not valid:
            clear_auth_cookies(response)
            raise AppError("AUTH_TOKEN_EXPIRED", "Refresh token has been revoked", 401)
        # Revoke old token
        await revoke_refresh_jti(redis, old_jti)

    # Issue new tokens (rotation)
    new_access = create_access_token(user_id)
    new_refresh, new_jti = create_refresh_token(user_id)
    await store_refresh_token(redis, user_id, new_jti)

    # Set new cookies for web clients
    set_auth_cookies(response, new_access, new_refresh)

    return RefreshResponse(access_token=new_access, refresh_token=new_refresh)


@router.post("/logout", response_model=LogoutResponse)
async def logout(request: Request, response: Response):
    """Clear auth cookies and revoke refresh token."""
    redis = request.app.state.redis
    refresh = request.cookies.get("refresh_token")
    if refresh:
        try:
            payload = decode_token(refresh)
            jti = payload.get("jti")
            if jti:
                await revoke_refresh_jti(redis, jti)
        except Exception:
            pass  # Token already expired/invalid — just clear cookies
    clear_auth_cookies(response)
    return LogoutResponse(message="Logged out successfully.")


@router.get("/api-keys", response_model=ApiKeyListResponse)
async def list_api_keys(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    keys = await list_api_keys_for_user(session, user.id)
    return ApiKeyListResponse(keys=keys)


@router.post("/api-keys", response_model=ApiKeyResponse, status_code=201)
async def create_api_key(
    body: CreateApiKeyRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await create_api_key_for_user(session, user.id, body.label)
    return ApiKeyResponse(**result)


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    from uuid import UUID as _UUID
    await revoke_api_key_for_user(session, user.id, _UUID(key_id))
    return Response(status_code=204)


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    user = await get_user_with_publisher(session, user.id)
    publisher_data = None
    if user.publisher:
        publisher_data = {
            "id": str(user.publisher.id),
            "slug": user.publisher.slug,
            "display_name": user.publisher.display_name,
            "trust_level": user.publisher.trust_level,
        }
    return MeResponse(
        id=user.id,
        email=user.email,
        username=user.username,
        publisher=publisher_data,
        two_factor_enabled=user.two_factor_enabled,
        is_admin=user.is_admin,
    )


@router.post("/2fa/setup", response_model=Setup2FAResponse)
async def setup_2fa_route(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await setup_2fa(session, user.id)
    return Setup2FAResponse(**result)


@router.post("/2fa/verify", response_model=Verify2FAResponse)
async def verify_2fa_route(
    body: Verify2FARequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await verify_2fa(session, user.id, body.code)
    return Verify2FAResponse(**result)


# --- Sprint 2: Account management ---


@router.post("/change-password", response_model=ChangePasswordResponse)
async def change_password_route(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await change_password(session, user.id, body.current_password, body.new_password)
    return ChangePasswordResponse(**result)


@router.post("/request-password-reset", response_model=RequestPasswordResetResponse)
async def request_password_reset_route(
    body: RequestPasswordResetRequest,
    session: AsyncSession = Depends(get_session),
):
    result = await request_password_reset(session, body.email)
    return RequestPasswordResetResponse(**result)


@router.post("/reset-password", response_model=ResetPasswordResponse)
async def reset_password_route(
    body: ResetPasswordRequest,
    session: AsyncSession = Depends(get_session),
):
    result = await reset_password(session, body.token, body.new_password)
    return ResetPasswordResponse(**result)


@router.post("/email/request-verification", response_model=RequestEmailVerificationResponse)
async def request_email_verification_route(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await request_email_verification(session, user.id)
    return RequestEmailVerificationResponse(**result)


@router.post("/email/verify", response_model=VerifyEmailResponse)
async def verify_email_route(
    body: VerifyEmailRequest,
    session: AsyncSession = Depends(get_session),
):
    result = await verify_email(session, body.token)
    return VerifyEmailResponse(**result)


# --- Email Preferences ---

from app.shared.email import EMAIL_PREF_DEFAULTS


@router.get("/email-preferences")
async def get_email_preferences(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    user = await get_user_with_publisher(session, user.id)
    prefs = dict(EMAIL_PREF_DEFAULTS)
    prefs.update(user.email_preferences or {})
    return {"preferences": prefs}


@router.put("/email-preferences")
async def update_email_preferences(
    body: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    user = await get_user_with_publisher(session, user.id)
    current = dict(user.email_preferences or {})
    # Only allow known keys with boolean values
    for key, value in body.items():
        if key in EMAIL_PREF_DEFAULTS and isinstance(value, bool):
            current[key] = value
    user.email_preferences = current
    await session.commit()
    prefs = dict(EMAIL_PREF_DEFAULTS)
    prefs.update(current)
    return {"preferences": prefs}


@router.put("/profile", response_model=UpdateProfileResponse)
async def update_profile_route(
    body: UpdateProfileRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await update_profile(session, user.id, body.username, body.email)
    return UpdateProfileResponse(**result)
