"""Dos fuentes de tarifa, para dos propósitos distintos.

`load_tariff_config`/`save_tariff_config` (JSON local, `TARIFF_CONFIG_PATH`):
siguen siendo lo único que respalda `GET`/`PUT /tariff` — la credencial de
servicio de ApiEMS contra CRMBackend es de solo lectura (`tariffs:read`), no
puede escribir tarifas allá. Editar una tarifa desde ApiEMS solo tiene efecto
en este archivo local.

`RemoteTariffStore` (CRMBackend, vía `CrmClient`): es lo que de verdad usan
`/costs`, `/reports` y `/analytics/summary` desde que se conectó la Fase 5.
**Esto significa que editar tarifas en `/tariff` ya no cambia los costos
calculados** — quedan desacopladas a propósito hasta decidir si `/tariff`
se deprecia o pasa a ser un proxy hacia CRMBackend.
"""

import asyncio
from pathlib import Path

from app.core.logging import get_logger
from app.schemas.tariff import TariffConfig
from app.services.crm.client import CrmClient, CrmClientError
from app.services.tariff.crm_adapter import adapt_crm_tariffs

logger = get_logger("apiems.tariff")

_EMPTY = TariffConfig()


def _read_if_exists(path: str) -> str | None:
    file = Path(path)
    return file.read_text() if file.exists() else None


async def load_tariff_config(path: str) -> TariffConfig:
    raw = await asyncio.to_thread(_read_if_exists, path)
    if raw is None:
        return _EMPTY
    return TariffConfig.model_validate_json(raw)


async def save_tariff_config(path: str, config: TariffConfig) -> None:
    file = Path(path)
    await asyncio.to_thread(file.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(file.write_text, config.model_dump_json(indent=2) + "\n")


class RemoteTariffStore:
    """Tarifa desde CRMBackend, con degradación si no responde.

    Guarda en memoria el último `TariffConfig` que se pudo traer con éxito.
    Si CRMBackend falla — caído, credencial revocada, lo que sea — y hay un
    valor cacheado, se sirve ese con un warning logueado: nunca un 500. Si
    nunca hubo uno (recién arrancó el proceso y CRM ya estaba caído), se
    sirve una tarifa vacía — el motor de costos (`rate_for_month` +
    `_totals_by_month` en `app/services/tariff/cost.py`) ya sabe tratar "sin
    tarifa" como costo 0 con el mes marcado `stale`, no hace falta inventar
    un concepto nuevo de "no disponible": el consumo en kWh sigue siendo
    correcto, solo el precio queda sin resolver, visible en `stale_months`.

    Una instancia por proceso (`app.state.remote_tariff_store`) — el caché
    en memoria no sirve de nada si se crea una nueva por request.
    """

    def __init__(self, crm: CrmClient) -> None:
        self._crm = crm
        self._last_good: TariffConfig | None = None

    async def load(self) -> TariffConfig:
        try:
            raw = await self._crm.get_tariffs()
        except CrmClientError as exc:
            if self._last_good is not None:
                logger.warning("crm_tariffs_unavailable_using_cache", error=str(exc))
                return self._last_good
            logger.warning("crm_tariffs_unavailable_no_cache", error=str(exc))
            return _EMPTY
        config = adapt_crm_tariffs(raw)
        self._last_good = config
        return config
