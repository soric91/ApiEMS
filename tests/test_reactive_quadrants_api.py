"""GET /analytics/reactive-quadrants: energía reactiva por cuadrante (kvarh).

Cubre el repartido de los contadores Q1Eq..Q4Eq (IEC 60375): Q1/Q2 importada
de la red, Q3/Q4 exportada, su balance y la tendencia por ventana.
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.models.variables import Variable
from app.schemas.influx import EnergyPoint
from tests.fakes import FakeInfluxRepository

_FROM = datetime(2026, 7, 16, 0, 0, tzinfo=UTC)
_TO = datetime(2026, 7, 17, 0, 0, tzinfo=UTC)
_ONE_SEC = timedelta(seconds=1)

Q1 = Variable.POWER_REACTIVE_QUAD1
Q2 = Variable.POWER_REACTIVE_QUAD2
Q3 = Variable.POWER_REACTIVE_QUAD3
Q4 = Variable.POWER_REACTIVE_QUAD4


def test_reactive_quadrants_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/analytics/reactive-quadrants").status_code == 401


def test_reactive_quadrants_csv_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/analytics/reactive-quadrants/csv").status_code == 401


def test_reactive_quadrants_wrong_range_400(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/analytics/reactive-quadrants",
        params={"from": _TO.isoformat(), "to": _FROM.isoformat()},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_reactive_quadrants_csv_wrong_range_400(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/analytics/reactive-quadrants/csv",
        params={"from": _TO.isoformat(), "to": _FROM.isoformat()},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_reactive_quadrants_csv_streams_every_raw_point(
    client: TestClient,
    fake_influx_repo: FakeInfluxRepository,
    auth_headers: dict[str, str],
) -> None:
    """Una fila por lectura real (1 Hz), con los cuatro cuadrantes mezclados y
    sin agregación de ninguna clase — es el cuerpo de "todos los puntos"."""
    t = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    fake_influx_repo.energy_records_points_by_counter = {
        Q1: [EnergyPoint(time=t, value=12.506), EnergyPoint(time=t + _ONE_SEC, value=13.0)],
        Q2: [EnergyPoint(time=t, value=3.0)],
        Q3: [],
        Q4: [EnergyPoint(time=t + _ONE_SEC, value=0.4)],
    }

    response = client.get(
        "/api/v1/analytics/reactive-quadrants/csv",
        params={"from": _FROM.isoformat(), "to": _TO.isoformat(), "device_id": "11"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    # El stream agrupa por contador (orden natural de las tablas de Flux).
    assert response.text.splitlines() == [
        "fecha_hora_utc,identify_device,campo,valor_kvarh",
        "2026-07-16T10:00:00+00:00,bf6a469f-4c2a-4402-9438-49a491ad2238,Q1Eq,12.51",
        "2026-07-16T10:00:01+00:00,bf6a469f-4c2a-4402-9438-49a491ad2238,Q1Eq,13.00",
        "2026-07-16T10:00:00+00:00,bf6a469f-4c2a-4402-9438-49a491ad2238,Q2Eq,3.00",
        "2026-07-16T10:00:01+00:00,bf6a469f-4c2a-4402-9438-49a491ad2238,Q4Eq,0.40",
    ]
    # El volcado pidió los cuatro cuadrantes del rango. El recorte por flota
    # no viaja en esta llamada: en HTTP el repo ya viene acotado por el
    # envoltorio (su recorte se prueba en test_dependencies_influx.py).
    assert fake_influx_repo.calls[-1] == (
        "energy_records",
        ("Q1Eq", "Q2Eq", "Q3Eq", "Q4Eq"),
        "11",
        (),
    )


def test_reactive_quadrants_full_payload(
    client: TestClient,
    fake_influx_repo: FakeInfluxRepository,
    auth_headers: dict[str, str],
) -> None:
    fake_influx_repo.energy_total_by_counter = {
        Q1: 10.0,
        Q2: 20.0,
        Q3: 5.0,
        Q4: 3.0,
    }
    response = client.get(
        "/api/v1/analytics/reactive-quadrants",
        params={"from": _FROM.isoformat(), "to": _TO.isoformat()},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]

    assert data["period_start"] == "2026-07-16T00:00:00Z"
    assert data["q1_kvarh"] == 10.0
    assert data["q2_kvarh"] == 20.0
    assert data["q3_kvarh"] == 5.0
    assert data["q4_kvarh"] == 3.0
    assert data["total_import_kvarh"] == 30.0
    assert data["total_export_kvarh"] == 8.0
    assert data["balance_kvarh"] == 22.0
    assert data["dominant"] == "q2"
    assert data["dominant_kvarh"] == 20.0


def test_reactive_quadrants_dominant_switches_with_values(
    client: TestClient,
    fake_influx_repo: FakeInfluxRepository,
    auth_headers: dict[str, str],
) -> None:
    fake_influx_repo.energy_total_by_counter = {
        Q1: 1.0,
        Q2: 2.0,
        Q3: 30.0,
        Q4: 2.0,
    }
    data = client.get(
        "/api/v1/analytics/reactive-quadrants",
        params={"from": _FROM.isoformat(), "to": _TO.isoformat()},
        headers=auth_headers,
    ).json()["data"]

    assert data["dominant"] == "q3"
    assert data["total_export_kvarh"] == 32.0
    assert data["balance_kvarh"] == -29.0


def test_reactive_quadrants_all_empty_dominant_none(
    client: TestClient,
    fake_influx_repo: FakeInfluxRepository,
    auth_headers: dict[str, str],
) -> None:
    fake_influx_repo.energy_total_by_counter = {Q1: 0.0, Q2: 0.0, Q3: 0.0, Q4: 0.0}
    data = client.get(
        "/api/v1/analytics/reactive-quadrants",
        params={"from": _FROM.isoformat(), "to": _TO.isoformat()},
        headers=auth_headers,
    ).json()["data"]

    assert data["dominant"] is None
    assert data["dominant_kvarh"] == 0.0
    assert data["balance_kvarh"] == 0.0
    assert data["trend"] == []


def test_reactive_quadrants_trend_merges_quadrants_by_time(
    client: TestClient,
    fake_influx_repo: FakeInfluxRepository,
    auth_headers: dict[str, str],
) -> None:
    t1 = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 7, 16, 11, 0, tzinfo=UTC)
    fake_influx_repo.energy_series_points_by_counter = {
        Q1: [EnergyPoint(time=t1, value=2.0), EnergyPoint(time=t2, value=3.0)],
        Q2: [EnergyPoint(time=t1, value=4.0)],
        Q3: [EnergyPoint(time=t2, value=5.0)],
        Q4: [],
    }
    data = client.get(
        "/api/v1/analytics/reactive-quadrants",
        params={"from": _FROM.isoformat(), "to": _TO.isoformat()},
        headers=auth_headers,
    ).json()["data"]

    # Los cuatro cuadrantes se alinean por ventana; donde un contador no
    # reportó queda 0.
    assert data["trend"] == [
        {
            "time": "2026-07-16T10:00:00Z",
            "q1_kvarh": 2.0,
            "q2_kvarh": 4.0,
            "q3_kvarh": 0.0,
            "q4_kvarh": 0.0,
        },
        {
            "time": "2026-07-16T11:00:00Z",
            "q1_kvarh": 3.0,
            "q2_kvarh": 0.0,
            "q3_kvarh": 5.0,
            "q4_kvarh": 0.0,
        },
    ]
