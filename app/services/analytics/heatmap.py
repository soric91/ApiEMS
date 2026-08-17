"""Mapa de calor hora x día: la forma del consumo de un vistazo.

La misma energía que ya se grafica como serie, reordenada en una cuadrícula de
24 filas por N días. Es la vista que hace saltar los patrones que una línea
esconde ("los martes a las 7 p.m. siempre gasto"), y la que traen todas las
plataformas de energía del mercado.

Cuatro métricas sobre los mismos contadores:

- `import` / `export` — energía por hora (kWh), directo de `difference()`.
- `net` — importado menos exportado; el balance de esa hora en la acometida.
- `cost` — lo que costó la importación de esa hora (kWh por el CU del mes).
  Deliberadamente NO neto: el crédito por exportar se reparte en dos tramos
  que se resuelven POR MES (ver `services/tariff/cost.py`), y repartirlos hora
  a hora daría un número que no suma a la factura.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Literal

import polars as pl

from app.models.variables import Variable
from app.repositories.influx import InfluxDataSource
from app.schemas.analytics import HeatmapResult
from app.schemas.influx import EnergyPoint
from app.schemas.tariff import TariffConfig
from app.services.influx.cache import cached_energy_series
from app.services.tariff.cost import rate_for_month

HeatmapMetric = Literal["import", "export", "net", "cost"]

_BUCKET = timedelta(hours=1)
_HOURS = 24

_UNITS: dict[str, str] = {
    "import": "kWh",
    "export": "kWh",
    "net": "kWh",
    "cost": "COP",
}


def _frame(points: list[EnergyPoint], column: str, tz_name: str) -> pl.DataFrame:
    """Los puntos con su fecha y hora LOCALES.

    Local y no UTC: agrupado en UTC, para Bogotá (UTC-5) las horas de la noche
    caerían en el día siguiente y el mapa mostraría el consumo corrido cinco
    casillas.
    """
    schema = {"time": pl.Datetime(time_zone="UTC"), column: pl.Float64}
    frame = pl.DataFrame(
        {"time": [p.time for p in points], column: [p.value for p in points]}, schema=schema
    )
    return frame.with_columns(  # pyright: ignore[reportUnknownMemberType]
        pl.col("time").dt.convert_time_zone(tz_name).dt.date().alias("fecha"),
        pl.col("time").dt.convert_time_zone(tz_name).dt.hour().alias("hora"),
        pl.col("time").dt.convert_time_zone(tz_name).dt.strftime("%Y-%m").alias("mes"),
    )


def _celdas(
    import_points: list[EnergyPoint],
    export_points: list[EnergyPoint],
    metric: HeatmapMetric,
    tariff: TariffConfig,
    tz_name: str,
) -> tuple[list[str], list[list[float | None]]]:
    """La cuadrícula (fechas, matriz[fecha][hora]) — cálculo puro (Polars)."""
    importado = _frame(import_points, "import", tz_name)
    exportado = _frame(export_points, "export", tz_name).drop("mes")
    merged = importado.join(exportado, on=["time", "fecha", "hora"], how="full", coalesce=True)
    merged = merged.fill_null(0.0)

    if metric == "import":
        valores = pl.col("import")
    elif metric == "export":
        valores = pl.col("export")
    elif metric == "net":
        valores = pl.col("import") - pl.col("export")
    else:
        # Un precio por mes: el mapa puede cruzar un cambio de tarifa, y cada
        # hora tiene que costar lo que costaba ese mes, no lo que cuesta hoy.
        # Un mes sin tarifa registrada vale 0 en vez de inventar un precio —
        # `/costs/*` ya avisa de esos meses con `stale_months`.
        meses: list[str] = [
            str(mes)
            for mes in merged["mes"].unique().to_list()  # pyright: ignore[reportUnknownMemberType]
            if mes is not None
        ]
        precios: dict[str, float] = {}
        for mes in meses:
            rate, _ = rate_for_month(tariff, mes)
            precios[mes] = rate.cu_cop_kwh if rate is not None else 0.0
        merged = merged.with_columns(  # pyright: ignore[reportUnknownMemberType]
            pl.col("mes")
            .replace_strict(precios, default=0.0)  # pyright: ignore[reportUnknownMemberType]
            .alias("precio")
        )
        valores = pl.col("import") * pl.col("precio")

    agrupado: pl.DataFrame = (
        merged.with_columns(valores.alias("valor"))  # pyright: ignore[reportUnknownMemberType]
        .group_by("fecha", "hora")
        .agg(pl.col("valor").sum())
        .sort("fecha", "hora")
    )

    por_fecha: dict[str, list[float | None]] = {}
    for row in agrupado.iter_rows(named=True):
        fecha = str(row["fecha"])
        fila = por_fecha.setdefault(fecha, [None] * _HOURS)
        fila[int(row["hora"])] = round(float(row["valor"]), 2)

    fechas = sorted(por_fecha)
    return fechas, [por_fecha[fecha] for fecha in fechas]


async def heatmap(
    repo: InfluxDataSource,
    start: datetime,
    stop: datetime,
    device_id: str | None,
    metric: HeatmapMetric,
    tariff: TariffConfig,
    tz_name: str,
) -> HeatmapResult:
    """La cuadrícula hora x día del rango pedido.

    Una casilla vacía (`null`) es una hora sin ningún dato — no un cero. La
    diferencia importa: pintar de "consumo cero" las horas que el gateway
    estuvo caído es exactamente lo que `/analytics/coverage` viene a evitar.
    """
    import_points, export_points = await asyncio.gather(
        cached_energy_series(
            repo, Variable.POWER_ACTIVE_TOTAL_POS, start, stop, _BUCKET, device_id
        ),
        cached_energy_series(
            repo, Variable.POWER_ACTIVE_TOTAL_NEG, start, stop, _BUCKET, device_id
        ),
    )
    if not import_points and not export_points:
        return HeatmapResult(
            device_id=device_id,
            period_start=start,
            period_end=stop,
            metric=metric,
            unit=_UNITS[metric],
            dates=[],
            values=[],
        )

    fechas, valores = await asyncio.to_thread(
        _celdas, import_points, export_points, metric, tariff, tz_name
    )
    return HeatmapResult(
        device_id=device_id,
        period_start=start,
        period_end=stop,
        metric=metric,
        unit=_UNITS[metric],
        dates=fechas,
        values=valores,
    )
