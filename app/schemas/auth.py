"""Modelos de autenticación."""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, examples=["admin"])
    password: str = Field(min_length=1, examples=["secret"])


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Vida del access token en segundos", examples=[1800])


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(
        default=None, description="Si se envía, también se revoca el refresh token"
    )


class UserInfo(BaseModel):
    username: str
