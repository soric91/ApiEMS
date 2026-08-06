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
    # Cuenta de servicio: CRMBackend todavía no tiene credencial
    # máquina-a-máquina, solo login de usuario — se usa una cuenta con el rol
    # mínimo que alcance. app/services/crm/client.py y
    # app/services/tariff/crm_adapter.py ya están listos, pero NO conectados
    # a get_tariff_config() todavía: el modelo Tariff de CRMBackend no tiene
    # cargo_fijo, y usarlo en vivo pondría ese cargo en 0 en cada cálculo de
    # costo mensual/anual sin que nadie lo decidiera explícitamente.
    CRM_BASE_URL: str = ""
    CRM_SERVICE_EMAIL: str = ""
    CRM_SERVICE_PASSWORD: str = ""

    @model_validator(mode="after")
    def _require_strong_secrets_in_production(self) -> "Settings":
        if self.ENVIRONMENT == "production":
            if len(self.JWT_SECRET) < _MIN_JWT_SECRET_LENGTH:
                raise ValueError("JWT_SECRET debe tener al menos 32 caracteres en producción")
            if not self.API_PASSWORD or self.API_PASSWORD == "changeme":
                raise ValueError("API_PASSWORD debe definirse (y no ser 'changeme') en producción")
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
