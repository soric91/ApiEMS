"""Qué equipos puede ver un cliente, resuelto contra CRMBackend.

Las lecturas llegan tagueadas con `identify_device`, que es exactamente el
`id` que ese equipo tiene en el CRM — el script de adquisición lo toma de la
configuración que el CRM le sirve (`DeviceConfig.identify_device`). Eso hace
que la pregunta "¿esta lectura es de este cliente?" se conteste con una
pertenencia a conjunto, sin ninguna convención implícita en el medio.
"""

import time
from dataclasses import dataclass, replace
from typing import Any, cast

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.crm.client import CrmClient, CrmClientError

logger = get_logger("apiems.fleet")

# Una página basta de sobra: un cliente con más de 200 sedes no existe hoy, y
# si existiera preferimos enterarnos por este aviso antes que servir un
# recorte silencioso.
_PAGE_LIMIT = 200


@dataclass(frozen=True)
class FleetVariable:
    """Una medición dada de alta, con lo que hace falta para graficarla.

    Los metadatos no se calculan acá: vienen del catálogo de CRMBackend, que
    es el único lugar donde se decide qué significa `PhV_phsC`. ApiEMS los
    transporta. Duplicar la tabla acá sería la forma exacta de volver a tener
    dos nombres para la misma cosa.
    """

    nombre: str
    etiqueta: str
    unidad: str
    magnitud: str | None
    fase: str | None
    acumulativa: bool
    # En qué equipos está cargada. La misma medición suele estar en varios.
    equipos: frozenset[str]


@dataclass(frozen=True)
class ClientFleet:
    """Los equipos de un cliente, y cómo se llaman."""

    client_id: str
    # Los `identify_device` que este cliente tiene permitido ver.
    device_ids: frozenset[str]
    # id -> nombre del dispositivo, para no tener que pedir el árbol de nuevo
    # solo para mostrar una etiqueta.
    device_names: dict[str, str]
    # Las mediciones dadas de alta en esos equipos, sin repetir.
    variables: tuple[FleetVariable, ...]
    puede_ver_consumo: bool


def _children(node: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Los hijos de un nodo del árbol, tolerando `null` y formas inesperadas.

    El CRM devuelve `null` — no `[]` — para un nivel que no se pidió, y eso es
    información, no un error: significa "no lo preguntaste". Cualquier otra
    forma rara se trata como vacía en vez de tumbar la petición.
    """
    value: object = node.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in cast(list[object], value) if isinstance(item, dict)]


def _walk_devices(
    payload: dict[str, Any],
) -> tuple[dict[str, str], dict[str, FleetVariable], bool]:
    """Aplana el árbol a equipos, variables y el permiso del cliente.

    Las variables se juntan por nombre a través de todos los equipos: dos
    medidores que miden tensión de fase A son una sola entrada con dos
    equipos, no dos entradas iguales.
    """
    devices: dict[str, str] = {}
    variables: dict[str, FleetVariable] = {}
    puede_ver = False
    for client in _children(payload, "items"):
        puede_ver = bool(client.get("puede_ver_consumo"))
        for site in _children(client, "sites"):
            for gateway in _children(site, "gateways"):
                for equipment in _children(gateway, "equipment"):
                    device_id = str(equipment.get("id", ""))
                    if not device_id:
                        continue
                    name = equipment.get("nombre_dispositivo")
                    devices[device_id] = str(name) if name else device_id
                    for variable in _children(equipment, "variables"):
                        _acumular(variables, variable, device_id)
    return devices, variables, puede_ver


def _acumular(
    destino: dict[str, FleetVariable], crudo: dict[str, Any], device_id: str
) -> None:
    """Suma una variable del árbol al conjunto, o le agrega un equipo más."""
    nombre = str(crudo.get("nombre", ""))
    if not nombre:
        return

    existente = destino.get(nombre)
    if existente is not None:
        destino[nombre] = replace(
            existente, equipos=existente.equipos | {device_id}
        )
        return

    etiqueta = crudo.get("etiqueta")
    destino[nombre] = FleetVariable(
        nombre=nombre,
        # Sin etiqueta —una variable anterior al catálogo— se muestra el
        # nombre crudo. Feo, pero honesto: la alternativa es no mostrarla.
        etiqueta=str(etiqueta) if etiqueta else nombre,
        unidad=str(crudo.get("unidad") or ""),
        magnitud=_texto(crudo.get("magnitud")),
        fase=_texto(crudo.get("fase")),
        acumulativa=bool(crudo.get("acumulativa")),
        equipos=frozenset({device_id}),
    )


def _texto(valor: object) -> str | None:
    return str(valor) if valor is not None else None


class FleetDirectory:
    """Resuelve y cachea la flota de cada cliente.

    Cacheado por tiempo además del ETag del cliente HTTP: aun cuando el CRM
    conteste 304, aplanar el árbol en cada request sería trabajo repetido para
    una respuesta que casi nunca cambia.
    """

    def __init__(self, settings: Settings, crm: CrmClient) -> None:
        self._settings = settings
        self._crm = crm
        self._cache: dict[str, tuple[float, ClientFleet]] = {}

    def invalidate(self, client_id: str | None = None) -> None:
        """Olvida lo cacheado. Sin argumento, todo."""
        if client_id is None:
            self._cache.clear()
        else:
            self._cache.pop(client_id, None)

    async def for_client(self, client_id: str) -> ClientFleet:
        """La flota de un cliente, del caché o del CRM."""
        cached = self._cache.get(client_id)
        if cached is not None and time.monotonic() < cached[0]:
            return cached[1]

        try:
            payload = await self._crm.get_fleet(
                nivel="variables", client_id=client_id, limit=_PAGE_LIMIT
            )
        except CrmClientError:
            # Servir lo viejo es mejor que negar el acceso a alguien que sí lo
            # tiene: los datos de consumo viven en InfluxDB y no dependen del
            # CRM. Sin nada cacheado no hay más remedio que fallar.
            if cached is not None:
                logger.warning("fleet_stale", client_id=client_id)
                return cached[1]
            raise

        if payload.get("total", 0) > 1:
            logger.warning(
                "fleet_unexpected_breadth",
                client_id=client_id,
                total=payload["total"],
            )

        devices, variables, puede_ver_consumo = _walk_devices(payload)
        fleet = ClientFleet(
            client_id=client_id,
            device_ids=frozenset(devices),
            device_names=devices,
            variables=tuple(sorted(variables.values(), key=lambda v: v.nombre)),
            puede_ver_consumo=puede_ver_consumo,
        )
        self._cache[client_id] = (
            time.monotonic() + self._settings.CRM_FLEET_CACHE_SECONDS,
            fleet,
        )
        logger.info(
            "fleet_resolved", client_id=client_id, devices=len(fleet.device_ids),
            variables=len(fleet.variables),
        )
        return fleet
