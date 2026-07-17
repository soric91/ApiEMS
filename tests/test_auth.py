from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.security import TokenBlacklist, verify_credentials

LOGIN = {"username": "testuser", "password": "testpass"}


def do_login(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/v1/auth/login", json=LOGIN)
    assert response.status_code == 200
    return response.json()["data"]


def auth_header(tokens: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_login_ok(client: TestClient) -> None:
    data = do_login(client)
    assert data["token_type"] == "bearer"
    assert data["expires_in"] > 0
    assert data["access_token"] != data["refresh_token"]


def test_login_wrong_password(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"username": "testuser", "password": "wrong"})
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False


def test_me_requires_token(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_with_token(client: TestClient) -> None:
    tokens = do_login(client)
    response = client.get("/api/v1/auth/me", headers=auth_header(tokens))
    assert response.status_code == 200
    assert response.json()["data"]["username"] == "testuser"


def test_refresh_rotates_and_revokes_old(client: TestClient) -> None:
    tokens = do_login(client)
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 200
    new_tokens = response.json()["data"]
    assert new_tokens["access_token"] != tokens["access_token"]

    # Reuso del refresh viejo -> revocado
    reuse = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reuse.status_code == 401

    # El par nuevo funciona
    assert client.get("/api/v1/auth/me", headers=auth_header(new_tokens)).status_code == 200


def test_refresh_rejects_access_token(client: TestClient) -> None:
    tokens = do_login(client)
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert response.status_code == 401


def test_logout_revokes_tokens(client: TestClient) -> None:
    tokens = do_login(client)
    response = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers=auth_header(tokens),
    )
    assert response.status_code == 200

    assert client.get("/api/v1/auth/me", headers=auth_header(tokens)).status_code == 401
    reuse = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reuse.status_code == 401


def test_logout_with_invalid_refresh_token_still_succeeds(client: TestClient) -> None:
    """Un refresh_token basura en logout no debe romper la revocación del access."""
    tokens = do_login(client)
    response = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": "not-a-real-jwt"},
        headers=auth_header(tokens),
    )
    assert response.status_code == 200
    assert client.get("/api/v1/auth/me", headers=auth_header(tokens)).status_code == 401


def test_expired_token_rejected(client: TestClient) -> None:
    settings = get_settings()
    now = datetime.now(tz=UTC)
    expired = jwt.encode(
        {
            "sub": "testuser",
            "type": "access",
            "jti": "x" * 32,
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
        },
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401


def test_tampered_token_rejected(client: TestClient) -> None:
    tokens = do_login(client)
    tampered = tokens["access_token"][:-4] + "abcd"
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tampered}"})
    assert response.status_code == 401


def test_verify_credentials_constant_time_paths() -> None:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        API_USERNAME="u",
        API_PASSWORD="p",
    )
    assert verify_credentials(settings, "u", "p")
    assert not verify_credentials(settings, "u", "x")
    assert not verify_credentials(settings, "x", "p")
    empty = Settings(_env_file=None, API_USERNAME="", API_PASSWORD="")  # pyright: ignore[reportCallIssue]
    assert not verify_credentials(empty, "", "")


def test_token_blacklist_prunes_expired_entries() -> None:
    blacklist = TokenBlacklist()
    past = datetime.now(tz=UTC) - timedelta(seconds=1)
    blacklist.revoke("expired-jti", past)
    assert blacklist.is_revoked("expired-jti") is False  # ya purgado al consultar

    future = datetime.now(tz=UTC) + timedelta(minutes=5)
    blacklist.revoke("active-jti", future)
    assert blacklist.is_revoked("active-jti") is True


def test_production_requires_strong_secrets() -> None:
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            ENVIRONMENT="production",
            JWT_SECRET="short",
            API_PASSWORD="x",
        )
    with pytest.raises(ValueError, match="API_PASSWORD"):
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            ENVIRONMENT="production",
            JWT_SECRET="a" * 32,
            API_PASSWORD="changeme",
        )
