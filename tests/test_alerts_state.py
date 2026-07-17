from datetime import UTC, datetime

from app.schemas.alerts import Alert
from app.services.alerts.state import MAX_RECENT, AlertsState


def _alert(bucket: int) -> Alert:
    return Alert(
        kind="hourly_power",
        severity="high",
        device_id="11",
        variable="POWER_ACTIVE_INST_TOTAL",
        value=100.0,
        expected_low=10.0,
        expected_high=50.0,
        bucket=bucket,
        timestamp=datetime(2026, 4, 20, bucket, tzinfo=UTC),
        message="test",
    )


def test_recent_returns_most_recent_first() -> None:
    state = AlertsState()
    state.add(_alert(1))
    state.add(_alert(2))
    state.add(_alert(3))
    recent = state.recent(limit=10)
    assert [a.bucket for a in recent] == [3, 2, 1]


def test_recent_respects_limit() -> None:
    state = AlertsState()
    for i in range(5):
        state.add(_alert(i))
    assert len(state.recent(limit=2)) == 2


def test_state_caps_at_max_recent() -> None:
    state = AlertsState()
    for i in range(MAX_RECENT + 10):
        state.add(_alert(i % 24))
    assert len(state.recent(limit=MAX_RECENT + 10)) == MAX_RECENT
