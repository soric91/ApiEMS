"""JWT y verificación de credenciales.

Un solo usuario (credenciales en .env). Tokens firmados con HS256:
- access: vida corta (JWT_EXPIRE min), autoriza las rutas protegidas.
- refresh: vida larga (JWT_REFRESH_EXPIRE min), solo sirve para rotar tokens.

Logout con JWT stateless = revocación por jti en una blacklist en memoria
(suficiente sin base de datos y con un único usuario; se pierde al reiniciar,
lo que solo obliga a re-login).
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt

from app.core.config import Settings

type TokenType = Literal["access", "refresh"]


class InvalidTokenError(Exception):
    """Token inválido, expirado, revocado o de tipo incorrecto."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TokenPayload:
    __slots__ = ("expires_at", "jti", "subject", "token_type")

    def __init__(self, subject: str, token_type: TokenType, jti: str, expires_at: datetime) -> None:
        self.subject = subject
        self.token_type = token_type
        self.jti = jti
        self.expires_at = expires_at


def verify_credentials(settings: Settings, username: str, password: str) -> bool:
    """Comparación en tiempo constante contra las credenciales de .env."""
    if not settings.API_USERNAME or not settings.API_PASSWORD:
        return False
    user_ok = secrets.compare_digest(username.encode(), settings.API_USERNAME.encode())
    pass_ok = secrets.compare_digest(password.encode(), settings.API_PASSWORD.encode())
    return user_ok and pass_ok


def create_token(settings: Settings, subject: str, token_type: TokenType) -> str:
    minutes = settings.JWT_EXPIRE if token_type == "access" else settings.JWT_REFRESH_EXPIRE
    now = datetime.now(tz=UTC)
    claims: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
    }
    return jwt.encode(claims, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)  # pyright: ignore[reportUnknownMemberType]


def decode_token(settings: Settings, token: str, expected_type: TokenType) -> TokenPayload:
    try:
        claims: dict[str, Any] = jwt.decode(  # pyright: ignore[reportUnknownMemberType]
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["sub", "type", "jti", "exp"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("Token expirado") from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError("Token inválido") from exc

    if claims["type"] != expected_type:
        raise InvalidTokenError(f"Se esperaba token de tipo '{expected_type}'")

    return TokenPayload(
        subject=str(claims["sub"]),
        token_type=expected_type,
        jti=str(claims["jti"]),
        expires_at=datetime.fromtimestamp(float(claims["exp"]), tz=UTC),
    )


class TokenBlacklist:
    """Revocación de tokens por jti, en memoria, con purga por expiración."""

    def __init__(self) -> None:
        self._revoked: dict[str, datetime] = {}

    def revoke(self, jti: str, expires_at: datetime) -> None:
        self._prune()
        self._revoked[jti] = expires_at

    def is_revoked(self, jti: str) -> bool:
        self._prune()
        return jti in self._revoked

    def _prune(self) -> None:
        now = datetime.now(tz=UTC)
        expired = [jti for jti, exp in self._revoked.items() if exp <= now]
        for jti in expired:
            del self._revoked[jti]
