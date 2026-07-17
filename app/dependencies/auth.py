"""Dependency de autenticación para proteger rutas."""

from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.core.security import InvalidTokenError, TokenBlacklist, TokenPayload, decode_token

_bearer = HTTPBearer(auto_error=False, description="Access token JWT")


def get_blacklist(request: Request) -> TokenBlacklist:
    return cast(TokenBlacklist, request.app.state.token_blacklist)


def _unauthorized(reason: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=reason,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
    blacklist: Annotated[TokenBlacklist, Depends(get_blacklist)],
) -> TokenPayload:
    if credentials is None:
        raise _unauthorized("Credenciales no proporcionadas")
    try:
        payload = decode_token(settings, credentials.credentials, expected_type="access")
    except InvalidTokenError as exc:
        raise _unauthorized(exc.reason) from exc
    if blacklist.is_revoked(payload.jti):
        raise _unauthorized("Token revocado")
    return payload


def get_current_user(
    payload: Annotated[TokenPayload, Depends(get_current_token)],
) -> str:
    return payload.subject


CurrentToken = Annotated[TokenPayload, Depends(get_current_token)]
CurrentUser = Annotated[str, Depends(get_current_user)]
