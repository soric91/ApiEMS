"""En qué modo se lee el medidor de frontera de una sede.

Dos escenarios distintos con el MISMO hardware:

- `consumo` — sin generación propia. Todo lo que pasa por el medidor es
  consumo de la instalación: los indicadores de carga valen las 24 h y la
  curva que se ve es la de la casa/planta.
- `generacion` — con fotovoltaica inyectando. El medidor solo ve el BALANCE
  NETO en la acometida (no hay medición en el inversor), así que en horas de
  sol el consumo real queda escondido detrás de la generación y varios
  indicadores solo son válidos en ventana nocturna.

Quién lo decide, en orden:

1. Lo declarado en el CRM (`Site.tiene_generacion`). Es la verdad: alguien
   que conoce la instalación lo marcó.
2. Si nadie lo declaró (`None`), se detecta: una sede que exportó energía
   real en los últimos 30 días tiene generación. Sin declarar y sin
   exportación es `consumo`, que es el caso mayoritario de la flota.

La detección se cachea 24 h: una sede no cambia de modo entre dos cargas del
panel, y la consulta —un total de contador— no debería repetirse por vista.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal

from app.core.cache import cached
from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.analytics import SiteMode
from app.services.influx.cache import cached_energy_total

# Cuánto histórico mira la detección. Un mes cubre cualquier racha de días
# nublados sin arrastrar una instalación solar que se retiró hace meses.
DETECTION_DAYS = 30
# Por debajo de esto no hay generación: es ruido de medición o una exportación
# anecdótica de un día puntual, no un sistema inyectando.
DETECTION_THRESHOLD_KWH = 1.0
# Una sede no cambia de modo intradía.
MODE_TTL = 86400


@cached(ttl_seconds=MODE_TTL)
async def detect_site_mode(
    repo: InfluxDataSource,
    device_id: str | None,
    days: int = DETECTION_DAYS,
) -> SiteMode:
    """El modo deducido de la energía exportada en los últimos `days` días."""
    stop = datetime.now(tz=UTC)
    start = stop - timedelta(days=days)
    exported = await cached_energy_total(
        repo, Variable.POWER_ACTIVE_TOTAL_NEG, start, stop, device_id
    )
    return "generacion" if exported > DETECTION_THRESHOLD_KWH else "consumo"


async def resolve_site_mode(
    repo: InfluxDataSource,
    declarations: list[bool | None],
    device_id: str | None,
) -> tuple[SiteMode, Literal["crm", "detected"]]:
    """El modo y de dónde salió: lo declarado en el CRM manda, y si nadie lo
    declaró se detecta por la energía exportada.

    Vive acá y no en el endpoint porque lo necesitan varios cálculos —la carga
    base cambia de ventana según el modo, y el panel lo pregunta directo—: dos
    resoluciones distintas del mismo modo serían dos respuestas distintas para
    la misma sede.
    """
    declarado = declared_mode(declarations)
    if declarado is not None:
        return declarado, "crm"
    return await detect_site_mode(repo, device_id), "detected"


def declared_mode(declarations: list[bool | None]) -> SiteMode | None:
    """Lo declarado en el CRM, si TODAS las sedes consultadas coinciden.

    Sin declaraciones, o con sedes que se contradicen (un cliente con una
    planta solar y una bodega sin nada, consultado sin `device_id`), devuelve
    `None` y la decisión pasa a la detección sobre los datos reales, que ahí
    es más honesta que elegir una de las dos.
    """
    conocidas = {value for value in declarations if value is not None}
    if len(conocidas) != 1:
        return None
    return "generacion" if conocidas.pop() else "consumo"
