"""Provider de inyección de dependencias para la tarifa eléctrica.

Fuente = CRMBackend (`RemoteTariffStore`, con degradación si no responde) —
no el JSON local, que desde la Fase 5 solo respalda `GET`/`PUT /tariff`.
Ver `app/services/tariff/store.py`.
"""

from fastapi import Request

from app.schemas.tariff import TariffConfig
from app.services.tariff.store import RemoteTariffStore


async def get_tariff_config(request: Request) -> TariffConfig:
    store: RemoteTariffStore = request.app.state.remote_tariff_store
    return await store.load()
