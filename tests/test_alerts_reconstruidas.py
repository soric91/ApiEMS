"""Las alertas horarias sobreviven al reinicio porque se recalculan.

La campanita mostraba solo lo que quedó en RAM desde el último arranque: al
reiniciar el proceso, el cliente perdía sus avisos aunque los datos que los
provocaron siguieran guardados en InfluxDB.

Se reconstruyen con la MISMA función que evalúa la lectura en vivo
(`hourly_alert`), así que el aviso de ahora y el de la semana pasada dicen la
misma frase de la misma hora. No se persiste nada: la potencia por hora y su
banda ya están, y guardar además el veredicto sería un segundo origen de verdad
que puede contradecir al primero cuando la banda cambie.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.cache import clear_all_caches
from app.core.config import Settings, get_settings
from app.models.variables import Aggregation, Variable
from app.schemas.alerts import Alert
from app.schemas.influx import TimeSeriesPoint
from app.services.alerts.history import hourly_anomalies
from app.services.alerts.state import AlertsState
from tests.conftest import TEST_DEVICE_ID
from tests.fakes import FakeInfluxRepository

# 2026-08-10 03:00 UTC = 22:00 en Bogotá: una hora de noche, sin sol, en la que
# el sitio históricamente importa.
AHORA = datetime(2026, 8, 10, 3, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _sin_cache() -> None:
    # Las bandas se cachean 24 h; entre tests eso mezclaría escenarios.
    clear_all_caches()


@pytest.fixture
def settings() -> Settings:
    return get_settings()


def _historia(vatios: float, horas: int = 24 * 25) -> list[TimeSeriesPoint]:
    """Una serie plana: la banda queda estrecha alrededor de `vatios`.

    Veinticinco días porque la banda pide 20 muestras por hora antes de dar un
    veredicto (`MIN_SAMPLES`): con menos historia no se alerta, y es correcto.

    La variación va por DÍA y no por hora: cada hora del día necesita valores
    distintos entre sí, o su p10 y su p90 coinciden y `classify` no clasifica
    nada — una banda de ancho cero no puede decir qué es raro.
    """
    return [
        TimeSeriesPoint(time=AHORA - timedelta(hours=h), value=vatios + (h // 24 % 5) * 20)
        for h in range(horas)
    ]


def _repo(historia: list[TimeSeriesPoint], picos: list[TimeSeriesPoint]) -> FakeInfluxRepository:
    repo = FakeInfluxRepository()
    # La banda sale del promedio por hora; los picos, del máximo.
    repo.instant_series_by_aggregation[(Variable.POWER_ACTIVE_INST_TOTAL, Aggregation.MEAN)] = (
        historia
    )
    repo.instant_series_by_aggregation[(Variable.POWER_ACTIVE_INST_TOTAL, Aggregation.MAX)] = picos
    return repo


async def test_una_hora_fuera_de_banda_vuelve_a_aparecer(settings: Settings) -> None:
    """El aviso se reconstruye aunque no quede nada en memoria."""
    pico = TimeSeriesPoint(time=AHORA - timedelta(hours=2), value=9000.0)
    repo = _repo(_historia(500.0), [pico])

    alertas = await hourly_anomalies(repo, settings, TEST_DEVICE_ID, days=10, now=AHORA)

    assert len(alertas) == 1
    assert alertas[0].kind == "hourly_power"
    assert alertas[0].device_id == TEST_DEVICE_ID
    assert alertas[0].value == 9000.0


async def test_lo_que_estuvo_dentro_de_lo_normal_no_inventa_alertas(settings: Settings) -> None:
    normal = TimeSeriesPoint(time=AHORA - timedelta(hours=2), value=505.0)
    repo = _repo(_historia(500.0), [normal])

    alertas = await hourly_anomalies(repo, settings, TEST_DEVICE_ID, days=10, now=AHORA)

    assert alertas == []


async def test_una_hora_exportando_nunca_alerta(settings: Settings) -> None:
    # Exportar excedente es siempre favorable, por lejos que quede de lo típico.
    exportando = TimeSeriesPoint(time=AHORA - timedelta(hours=2), value=-9000.0)
    repo = _repo(_historia(500.0), [exportando])

    alertas = await hourly_anomalies(repo, settings, TEST_DEVICE_ID, days=10, now=AHORA)

    assert alertas == []


async def test_sin_banda_no_hay_veredicto(settings: Settings) -> None:
    """Sin historial no se puede decir qué es raro, y no se inventa."""
    repo = _repo([], [TimeSeriesPoint(time=AHORA, value=9000.0)])

    alertas = await hourly_anomalies(repo, settings, TEST_DEVICE_ID, days=10, now=AHORA)

    assert alertas == []


async def test_vienen_de_la_mas_reciente_hacia_atras(settings: Settings) -> None:
    picos = [TimeSeriesPoint(time=AHORA - timedelta(hours=h), value=9000.0) for h in (5, 2, 8)]
    repo = _repo(_historia(500.0), picos)

    alertas = await hourly_anomalies(repo, settings, TEST_DEVICE_ID, days=10, now=AHORA)

    assert [a.timestamp for a in alertas] == sorted((a.timestamp for a in alertas), reverse=True)
    assert alertas[0].timestamp == AHORA - timedelta(hours=2)


def test_el_endpoint_las_devuelve_sin_nada_en_memoria(
    client: TestClient,
    app: FastAPI,
    auth_headers: dict[str, str],
    fake_influx_repo: FakeInfluxRepository,
) -> None:
    """El caso del reinicio: RAM vacía y el cliente igual ve sus avisos."""
    ahora = datetime.now(tz=UTC)
    fake_influx_repo.instant_series_by_aggregation[
        (Variable.POWER_ACTIVE_INST_TOTAL, Aggregation.MEAN)
    ] = [
        TimeSeriesPoint(time=ahora - timedelta(hours=h), value=500.0 + (h // 24 % 5) * 20)
        for h in range(24 * 25)
    ]
    fake_influx_repo.instant_series_by_aggregation[
        (Variable.POWER_ACTIVE_INST_TOTAL, Aggregation.MAX)
    ] = [TimeSeriesPoint(time=ahora - timedelta(hours=2), value=9000.0)]

    response = client.get(
        "/api/v1/alerts", params={"device_id": TEST_DEVICE_ID}, headers=auth_headers
    )

    assert response.status_code == 200
    recientes = response.json()["data"]["recent"]
    assert len(recientes) == 1
    assert recientes[0]["value"] == 9000.0


def test_la_de_memoria_le_gana_a_la_reconstruida_de_la_misma_hora(
    client: TestClient,
    app: FastAPI,
    auth_headers: dict[str, str],
    fake_influx_repo: FakeInfluxRepository,
) -> None:
    """Cuando la hora está en los dos lados, vale la que de verdad se emitió.

    La reconstruida es fiel al veredicto pero lleva el pico de la hora, no el
    valor exacto que disparó el aviso.
    """
    ahora = datetime.now(tz=UTC)
    hora_alerta = ahora - timedelta(hours=2)

    fake_influx_repo.instant_series_by_aggregation[
        (Variable.POWER_ACTIVE_INST_TOTAL, Aggregation.MEAN)
    ] = [
        TimeSeriesPoint(time=ahora - timedelta(hours=h), value=500.0 + (h // 24 % 5) * 20)
        for h in range(24 * 25)
    ]
    fake_influx_repo.instant_series_by_aggregation[
        (Variable.POWER_ACTIVE_INST_TOTAL, Aggregation.MAX)
    ] = [TimeSeriesPoint(time=hora_alerta, value=9000.0)]

    state: AlertsState = app.state.alerts_state
    state.add_if_due(
        Alert(
            kind="hourly_power",
            severity="high",
            device_id=TEST_DEVICE_ID,
            variable=Variable.POWER_ACTIVE_INST_TOTAL.value,
            value=7777.0,
            expected_low=10.0,
            expected_high=50.0,
            bucket=hora_alerta.hour,
            timestamp=hora_alerta,
            message="la que se emitió",
        )
    )

    response = client.get(
        "/api/v1/alerts", params={"device_id": TEST_DEVICE_ID}, headers=auth_headers
    )

    recientes = response.json()["data"]["recent"]
    assert len(recientes) == 1
    assert recientes[0]["value"] == 7777.0
