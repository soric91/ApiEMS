"""Golden de /history/downsample (F6).

El frontend rellena las gráficas en vivo con esta serie (downsample de la
última hora). Con `from`/`to` y `target_points` fijos, `interval_seconds` y los
puntos del doble son deterministas — este golden no necesita normalizar fechas.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.models.variables import Variable
from app.schemas.influx import TimeSeriesPoint
from tests.fakes import FakeInfluxRepository

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDEN_PATH = GOLDEN_DIR / "history_downsample.json"

RANGE = {"from": "2026-07-01T00:00:00Z", "to": "2026-07-01T06:00:00Z"}
TARGET_POINTS = 12
# interval = span / target = 6h / 12 = 1800s.
EXPECTED_INTERVAL_SECONDS = 1800

POINTS = [
    TimeSeriesPoint(time=datetime(2026, 7, 1, h, 0, 0, tzinfo=UTC), value=float(100 + h))
    for h in range(6)
]


def test_golden_history_downsample(
    client: TestClient, fake_influx_repo: FakeInfluxRepository, auth_headers: dict[str, str]
) -> None:
    fake_influx_repo.instant_series_by_variable[Variable.POWER_ACTIVE_INST_TOTAL] = POINTS

    response = client.get(
        "/api/v1/history/downsample",
        params={
            "variable": "TotW",
            "aggregation": "mean",
            "target_points": TARGET_POINTS,
            **RANGE,
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["interval_seconds"] == EXPECTED_INTERVAL_SECONDS

    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN_DIR.mkdir(exist_ok=True)
        GOLDEN_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return

    assert json.loads(GOLDEN_PATH.read_text()) == payload, (
        "/history/downsample divergió del golden. Si el cambio es intencional, "
        "regenera con UPDATE_GOLDEN=1 y documenta el contrato en el PR."
    )
