from datetime import UTC, datetime, timedelta

from app.schemas.alerts import Alert, AlertSeverity
from app.services.alerts.state import MAX_RECENT, AlertsState

BASE = datetime(2026, 4, 20, 0, tzinfo=UTC)


def _alert(bucket: int, minute_offset: int = 0, severity: AlertSeverity = "high") -> Alert:
    return Alert(
        kind="hourly_power",
        severity=severity,
        device_id="11",
        variable="TotW",
        value=100.0,
        expected_low=10.0,
        expected_high=50.0,
        bucket=bucket,
        timestamp=BASE + timedelta(minutes=minute_offset),
        message="test",
    )


def test_recent_returns_most_recent_first() -> None:
    state = AlertsState()
    state.add_if_due(_alert(1, minute_offset=0))
    state.add_if_due(_alert(2, minute_offset=20))
    state.add_if_due(_alert(3, minute_offset=40))
    recent = state.recent(limit=10)
    assert [a.bucket for a in recent] == [3, 2, 1]


def test_recent_respects_limit() -> None:
    state = AlertsState()
    for i in range(5):
        state.add_if_due(_alert(i, minute_offset=i * 20))
    assert len(state.recent(limit=2)) == 2


def test_state_caps_at_max_recent() -> None:
    state = AlertsState()
    for i in range(MAX_RECENT + 10):
        state.add_if_due(_alert(i % 24, minute_offset=i * 20))
    assert len(state.recent(limit=MAX_RECENT + 10)) == MAX_RECENT


def test_add_if_due_dedupes_same_condition_within_cooldown() -> None:
    state = AlertsState(cooldown=timedelta(minutes=15))
    assert state.add_if_due(_alert(10, minute_offset=0)) is True
    assert state.add_if_due(_alert(10, minute_offset=5)) is False
    assert len(state.recent(limit=10)) == 1


def test_add_if_due_allows_after_cooldown_elapses() -> None:
    state = AlertsState(cooldown=timedelta(minutes=15))
    assert state.add_if_due(_alert(10, minute_offset=0)) is True
    assert state.add_if_due(_alert(10, minute_offset=16)) is True
    assert len(state.recent(limit=10)) == 2


def test_add_if_due_escalation_bypasses_cooldown() -> None:
    """moderate -> high dentro del cooldown SÍ se emite: es información
    nueva (la condición empeoró), no una repetición."""
    state = AlertsState(cooldown=timedelta(minutes=15))
    assert state.add_if_due(_alert(10, minute_offset=0, severity="moderate")) is True
    assert state.add_if_due(_alert(10, minute_offset=5, severity="high")) is True
    assert len(state.recent(limit=10)) == 2


def test_add_if_due_high_to_moderate_within_cooldown_still_deduped() -> None:
    """high -> moderate no es escalación (mejoró, no empeoró) — sigue
    dedupeado dentro del cooldown."""
    state = AlertsState(cooldown=timedelta(minutes=15))
    assert state.add_if_due(_alert(10, minute_offset=0, severity="high")) is True
    assert state.add_if_due(_alert(10, minute_offset=5, severity="moderate")) is False


def test_add_if_due_different_bucket_not_deduped() -> None:
    state = AlertsState(cooldown=timedelta(minutes=15))
    assert state.add_if_due(_alert(10, minute_offset=0)) is True
    assert state.add_if_due(_alert(11, minute_offset=1)) is True
    assert len(state.recent(limit=10)) == 2
