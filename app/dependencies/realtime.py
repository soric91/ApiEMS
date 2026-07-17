"""Providers de inyección de dependencias para el estado en memoria (tiempo real)."""

from typing import cast

from fastapi import Request

from app.services.realtime.state import RealtimeState


def get_realtime_state(request: Request) -> RealtimeState:
    return cast(RealtimeState, request.app.state.realtime_state)
