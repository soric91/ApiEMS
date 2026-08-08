"""Application factory de ApiEMS."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.crm_identity import CrmIdentityVerifier
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestTimingMiddleware
from app.repositories.influx import InfluxRepository
from app.schemas.mqtt import DeviceReading
from app.services.alerts.detector import check_hourly
from app.services.alerts.state import AlertsState
from app.services.crm.client import CrmClient
from app.services.crm.fleet import FleetDirectory
from app.services.influx.client import InfluxService
from app.services.mqtt.client import MQTTService
from app.services.realtime.state import RealtimeState
from app.services.tariff.store import RemoteTariffStore
from app.services.websocket.manager import ConnectionManager
from app.websocket.routes import router as websocket_router

OPENAPI_TAGS = [
    {"name": "Health", "description": "Estado del servicio"},
    {"name": "Auth", "description": "Autenticación JWT (access + refresh, usuario único)"},
    {
        "name": "Dashboard",
        "description": "Panel principal: instantáneas (RAM) + energía del periodo (InfluxDB)",
    },
    {
        "name": "Realtime",
        "description": "Tiempo real desde memoria (RAM), alimentado por MQTT. "
        "No consulta InfluxDB.",
    },
    {
        "name": "History",
        "description": "Series y resúmenes históricos consultados directo a InfluxDB",
    },
    {
        "name": "Consumption",
        "description": "Energía importada de la red (difference() sobre POWER_ACTIVE_TOTAL_POS)",
    },
    {
        "name": "Export",
        "description": "Excedente solar exportado a la red "
        "(difference() sobre POWER_ACTIVE_TOTAL_NEG)",
    },
    {
        "name": "Analytics",
        "description": "Perfiles, demanda pico, factor de carga, carga base y comparación "
        "de periodos (Polars)",
    },
    {
        "name": "KPIs",
        "description": "Indicadores clave del periodo: potencia/voltaje/corriente/factor "
        "de potencia + energía (Polars)",
    },
    {
        "name": "Reports",
        "description": "Payload listo para PDF/Excel: KPIs + analytics + energía del periodo",
    },
    {
        "name": "Alerts",
        "description": "Alertas de consumo anómalo por bandas de percentiles (sin ML)",
    },
    {
        "name": "Tariff",
        "description": "Configuración de tarifa eléctrica (COP/kWh) — editable, "
        "persistida en archivo",
    },
    {
        "name": "Costs",
        "description": "Costo/crédito en COP derivado de energía + tarifa configurada",
    },
]


def _make_mqtt_handler(
    state: RealtimeState,
    ws_manager: ConnectionManager,
    alerts_state: AlertsState,
    influx_repo: InfluxRepository,
    settings: Settings,
) -> Callable[[DeviceReading], Awaitable[None]]:
    async def handler(reading: DeviceReading) -> None:
        state.update(reading)
        await ws_manager.broadcast(reading)

        # Las alertas consultan InfluxDB, así que dependen de un servicio que
        # el relay no necesita para nada. Si esa consulta falla, el panel tiene
        # que seguir viendo su lectura en vivo: lo que se pierde es la alerta,
        # no el dato. Va después del broadcast y con su propio `except` por eso.
        try:
            alert = await check_hourly(influx_repo, reading, settings)
        except Exception as exc:
            get_logger("apiems.alerts").warning("alerta_no_evaluada", error=str(exc))
            return
        if alert is not None and alerts_state.add_if_due(alert):
            await ws_manager.broadcast_alert(alert)

    return handler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = get_settings()
    logger = get_logger("apiems")
    logger.info("startup", app=settings.APP_NAME, environment=settings.ENVIRONMENT)

    # Construcción, no conexión: CrmClient no habla con la red hasta el
    # primer get_tariffs()/get_fleet() — el arranque no se bloquea esperando
    # a CRMBackend, y si está caído, RemoteTariffStore degrada en vez de
    # tumbar el proceso (ver app/services/tariff/store.py).
    app.state.crm_client = CrmClient(settings)
    app.state.remote_tariff_store = RemoteTariffStore(app.state.crm_client)
    # La identidad la emite el CRM; acá solo se verifica con su clave pública.
    # PyJWKClient pide el JWKS de forma perezosa, así que esto tampoco toca la
    # red al arrancar.
    app.state.identity_verifier = CrmIdentityVerifier(settings)
    app.state.fleet_directory = FleetDirectory(settings, app.state.crm_client)

    influx = InfluxService(settings)
    await influx.connect()
    app.state.influx = influx
    app.state.influx_repo = InfluxRepository(
        influx.query_api, settings.INFLUX_BUCKET, settings.TIMEZONE
    )

    realtime_state = RealtimeState()
    ws_manager = ConnectionManager()
    alerts_state = AlertsState()
    app.state.realtime_state = realtime_state
    app.state.ws_manager = ws_manager
    app.state.alerts_state = alerts_state

    mqtt_handler = _make_mqtt_handler(
        realtime_state, ws_manager, alerts_state, app.state.influx_repo, settings
    )
    mqtt = MQTTService(settings, handler=mqtt_handler)
    app.state.mqtt = mqtt
    if settings.ENVIRONMENT != "testing":
        await mqtt.start()

    yield

    await mqtt.stop()
    await influx.close()
    logger.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        description=(
            "Backend EMS. Mide el balance neto en la frontera con la red: "
            "energía importada (`TotWh_import`) y exportada "
            "(`TotWh_export`). No mide generación solar ni autoconsumo."
        ),
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(RequestTimingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(websocket_router)
    return app


app = create_app()
