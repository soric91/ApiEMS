from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.models.variables import Aggregation
from app.schemas.influx import EnergyPoint, TimeSeriesPoint
from tests.fakes import FakeInfluxRepository

FROM = "2026-07-01T00:00:00Z"
TO = "2026-07-02T00:00:00Z"


def test_history_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/history", params={"variable": "PhV_phsA"}).status_code == 401


def test_history_instant_variable(
    client: TestClient, fake_influx_repo: FakeInfluxRepository, auth_headers: dict[str, str]
) -> None:
    fake_influx_repo.instant_series_points = [
        TimeSeriesPoint(time=datetime(2026, 7, 1, 12, tzinfo=UTC), value=120.5)
    ]
    response = client.get(
        "/api/v1/history",
        params={"variable": "PhV_phsA", "from": FROM, "to": TO, "aggregation": "max"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["aggregation"] == "max"
    assert body["points"][0]["value"] == 120.5
    assert fake_influx_repo.calls[-1][0] == "instant_series"


def test_history_counter_variable_uses_difference(
    client: TestClient, fake_influx_repo: FakeInfluxRepository, auth_headers: dict[str, str]
) -> None:
    fake_influx_repo.energy_series_points = [
        EnergyPoint(time=datetime(2026, 7, 1, 12, tzinfo=UTC), value=0.5)
    ]
    response = client.get(
        "/api/v1/history",
        params={"variable": "TotWh_import", "from": FROM, "to": TO},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert fake_influx_repo.calls[-1][0] == "energy_series"
    assert response.json()["data"]["points"][0]["value"] == 0.5


def test_history_invalid_range_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get(
        "/api/v1/history",
        params={"variable": "PhV_phsA", "from": TO, "to": FROM},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_history_too_many_points_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get(
        "/api/v1/history",
        params={
            "variable": "PhV_phsA",
            "from": "2020-01-01T00:00:00Z",
            "to": "2026-01-01T00:00:00Z",
            "interval_seconds": 1,
        },
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_history_stats_reduces_over_raw_data(
    client: TestClient, fake_influx_repo: FakeInfluxRepository, auth_headers: dict[str, str]
) -> None:
    """F0.2: los estadísticos salen de reducciones de InfluxDB sobre los datos
    crudos, no de los puntos ya agregados por ventana. Una serie que promedia
    520 W puede tener un pico real de 10 kW, y es ese el que debe salir."""
    fake_influx_repo.instant_reduce_by_aggregation = {
        Aggregation.MIN: 10.0,
        Aggregation.MAX: 10_000.0,
        Aggregation.MEAN: 520.0,
        Aggregation.LAST: 480.0,
    }

    response = client.get(
        "/api/v1/history/stats",
        params={"variable": "TotW", "from": FROM, "to": TO},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["min"] == 10.0
    assert body["max"] == 10_000.0
    assert body["mean"] == 520.0
    assert body["last"] == 480.0
    aggregations = sorted(
        str(call[2]) for call in fake_influx_repo.calls if call[0] == "instant_reduce"
    )
    assert aggregations == ["last", "max", "mean", "min"]


def test_history_stats_rejects_cumulative_counter(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Un contador monótono no admite mean/max/min — solo difference()/last()."""
    response = client.get(
        "/api/v1/history/stats",
        params={"variable": "TotWh_import", "from": FROM, "to": TO},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "acumulativo" in response.json()["message"]


def test_history_stats_invalid_range_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/history/stats",
        params={"variable": "TotW", "from": TO, "to": FROM},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_history_stats_requires_auth(client: TestClient) -> None:
    response = client.get(
        "/api/v1/history/stats", params={"variable": "TotW", "from": FROM, "to": TO}
    )
    assert response.status_code == 401


def test_history_downsample_computes_interval(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/history/downsample",
        params={
            "variable": "PhV_phsA",
            "from": "2026-01-01T00:00:00Z",
            "to": "2026-07-01T00:00:00Z",
            "target_points": 100,
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["interval_seconds"] > 900  # rango de 6 meses / 100 puntos >> 15min


def test_history_downsample_is_cached(
    client: TestClient, fake_influx_repo: FakeInfluxRepository, auth_headers: dict[str, str]
) -> None:
    """El backfill de las gráficas en vivo llama a /history/downsample con el
    mismo patrón de rango una y otra vez: la respuesta va cacheada (TTL corto)
    para que el mismo bucket no golpee InfluxDB dos veces."""
    fake_influx_repo.instant_series_points = [
        TimeSeriesPoint(time=datetime(2026, 7, 1, 12, tzinfo=UTC), value=120.5)
    ]
    params = {"variable": "PhV_phsA", "from": FROM, "to": TO, "target_points": 30}

    response = client.get("/api/v1/history/downsample", params=params, headers=auth_headers)
    assert response.status_code == 200
    queries = len(fake_influx_repo.calls)

    response = client.get("/api/v1/history/downsample", params=params, headers=auth_headers)
    assert response.status_code == 200
    assert len(fake_influx_repo.calls) == queries  # cache hit: no hubo nueva query
