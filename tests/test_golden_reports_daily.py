"""Golden del reporte diario /reports/daily (F6).

/reports/daily es el reporte del día en curso (sin from/to). Su contrato es el
mismo ReportData que /reports/custom; el golden fija estructura y valores, y
normaliza las fechas del "hoy" de Bogotá para que no dependa del calendario.
"""

import json
import os
import re
from pathlib import Path

from fastapi.testclient import TestClient

from tests.fakes import FakeInfluxRepository

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDEN_PATH = GOLDEN_DIR / "reports_daily.json"

_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _normalize(obj: object) -> object:
    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    if isinstance(obj, str):
        if _MONTH_RE.match(obj):
            return "<mes>"
        return _ISO_RE.sub("<fecha>", obj)
    return obj


def _stable_payload(data: dict) -> dict:
    data = dict(data)
    data.pop("generated_at", None)
    return _normalize(data)  # type: ignore[return-value]


def test_golden_daily_report(
    client: TestClient, fake_influx_repo: FakeInfluxRepository, auth_headers: dict[str, str]
) -> None:
    fake_influx_repo.energy_total_value = 5.5
    response = client.get("/api/v1/reports/daily", headers=auth_headers)
    assert response.status_code == 200
    payload = _stable_payload(response.json()["data"])

    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN_DIR.mkdir(exist_ok=True)
        GOLDEN_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return

    assert json.loads(GOLDEN_PATH.read_text()) == payload, (
        "/reports/daily divergió del golden. Si el cambio es intencional, "
        "regenera con UPDATE_GOLDEN=1 y documenta el contrato en el PR."
    )
