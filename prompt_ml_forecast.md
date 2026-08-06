# PROMPT — Proyecto ML de práctica: forecast de consumo (ApiEMS)

## ROL

Eres un ingeniero de datos/ML pragmático. Este es un proyecto de **práctica** (aplicar ciencia de datos aprendida en un curso), no un requisito de producción del backend ApiEMS. La prioridad es un flujo completo y correcto (extracción → EDA → baseline → modelo → evaluación), no sofisticación.

---

## REGLA #1 — ANTES DE ESCRIBIR UNA SOLA LÍNEA

Este proyecto vive **separado** del backend ApiEMS (carpeta propia, `pyproject.toml` propio via `uv`). **No** agregar `scikit-learn`/`pandas`/etc. a las dependencias de la API — es una elección deliberada de este proyecto, no un olvido. Si hace falta leer cómo se conecta el backend a InfluxDB para replicar el patrón, mirar `app/core/config.py` y `app/services/influx/client.py` de ApiEMS, pero no importar código de ahí — este proyecto es standalone.

**Prohibido**: poner valores reales de credenciales en el `.env` — el `.env` se crea solo con los **nombres** de las variables (placeholders vacíos o de ejemplo), el usuario los completa a mano. **Prohibido** commitear `.env` (debe estar en `.gitignore` desde el commit inicial).

---

## CONTEXTO — de dónde salen los datos

InfluxDB 2.x, bucket con datos de un medidor bidireccional (solo mide balance neto import/export en la acometida — no hay submedición de generación solar ni de consumo bruto por separado). Variables disponibles (ver `app/models/variables.py` de ApiEMS):

```
CURRENT_A, CURRENT_B                    # instantáneas, amperios
VOLTAGE_A, VOLTAGE_B                    # instantáneas, voltios
POWER_ACTIVE_INST_A, POWER_ACTIVE_INST_B
POWER_ACTIVE_INST_TOTAL                 # instantánea, W — positivo=importando, negativo=exportando
POWER_REACTIVE_INST_TOTAL               # instantánea
FACTOR_POTENCIA_TOTAL                   # instantánea
POWER_ACTIVE_TOTAL_POS                  # contador acumulativo kWh — energía importada (monótono)
POWER_ACTIVE_TOTAL_NEG                  # contador acumulativo kWh — energía exportada (monótono)
```

**Regla de dominio no negociable**: `POWER_ACTIVE_TOTAL_POS`/`_NEG` son contadores monótonos crecientes — en Flux solo se leen con `difference()` (delta en un rango) o `last()` (valor puntual), **nunca** `mean()`/`max()`/`min()` directo sobre el contador crudo. Las demás variables son instantáneas, se agregan con `mean()`/`max()`/`min()` normalmente.

**Feature engineering — qué variables usar y cuáles no**: para forecast de consumo, usar solo `POWER_ACTIVE_INST_TOTAL` (+ `hour`, `weekday`, lags derivados de ella). `VOLTAGE_*`, `CURRENT_*`, `FACTOR_POTENCIA_TOTAL`, `POWER_REACTIVE_INST_TOTAL` son casi colineales con la potencia activa (V×I×FP ≈ potencia activa) — no aportan señal nueva a un modelo de forecast y solo meten ruido. Son útiles para otro problema (calidad eléctrica / desbalance de fases), no para este.

---

## QUÉ CONSTRUIR

### 1. Setup del proyecto (`uv`)
```
uv init ml_forecast --python 3.12
cd ml_forecast
uv add polars influxdb-client scikit-learn matplotlib python-dotenv
uv add --dev pytest ruff
```
Estructura:
```
ml_forecast/
  .env                  # placeholders, NO valores reales
  .env.example          # mismo contenido que .env, para commitear
  .gitignore            # .env, __pycache__, *.parquet, .venv
  pyproject.toml
  src/
    config.py           # carga .env (INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET)
    influx_client.py    # conexión a InfluxDB, función de query Flux
    extract.py          # script: descarga histórico -> data/raw.parquet
    features.py          # feature engineering (hour, weekday, lags, rolling)
    train.py             # entrena baseline + modelo, guarda el mejor en models/
    predict.py           # CLI: carga modelo entrenado, predice N horas adelante
    evaluate.py          # MAE/RMSE del modelo vs baseline naive
  data/                  # gitignored, datos descargados
  models/                # gitignored, modelo entrenado (.joblib)
  notebooks/
    eda.ipynb            # exploración
```

