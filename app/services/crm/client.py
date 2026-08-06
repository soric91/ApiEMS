"""Cliente HTTP hacia CRMBackend — credencial de servicio (Fase 5).

CRMBackend expone `POST /api/v1/service/token`: intercambia
`client_id`/`client_secret` (emitidos desde su panel, `POST
/api/v1/service-accounts`, solo admin) por un token de corta duración con
permisos explícitos (acá: `tariffs:read`, `fleet:read`). Nada de esto es una
cuenta de usuario — no hay email/password, y el token no sirve para escribir
nada (`GET /api/v1/tariffs` y `GET /api/v1/fleet` aceptan el token de
servicio; todo lo demás, incluido el detalle `/tariffs/{id}`, sigue cerrado
a máquinas con 401).
"""

import time
from typing import Any

import httpx

from app.core.config import Settings

_HTTP_OK = 200
_HTTP_NOT_MODIFIED = 304
_HTTP_UNAUTHORIZED = 401
# Renovar un poco antes de que expire de verdad — evita perder una llamada
# en vuelo justo en el borde del vencimiento.
_EXPIRY_SAFETY_MARGIN_SECONDS = 10


class CrmClientError(RuntimeError):
    """CRMBackend respondió con un error o el cliente no está configurado."""


# (nivel, client_id, search, limit, offset) -> (ETag, payload)
_FleetCacheKey = tuple[str, str | None, str, int, int]


class CrmClient:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._base_url = settings.CRM_BASE_URL.rstrip("/")
        self._client_id = settings.CRM_CLIENT_ID
        self._client_secret = settings.CRM_CLIENT_SECRET
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._fleet_cache: dict[_FleetCacheKey, tuple[str, dict[str, Any]]] = {}
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

    async def _authorized_get(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        token = self._token if self._token_is_fresh() else await self._login(client)
        headers = {"Authorization": f"Bearer {token}", **(extra_headers or {})}
        response = await client.get(f"{self._base_url}{path}", params=params, headers=headers)
        if response.status_code == _HTTP_UNAUTHORIZED:
            # Solo 401 (token vencido/revocado del lado del CRM) — nunca
            # 403. Un 403 significa que la credencial es válida pero le
            # falta el permiso (ej. pidieron fleet:read sin tenerlo);
            # pedir un token nuevo no cambia eso, sería un reintento en
            # loop hacia el mismo resultado.
            token = await self._login(client)
            headers["Authorization"] = f"Bearer {token}"
            response = await client.get(f"{self._base_url}{path}", params=params, headers=headers)
        return response

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        async with self._new_client() as client:
            response = await self._authorized_get(client, path, params)
            if response.status_code != _HTTP_OK:
                raise CrmClientError(f"GET {path} falló: HTTP {response.status_code}")
            return response.json()

    async def get_tariffs(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Todas las tarifas registradas, página única (≤200 meses de historia)."""
        page = await self._get("/api/v1/tariffs", params={"limit": limit})
        return page["items"]

    async def get_fleet(
        self,
        *,
        nivel: str = "variables",
        client_id: str | None = None,
        search: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Árbol completo: empresas → sedes → gateways → equipos → variables.

        Cacheado por ETag, por combinación exacta de parámetros — pedir
        `nivel=equipos` después de `nivel=variables` no devuelve el árbol
        equivocado porque cada combinación tiene su propia entrada. El ETag
        cambia cuando un gateway se calla (`estado` se deriva de
        `ultima_conexion`), así que esto no sirve como caché de larga
        duración: sirve para no volver a parsear el árbol si nada cambió.
        """
        cache_key: _FleetCacheKey = (nivel, client_id, search, limit, offset)
        cached = self._fleet_cache.get(cache_key)
        params: dict[str, Any] = {"nivel": nivel, "limit": limit, "offset": offset}
        if client_id is not None:
            params["client_id"] = client_id
        if search:
            params["search"] = search
        extra_headers = {"If-None-Match": cached[0]} if cached is not None else None

        async with self._new_client() as client:
            response = await self._authorized_get(
                client, "/api/v1/fleet", params, extra_headers
            )
            if response.status_code == _HTTP_NOT_MODIFIED:
                if cached is None:
                    raise CrmClientError(
                        "CRMBackend devolvió 304 sin que hubiera un ETag cacheado antes"
                    )
                return cached[1]
            if response.status_code != _HTTP_OK:
                raise CrmClientError(f"GET /api/v1/fleet falló: HTTP {response.status_code}")

            data = response.json()
            etag = response.headers.get("etag")
            if etag:
                self._fleet_cache[cache_key] = (etag, data)
            return data
