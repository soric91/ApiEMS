"""Application settings loaded from environment / .env via pydantic-settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MIN_JWT_SECRET_LENGTH = 32


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

    # --- JWT ---
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE: int = Field(default=30, description="Access token TTL en minutos")
    JWT_REFRESH_EXPIRE: int = Field(default=10080, description="Refresh token TTL en minutos")

    # --- API auth (usuario único) ---
    API_USERNAME: str = ""
    API_PASSWORD: str = ""

    # --- CORS ---
    CORS_ORIGINS: str = "http://localhost:4321"

    # --- Zona horaria para límites de periodo (día/semana/mes/año) ---
    # Los timestamps expuestos por la API siempre son UTC; esto solo decide
    # qué rango de datos corresponde a "hoy", "esta semana", etc.
    TIMEZONE: str = "America/Bogota"

    # --- Tarifa eléctrica (costos en COP) ---
    # Archivo editable en caliente (no .env): la tarifa cambia mes a mes y no
    # tiene sentido reiniciar el contenedor solo para actualizar un número.
    TARIFF_CONFIG_PATH: str = "data/tariffs.json"

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
            if len(self.JWT_SECRET) < _MIN_JWT_SECRET_LENGTH:
                raise ValueError("JWT_SECRET debe tener al menos 32 caracteres en producción")
            if not self.API_PASSWORD or self.API_PASSWORD == "changeme":
                raise ValueError("API_PASSWORD debe definirse (y no ser 'changeme') en producción")
            # Solo si se configuró CRM_BASE_URL: un secreto vacío o de ejemplo
            # se detecta al arrancar, no en la primera petición de costos.
            if self.CRM_BASE_URL and not self.CRM_CLIENT_SECRET.startswith("svcsec_"):
                raise ValueError(
                    "CRM_CLIENT_SECRET debe ser una credencial de servicio real "
                    "('svcsec_...') cuando CRM_BASE_URL está configurado en producción"
                )
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
