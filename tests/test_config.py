from app.core.config import Settings


def test_cors_origins_parses_comma_separated() -> None:
    settings = Settings(CORS_ORIGINS="http://a.com, http://b.com ,,http://c.com")
    assert settings.cors_origins_list == ["http://a.com", "http://b.com", "http://c.com"]


def test_defaults() -> None:
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert settings.INFLUX_BUCKET == "modbus_data_v2"
    assert settings.MQTT_TOPIC == "gatewayems/modbus"
    assert settings.MQTT_QOS == 1
    assert settings.is_production is False
