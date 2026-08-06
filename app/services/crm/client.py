"""Cliente HTTP hacia CRMBackend — cuenta de servicio (Fase 5).

CRMBackend todavía no expone una credencial máquina-a-máquina (ver
`prompt_arquitectura_v2.md`, Fase 5, punto 1): el único login disponible es
el de un usuario real, con rol y audiencia de usuario. Este cliente hace
login con una cuenta dedicada (`CRM_SERVICE_EMAIL`/`CRM_SERVICE_PASSWORD`) y
cachea el access token en memoria hasta que el servidor lo rechace.
"""

from typing import Any

import httpx

from app.core.config import Settings

_HTTP_OK = 200
_HTTP_UNAUTHORIZED = 401


class CrmClientError(RuntimeError):
    """CRMBackend respondió con un error o el cliente no está configurado."""


class CrmClient:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._base_url = settings.CRM_BASE_URL.rstrip("/")
        self._email = settings.CRM_SERVICE_EMAIL
        self._password = settings.CRM_SERVICE_PASSWORD
        self._token: str | None = None
        # Inyectable en tests (httpx.MockTransport) — None en producción usa
        # el transporte HTTP real de httpx.
        self._transport = transport

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=10.0, transport=self._transport)

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._email and self._password)

    async def _login(self, client: httpx.AsyncClient) -> str:
        if not self.configured:
            raise CrmClientError(
                "CRM_BASE_URL/CRM_SERVICE_EMAIL/CRM_SERVICE_PASSWORD sin configurar"
            )
        response = await client.post(
            f"{self._base_url}/api/v1/auth/login",
            json={"email": self._email, "password": self._password},
        )
        if response.status_code != _HTTP_OK:
            raise CrmClientError(
                f"login contra CRMBackend falló: HTTP {response.status_code}"
            )
        token = response.json()["access_token"]
        self._token = token
        return token

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        async with self._new_client() as client:
            token = self._token or await self._login(client)
            response = await client.get(
                f"{self._base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == _HTTP_UNAUTHORIZED:
                token = await self._login(client)
                response = await client.get(
                    f"{self._base_url}{path}",
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
            if response.status_code != _HTTP_OK:
                raise CrmClientError(f"GET {path} falló: HTTP {response.status_code}")
            return response.json()

    async def get_tariffs(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Todas las tarifas registradas, página única (≤200 meses de historia)."""
        page = await self._get("/api/v1/tariffs", params={"limit": limit})
        return page["items"]
