"""Providers de inyección de dependencias para InfluxDB."""

from typing import cast

from fastapi import Request

from app.repositories.influx import InfluxRepository


def get_influx_repository(request: Request) -> InfluxRepository:
    return cast(InfluxRepository, request.app.state.influx_repo)
