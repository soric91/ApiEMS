"""La tarifa, siempre desde CRMBackend.

Antes convivían dos fuentes: un JSON local editable por `PUT /tariff` y el
CRM. Eran dos verdades para el mismo número, y los costos ya se calculaban
con la del CRM — editar la local no cambiaba nada, que es la peor forma de
tener una opción. La local se eliminó; el CRM es el único dueño del precio.
"""

from app.core.logging import get_logger
from app.schemas.tariff import TariffConfig
from app.services.crm.client import CrmClient, CrmClientError
from app.services.tariff.crm_adapter import adapt_crm_tariffs

logger = get_logger("apiems.tariff")

_EMPTY = TariffConfig()


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