### 2. `.env` — solo nombres, sin valores
```bash
# InfluxDB — completar a mano, no commitear con valores reales
INFLUX_URL=
INFLUX_TOKEN=
INFLUX_ORG=
INFLUX_BUCKET=

# Rango a extraer
EXTRACT_START_DAYS_AGO=29
TIMEZONE=America/Bogota
```
Mismos nombres de variable que usa ApiEMS (`INFLUX_URL`, `INFLUX_TOKEN`, `INFLUX_ORG`, `INFLUX_BUCKET`) para que el usuario pueda copiar los valores directo desde el `.env` del backend si quiere apuntar al mismo InfluxDB.

### 3. `influx_client.py` — conexión
Usar `influxdb_client` (cliente oficial) con `INFLUX_URL`/`INFLUX_TOKEN`/`INFLUX_ORG` de `config.py`. Una función `query_power_active_total(start, stop) -> pl.DataFrame` que arma el Flux (`from(bucket) |> range(start, stop) |> filter(_field == "POWER_ACTIVE_INST_TOTAL") |> aggregateWindow(every: 1h, fn: mean)`) y devuelve un DataFrame de Polars con columnas `time`, `value`.

### 4. `extract.py`
Script ejecutable (`uv run python -m src.extract`) que llama `influx_client`, guarda `data/raw.parquet`. Idempotente — si ya existe el archivo, avisa y no vuelve a pegarle a InfluxDB salvo `--force`.

### 5. `features.py`
A partir de `raw.parquet` (tz-aware UTC), **convertir a `TIMEZONE` local antes de extraer hora/día** (mismo bug que ya se corrigió en ApiEMS: `.dt.hour()` sobre una columna UTC da la hora UTC, no la local — usar `.dt.convert_time_zone(tz_name)` primero). Genera: `hour`, `weekday`, `lag_1h`, `lag_24h`, `rolling_mean_24h`. Filas sin lag suficiente (primeras 24h) se descartan.

### 6. `train.py`
- Split **temporal** (últimos N días = test, resto = train) — nunca `train_test_split` aleatorio en series de tiempo.
- Baseline: predicción = `lag_24h` (o `lag_168` si hay 7+ días, "mismo valor hace una semana a esa hora").
- Modelo: `RandomForestRegressor` (o `GradientBoostingRegressor`) de sklearn sobre las features de `features.py`.
- Guarda el modelo entrenado en `models/model.joblib` con `joblib.dump`.

### 7. `evaluate.py`
MAE y RMSE del modelo contra el set de test, comparado lado a lado contra el baseline. Si el modelo no le gana al baseline, imprimirlo tal cual — es una conclusión válida con 29 días de datos, no ocultarla.

### 8. `predict.py`
CLI simple: `uv run python -m src.predict --hours 24` → carga `models/model.joblib`, predice las próximas N horas, imprime tabla o guarda CSV.

---

## FASES

**Fase 1 — Setup + conexión**
`uv init`, estructura de carpetas, `.env`/`.env.example`, `config.py`, `influx_client.py`. Probar conexión con una query mínima (`last()` de `POWER_ACTIVE_INST_TOTAL`) antes de seguir.

**Fase 2 — Extracción + EDA**
`extract.py` funcionando contra InfluxDB real, `notebooks/eda.ipynb` con la serie graficada, estacionalidad diaria/semanal visible.

**Fase 3 — Features + baseline**
`features.py` + baseline naive en `train.py`, `evaluate.py` mostrando el MAE del baseline solo (todavía sin modelo ML).

**Fase 4 — Modelo + evaluación**
RandomForest/GradientBoosting entrenado, comparación baseline vs modelo en `evaluate.py`.

**Fase 5 (opcional) — Predicción servible**
`predict.py` CLI. Si más adelante se quiere exponer como endpoint, eso ya sería un proyecto aparte que consume `models/model.joblib` — no meterlo de vuelta en la API de ApiEMS sin decidirlo explícitamente.

Al final de cada fase: correr el script, mostrar output real (no simulado), y solo entonces seguir a la próxima.
