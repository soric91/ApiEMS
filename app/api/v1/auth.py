"""Endpoints de autenticación (usuario único definido en .env)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import Settings, get_settings
from app.core.security import (
    InvalidTokenError,
    TokenBlacklist,
    create_token,
    decode_token,
    verify_credentials,
)
from app.dependencies.auth import CurrentToken, get_blacklist
from app.schemas.auth import LoginRequest, LogoutRequest, RefreshRequest, TokenPair, UserInfo
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/auth", tags=["Auth"])

SettingsDep = Annotated[Settings, Depends(get_settings)]
BlacklistDep = Annotated[TokenBlacklist, Depends(get_blacklist)]


def _token_pair(settings: Settings, username: str) -> TokenPair:
    return TokenPair(
        access_token=create_token(settings, username, "access"),
        refresh_token=create_token(settings, username, "refresh"),
        expires_in=settings.JWT_EXPIRE * 60,
    )


@router.post(
    "/login",
    summary="Iniciar sesión",
    response_model=ApiResponse[TokenPair],
    responses={401: {"description": "Credenciales inválidas"}},
)
async def login(body: LoginRequest, settings: SettingsDep) -> ApiResponse[TokenPair]:
    """Valida usuario/contraseña (definidos en `.env`) y entrega el par de tokens.

    - `access_token`: enviar en `Authorization: Bearer <token>` en cada request.
    - `refresh_token`: usar solo en `/auth/refresh` para renovar el par.
    """
    if not verify_credentials(settings, body.username, body.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return ApiResponse(message="Login exitoso", data=_token_pair(settings, body.username))


@router.post(
    "/refresh",
    summary="Renovar tokens",
    response_model=ApiResponse[TokenPair],
    responses={401: {"description": "Refresh token inválido, expirado o revocado"}},
)
async def refresh(
    body: RefreshRequest, settings: SettingsDep, blacklist: BlacklistDep
) -> ApiResponse[TokenPair]:
    """Rota el par de tokens a partir de un refresh token válido.

    El refresh token usado queda revocado (rotación): un refresh token
    solo puede canjearse una vez.
    """
    try:
        payload = decode_token(settings, body.refresh_token, expected_type="refresh")
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=exc.reason,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if blacklist.is_revoked(payload.jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token revocado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    blacklist.revoke(payload.jti, payload.expires_at)
    return ApiResponse(message="Tokens renovados", data=_token_pair(settings, payload.subject))


@router.post(
    "/logout",
    summary="Cerrar sesión",
    response_model=ApiResponse[None],
    responses={401: {"description": "Access token inválido"}},
)
async def logout(
    body: LogoutRequest,
    token: CurrentToken,
    settings: SettingsDep,
    blacklist: BlacklistDep,
) -> ApiResponse[None]:
    """Revoca el access token actual y, si se envía, también el refresh token."""
    blacklist.revoke(token.jti, token.expires_at)
    if body.refresh_token:
        try:
            refresh_payload = decode_token(settings, body.refresh_token, expected_type="refresh")
        except InvalidTokenError:
            pass  # refresh ya inválido: nada que revocar
        else:
            blacklist.revoke(refresh_payload.jti, refresh_payload.expires_at)
    return ApiResponse(message="Sesión cerrada", data=None)


@router.get(
    "/me",
    summary="Usuario autenticado",
    response_model=ApiResponse[UserInfo],
    responses={401: {"description": "No autenticado"}},
)
async def me(token: CurrentToken) -> ApiResponse[UserInfo]:
    """Devuelve el usuario del token — útil para validar sesión desde el frontend."""
    return ApiResponse(message="OK", data=UserInfo(username=token.subject))
