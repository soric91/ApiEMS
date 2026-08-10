"""Application settings loaded from environment / .env via pydantic-settings."""

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- App ---
    APP_NAME: str = "ApiEMS"
    ENVIRONMENT: Literal["development", "production", "testing"] = "development"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- InfluxDB ---
    INFLUX_URL: str = "http://localhost:8086"
    INFLUX_TOKEN: str = ""
    INFLUX_ORG: str = ""
    INFLUX_BUCKET: str = "modbus_data_v2"
    # Cuánto espera una consulta antes de rendirse, en milisegundos.
    #
    # El valor por defecto de la librería son 10 s, y alcanza para una consulta
    # sola. La pantalla de análisis dispara seis pesadas a la vez —dos períodos
    # de treinta días, el perfil mensual, los costos— y en un servidor chico se
    # encolan: cada una espera a las anteriores y todas cruzan el límite, hasta
    # las de metadatos que en sí son instantáneas.
    #
    # Subirlo no las hace rápidas; evita que una espera legítima se convierta en
    # un 500 que además llega sin cabecera CORS y se ve como un problema de
    # permisos en el navegador.
    INFLUX_TIMEOUT_MS: int = 60_000

    # --- MQTT ---
    MQTT_HOST: str = "localhost"
    MQTT_PORT: int = 1883
    MQTT_USER: str = ""
    MQTT_PASSWORD: str = ""
    # El puerto 1883 manda usuario y contraseña en claro. Por defecto en True
    # para que un broker nuevo se conecte cifrado sin que nadie se acuerde de
    # activarlo; apagarlo es una decisión explícita y solo tiene sentido en
    # un broker local que no sale de la máquina.
    MQTT_USE_TLS: bool = True
    MQTT_TOPIC: str = "gatewayems/modbus"
    MQTT_QOS: int = 1
    # Distinto al client_id del script de adquisición: IDs duplicados
    # provocan desconexiones mutuas en el broker.
    MQTT_CLIENT_ID: str = "apiems-backend"

    # --- Identidad ---
    # ApiEMS ya no emite tokens: los emite CRMBackend, que es quien sabe qué
    # clientes existen y qué puede ver cada uno. Acá solo se verifican, con la
    # clave pública que el CRM publica — sin secreto compartido, así que este
    # servicio nunca puede falsificar uno.
    #
    # Solo se acepta la audiencia `monitor`, la de la web de clientes. Un token
    # de operador del CRM no abre el panel de consumo de nadie.
    CRM_JWT_AUDIENCE: str = "monitor"
    # Las claves cambian solo al rotarlas; releerlas por request es puro gasto.
    CRM_JWKS_CACHE_SECONDS: int = 3600
    # El árbol de la flota cambia cuando alguien edita equipos en el CRM.
    # Corto porque un equipo nuevo debe aparecer sin reiniciar nada.
    CRM_FLEET_CACHE_SECONDS: int = 300

    # --- CORS ---
    CORS_ORIGINS: str = "http://localhost:4321"

    # --- Zona horaria para límites de periodo (día/semana/mes/año) ---
    # Los timestamps expuestos por la API siempre son UTC; esto solo decide
    # qué rango de datos corresponde a "hoy", "esta semana", etc.
    TIMEZONE: str = "America/Bogota"


    # --- CRMBackend (Fase 5, prompt_arquitectura_v2.md) ---
    # Credencial máquina-a-máquina real: POST /api/v1/service/token con
    # client_id/client_secret devuelve un token de servicio (permiso
    # tariffs:read), aceptado por GET /api/v1/tariffs. Emitida desde el panel
    # de CRMBackend (POST /api/v1/service-accounts, solo admin) — no es una
    # cuenta de usuario ni tiene email/password.
    CRM_BASE_URL: str = ""
    CRM_CLIENT_ID: str = ""
    CRM_CLIENT_SECRET: str = ""

    @model_validator(mode="after")
    def _require_strong_secrets_in_production(self) -> "Settings":
        if self.ENVIRONMENT == "production":
            # El CRM dejó de ser opcional: sin él no hay identidad ni tarifas,
            # así que un arranque sin configurar es un despliegue roto, no un
            # modo degradado.
            if not self.CRM_BASE_URL:
                raise ValueError("CRM_BASE_URL es obligatorio: el CRM emite la identidad")
            if not self.CRM_CLIENT_SECRET.startswith("svcsec_"):
                raise ValueError(
                    "CRM_CLIENT_SECRET debe ser una credencial de servicio real "
                    "('svcsec_...'). Se emite en el CRM: Servicios > Nueva credencial"
                )
        return self

    @property
    def crm_jwks_url(self) -> str:
        """Dónde publica el CRM la clave pública con la que firma."""
        return f"{self.CRM_BASE_URL.rstrip('/')}/.well-known/jwks.json"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
