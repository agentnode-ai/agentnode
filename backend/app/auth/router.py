from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.shared.rate_limit import rate_limit
from app.auth.schemas import (
    ApiKeyResponse,
    CreateApiKeyRequest,
    LoginRequest,
    MeResponse,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    RegisterResponse,
    Setup2FAResponse,
    TokenResponse,
    Verify2FARequest,
    Verify2FAResponse,
)
from app.auth.security import create_access_token, decode_token
from app.auth.service import (
    create_api_key_for_user,
    get_user_with_publisher,
    login_user,
    register_user,
    setup_2fa,
    verify_2fa,
)
from app.database import get_session
from app.shared.exceptions import AppError

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResponse, status_code=201, dependencies=[Depends(rate_limit(10, 60))])
async def register(body: RegisterRequest, session: AsyncSession = Depends(get_session)):
    user = await register_user(session, body.email, body.username, body.password)
    return RegisterResponse(id=user.id, email=user.email, username=user.username)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(rate_limit(20, 60))])
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)):
    result = await login_user(session, body.email, body.password, body.totp_code)
    return TokenResponse(**result)


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(body: RefreshRequest, session: AsyncSession = Depends(get_session)):
    try:
        payload = decode_token(body.refresh_token)
    except Exception:
        raise AppError("AUTH_TOKEN_EXPIRED", "Refresh token is invalid or expired", 401)

    if payload.get("token_type") != "refresh":
        raise AppError("AUTH_INVALID_CREDENTIALS", "Expected refresh token", 401)

    user_id = payload.get("sub")
    if not user_id:
        raise AppError("AUTH_INVALID_CREDENTIALS", "Invalid token payload", 401)

    return RefreshResponse(access_token=create_access_token(user_id))


@router.post("/api-keys", response_model=ApiKeyResponse, status_code=201)
async def create_api_key(
    body: CreateApiKeyRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await create_api_key_for_user(session, user.id, body.label)
    return ApiKeyResponse(**result)


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
    result = await verify_2fa(session, user.id, body.totp_code)
    return Verify2FAResponse(**result)
