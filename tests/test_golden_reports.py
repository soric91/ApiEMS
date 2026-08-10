"""Snapshots de regresión del payload de /reports (contrato de F2).

El golden bloquea que una refactorización cambie los campos y valores del
reporte sin que se note. Se regenera SOLO al cambiar el contrato a propósito:
`UPDATE_GOLDEN=1 python -m pytest tests/test_golden_reports.py`.
"""

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from tests.fakes import FakeInfluxRepository

GOLDEN_DIR = Path(__file__).parent / "golden"

# Rango fijo anclado a julio 2026: `period_start`/`period_end` y `stale_months`
# son deterministas (solo dependen del rango y de la tarifa de prueba, vacía).
CUSTOM_RANGE = {"from": "2026-07-01T00:00:00Z", "to": "2026-07-02T00:00:00Z"}


def _stable_payload(data: dict) -> dict:
    """Quita lo que varía entre ejecuciones (`generated_at` lleva ahora real)."""
    data = dict(data)
    data.pop("generated_at", None)
    return data


def test_golden_custom_report(
    client: TestClient, fake_influx_repo: FakeInfluxRepository, auth_headers: dict[str, str]
) -> None:
    fake_influx_repo.energy_total_value = 5.5
    response = client.get("/api/v1/reports/custom", params=CUSTOM_RANGE, headers=auth_headers)
    assert response.status_code == 200
    payload = _stable_payload(response.json()["data"])

    golden_path = GOLDEN_DIR / "reports_custom.json"
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN_DIR.mkdir(exist_ok=True)
        golden_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return

    assert json.loads(golden_path.read_text()) == payload, (
        "/reports/custom divergió del golden. Si el cambio es intencional, "
        "regenera con UPDATE_GOLDEN=1 y documenta el contrato en el PR."
    )
