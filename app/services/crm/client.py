"""Cliente HTTP hacia CRMBackend — credencial de servicio (Fase 5).

CRMBackend expone `POST /api/v1/service/token`: intercambia
`client_id`/`client_secret` (emitidos desde su panel, `POST
/api/v1/service-accounts`, solo admin) por un token de corta duración con
permisos explícitos (acá: `tariffs:read`). Nada de esto es una cuenta de
usuario — no hay email/password, y el token no sirve para escribir nada
(`GET /api/v1/tariffs` acepta el token de servicio; POST/PATCH/DELETE
siguen cerrados a máquinas).
"""

import time
from typing import Any

import httpx

from app.core.config import Settings

_HTTP_OK = 200
_HTTP_UNAUTHORIZED = 401
# Renovar un poco antes de que expire de verdad — evita perder una llamada
# en vuelo justo en el borde del vencimiento.
_EXPIRY_SAFETY_MARGIN_SECONDS = 10


class CrmClientError(RuntimeError):
    """CRMBackend respondió con un error o el cliente no está configurado."""


class CrmClient:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._base_url = settings.CRM_BASE_URL.rstrip("/")
        self._client_id = settings.CRM_CLIENT_ID
        self._client_secret = settings.CRM_CLIENT_SECRET
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        # Inyectable en tests (httpx.MockTransport) — None en producción usa
        # el transporte HTTP real de httpx.
        self._transport = transport

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=10.0, transport=self._transport)

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._client_id and self._client_secret)

    def _token_is_fresh(self) -> bool:
        return self._token is not None and time.monotonic() < self._token_expires_at

    async def _login(self, client: httpx.AsyncClient) -> str:
        if not self.configured:
            raise CrmClientError("CRM_BASE_URL/CRM_CLIENT_ID/CRM_CLIENT_SECRET sin configurar")
        response = await client.post(
            f"{self._base_url}/api/v1/service/token",
            json={"client_id": self._client_id, "client_secret": self._client_secret},
        )
        if response.status_code != _HTTP_OK:
            raise CrmClientError(
                f"token de servicio CRMBackend falló: HTTP {response.status_code}"
            )
        body = response.json()
        token = body["access_token"]
        expires_in = body["expires_in"]
        self._token = token
        self._token_expires_at = time.monotonic() + expires_in - _EXPIRY_SAFETY_MARGIN_SECONDS
        return token

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        async with self._new_client() as client:
            token = self._token if self._token_is_fresh() else await self._login(client)
            response = await client.get(
                f"{self._base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == _HTTP_UNAUTHORIZED:
                # Solo 401 (token vencido/revocado del lado del CRM) — nunca
                # 403. Un 403 significa que la credencial es válida pero le
                # falta el permiso (ej. pidieron fleet:read sin tenerlo);
                # pedir un token nuevo no cambia eso, sería un reintento en
                # loop hacia el mismo resultado.
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
