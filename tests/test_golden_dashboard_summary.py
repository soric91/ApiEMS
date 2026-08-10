"""Golden del payload consolidado de /dashboard/summary (F6).

El resumen del panel es el contrato que F5 migró el frontend a consumir en una
sola llamada: potencia/voltajes/corrientes en vivo, energía de hoy/del mes,
costos día/mes y KPIs. El golden bloquea que una refactorización cambie esos
campos sin que se note.

Las fechas dependen del "hoy" de Bogotá, así que se normalizan antes de
comparar/escribir: lo que el golden fija es la estructura y los valores (los
números de energía y potencia vienen de los dobles, constantes).
"""

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.schemas.mqtt import DeviceReading
from app.services.realtime.state import RealtimeState
from tests.fakes import FakeInfluxRepository

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDEN_PATH = GOLDEN_DIR / "dashboard_summary.json"

DEVICE_ID = "bf6a469f-4c2a-4402-9438-49a491ad2238"

READING = DeviceReading(
    device_name="Modbus_DTSU666_11",
    device_id=11,
    identify_device=DEVICE_ID,
    device_type="CT_Meter",
    timestamp=datetime(2026, 7, 16, 13, 26, 0, tzinfo=UTC),
    success=True,
    error=None,
    data={
        "PhV_phsA": 120.4,
        "PhV_phsB": 121.2,
        "A_phsA": 1.93,
        "A_phsB": 2.81,
        "TotW": -442.2,
        "TotPF": 0.75,
    },
)

_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _normalize(obj: object) -> object:
    """Reemplaza fechas (instantes y meses) por plantillas: lo variable de
    un resumen del "día en curso" son las fechas, no los valores."""
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


def test_golden_dashboard_summary(
    client: TestClient,
    app: FastAPI,
    fake_influx_repo: FakeInfluxRepository,
    auth_headers: dict[str, str],
) -> None:
    state: RealtimeState = app.state.realtime_state
    state.update(READING)
    fake_influx_repo.energy_total_value = 5.5

    response = client.get("/api/v1/dashboard/summary", headers=auth_headers)
    assert response.status_code == 200
    payload = _stable_payload(response.json()["data"])

    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN_DIR.mkdir(exist_ok=True)
        GOLDEN_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return

    assert json.loads(GOLDEN_PATH.read_text()) == payload, (
        "/dashboard/summary divergió del golden. Si el cambio es intencional, "
        "regenera con UPDATE_GOLDEN=1 y documenta el contrato en el PR."
    )
