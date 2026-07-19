"""Perfiles típicos de carga: por hora del día y por día de la semana."""

import asyncio
from datetime import datetime, timedelta

import polars as pl

from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.analytics import HourProfilePoint, WeekdayProfilePoint
from app.services.analytics.common import auto_interval
from app.services.influx.cache import cached_energy_series, cached_instant_series

_WEEKDAY_NAMES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
_DAY_INTERVAL = timedelta(days=1)


async def daily_profile(
    repo: InfluxDataSource,
    start: datetime,
    stop: datetime,
    device_id: str | None,
    tz_name: str,
) -> list[HourProfilePoint]:
    """Curva típica de potencia neta por hora del día, promediando todos los
    días del rango solicitado. Agrupa por hora LOCAL (`tz_name`) — los
    timestamps llegan de InfluxDB en UTC, y sin convertir primero
    `.dt.hour()` extraería la hora UTC, no la de la zona configurada."""
    every = auto_interval(start, stop)
    points = await cached_instant_series(
        repo, Variable.POWER_ACTIVE_INST_TOTAL, start, stop, every, device_id=device_id
    )
    if not points:
        return []

    df = pl.DataFrame({"time": [p.time for p in points], "value": [p.value for p in points]})
    grouped: pl.DataFrame = (
        df.with_columns(  # pyright: ignore[reportUnknownMemberType]
            pl.col("time").dt.convert_time_zone(tz_name).dt.hour().alias("hour")
        )
        .group_by("hour")
        .agg(
            pl.col("value").mean().alias("avg"),
            pl.col("value").max().alias("max"),
            pl.col("value").min().alias("min"),
            pl.col("value").count().alias("count"),
        )
        .sort("hour")
    )
    return [
        HourProfilePoint(
            hour=int(row["hour"]),
            power_avg_w=round(float(row["avg"]), 2),
            power_max_w=round(float(row["max"]), 2),
            power_min_w=round(float(row["min"]), 2),
            sample_count=int(row["count"]),
        )
        for row in grouped.iter_rows(named=True)
    ]


async def weekday_profile(
    repo: InfluxDataSource,
    start: datetime,
    stop: datetime,
    device_id: str | None,
    tz_name: str,
) -> list[WeekdayProfilePoint]:
    """Consumo/exportación promedio por día de la semana en el rango
    solicitado. Agrupa por día de semana LOCAL (`tz_name`): para Bogotá
    (detrás de UTC) el bucket diario ya alineado por `flux_window_offset`
    cae en la misma fecha UTC, así que `.dt.weekday()` crudo coincide hoy —
    pero eso es una coincidencia del signo del offset, no una garantía; una
    zona adelantada a UTC sí correría el día. Se convierte siempre por
    generalidad, no porque haya un bug activo hoy con `TIMEZONE=America/Bogota`."""
    import_points, export_points = await asyncio.gather(
        cached_energy_series(
            repo, Variable.POWER_ACTIVE_TOTAL_POS, start, stop, _DAY_INTERVAL, device_id
        ),
        cached_energy_series(
            repo, Variable.POWER_ACTIVE_TOTAL_NEG, start, stop, _DAY_INTERVAL, device_id
        ),
    )
    if not import_points and not export_points:
        return []

    # schema explícito: si uno de los dos lados viene vacío (ej. un mes sin
    # ninguna exportación), Polars no puede inferir el dtype de `time` desde
    # una lista vacía y el join siguiente falla por tipos incompatibles.
    time_dtype = pl.Datetime(time_zone="UTC")
    import_df = pl.DataFrame(
        {"time": [p.time for p in import_points], "consumption": [p.value for p in import_points]},
        schema={"time": time_dtype, "consumption": pl.Float64},
    )
    export_df = pl.DataFrame(
        {"time": [p.time for p in export_points], "export": [p.value for p in export_points]},
        schema={"time": time_dtype, "export": pl.Float64},
    )
    merged = import_df.join(export_df, on="time", how="full", coalesce=True).fill_null(0.0)
    grouped: pl.DataFrame = (
        merged.with_columns(  # pyright: ignore[reportUnknownMemberType]
            (pl.col("time").dt.convert_time_zone(tz_name).dt.weekday() - 1).alias("weekday")
        )
        .group_by("weekday")
        .agg(pl.col("consumption").mean().alias("avg_c"), pl.col("export").mean().alias("avg_e"))
        .sort("weekday")
    )
    return [
        WeekdayProfilePoint(
            weekday=int(row["weekday"]),
            weekday_name=_WEEKDAY_NAMES[int(row["weekday"])],
            consumption_avg_kwh=round(float(row["avg_c"]), 2),
            export_avg_kwh=round(float(row["avg_e"]), 2),
        )
        for row in grouped.iter_rows(named=True)
    ]
