from datetime import UTC, datetime

from app.schemas.mqtt import DeviceReading
from app.services.realtime.state import RealtimeState

READING = DeviceReading(
    device_name="Modbus_DTSU666_11",
    device_id=11,
    identify_device="bf6a469f-4c2a-4402-9438-49a491ad2238",
    device_type="CT_Meter",
    timestamp=datetime(2026, 7, 16, 13, 26, 0, tzinfo=UTC),
    success=True,
    error=None,
    data={"VOLTAGE_A": 120.4, "POWER_ACTIVE_INST_TOTAL": -442.2},
)


UUID = "bf6a469f-4c2a-4402-9438-49a491ad2238"


def test_update_then_latest() -> None:
    state = RealtimeState()
    assert state.latest() == []
    state.update(READING)
    snapshots = state.latest()
    assert len(snapshots) == 1
    assert snapshots[0].device_id == UUID
    assert snapshots[0].data["VOLTAGE_A"] == 120.4


def test_device_lookup() -> None:
    state = RealtimeState()
    state.update(READING)
    assert state.device(UUID) is not None
    assert state.device("99") is None


def test_update_overwrites_same_device() -> None:
    state = RealtimeState()
    state.update(READING)
    updated = READING.model_copy(update={"data": {"VOLTAGE_A": 121.0}})
    state.update(updated)
    assert len(state.latest()) == 1
    assert state.device(UUID).data["VOLTAGE_A"] == 121.0  # pyright: ignore[reportOptionalMemberAccess]


def test_values_of_filters_by_variable() -> None:
    state = RealtimeState()
    state.update(READING)
    assert len(state.values_of("VOLTAGE_A")) == 1
    assert state.values_of("FACTOR_POTENCIA_TOTAL") == []
