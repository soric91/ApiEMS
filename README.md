# ApiEMS — Backend EMS Residencial

Backend para un Energy Management System residencial. FastAPI + InfluxDB 2.x + MQTT.

## Modelo de medición (importante)

La residencia tiene generación solar, pero **el único punto de medición es el medidor
bidireccional en la frontera con la red**. El sistema solo conoce el balance neto:

| Field | Significado | Agregación |
|---|---|---|
| `POWER_ACTIVE_TOTAL_POS` | Energía **importada** de la red (contador acumulativo) | `difference()` / `last()` |
| `POWER_ACTIVE_TOTAL_NEG` | Energía **exportada** a la red (contador acumulativo) | `difference()` / `last()` |
| `POWER_ACTIVE_INST_*`, `VOLTAGE_*`, `CURRENT_*`, `FACTOR_POTENCIA_TOTAL` | Instantáneas | mean / max / min |

No existen métricas de generación solar ni autoconsumo: solo importación, exportación
y balance neto (`importado - exportado`). Nunca aplicar `mean()` a los contadores.

## Alertas de consumo (sin ML)

`GET /api/v1/alerts` compara el consumo contra bandas de percentiles (P10-P90)
calculadas sobre el historial real — sin modelo entrenado:

- **Horaria** (`recent`, tiempo real vía MQTT + WS): banda de `POWER_ACTIVE_INST_TOTAL`
  por hora local (0-23), agrupando todos los días de la semana. Evaluada en cada
  mensaje MQTT; si el valor cae fuera de banda, se guarda en memoria y se
  difunde por WebSocket como `{"type": "alert", ...}` a todos los clientes
  conectados (no solo a los suscritos a esa variable). **Exportar excedente
  (valor ≤ 0) nunca alerta** — es siempre favorable, sin importar cuánto se
  aleje de lo típico. Si la hora históricamente exporta casi siempre (banda
  con `p90 < 0`, ej. 09:00–16:00 en datos reales) pero ahora se está
  importando, la alerta es `high` directa sin importar la magnitud — el
  cambio de signo en sí es la señal (posible falla del sistema solar o
  consumo que superó la generación).
- **Diaria** (`daily_total`, bajo demanda): compara el ÚLTIMO DÍA COMPLETO
  (ayer) contra la banda de energía importada de su día de semana. Nunca
  evalúa el día en curso (un total parcial siempre parecería "bajo").

Clasificación tipo cerca de Tukey: dentro de `[p10, p90]` = normal; hasta medio
ancho de banda más allá = `moderate`; más lejos = `high`. Bandas cacheadas 24h,
recalculadas con Polars sobre `InfluxRepository` — mismo patrón de caché que
KPIs/analytics. Requiere mínimo de muestras por bucket (20 para la banda
horaria, 3 para la diaria) para evitar alertar sobre ruido con poco historial.

## Costos y tarifa (COP)

El costo/crédito en COP es aritmética sobre los kWh ya calculados (`/consumption`,
`/export`) contra una tarifa configurable — no es una medición nueva.

- Tarifa editable en `data/tariffs.json` (no `.env`, sin redeploy): CU (COP/kWh)
  y cargo fijo por mes de vigencia, más la tasa de crédito por excedente
  exportado (`excedente_cop_kwh`). `GET/PUT /api/v1/tariff`.
- Si un mes del rango consultado no tiene tarifa registrada, se usa la más
  reciente anterior disponible y el mes queda marcado en `stale_months` — el
  costo nunca oculta que es una estimación con tarifa vieja.
- Cargo fijo: concepto mensual/anual, se debe por cada mes calendario que el
  rango **realmente toca** (aunque ese mes no tenga todavía ningún punto de
  consumo). Se omite en `day`/`week` (`cargo_fijo_included=False`) en vez de
  prorratearlo.
- Endpoints: `GET /api/v1/costs/{day,week,month,year}` (presets) y
  `GET /api/v1/costs/range?from&to` (rango libre, siempre con cargo fijo
  incluido — es una elección explícita del usuario, no una vista parcial
  automática). Todos devuelven `series` con el costo/crédito por bucket, para
  graficar junto a `/consumption` y `/export`.
- `GET /api/v1/reports/{daily,weekly,monthly,yearly,custom}` embebe el mismo
  desglose en el campo `costs`, reutilizando los puntos ya obtenidos para el
  reporte (sin llamadas extra a InfluxDB).

## Requisitos

- Python 3.13
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync
cp .env.example .env   # editar credenciales
uv run pre-commit install
```

## Ejecutar

```bash
uv run uvicorn app.main:app --reload
```

- Swagger: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Health: http://localhost:8000/api/v1/health

## Calidad

```bash
uv run ruff check
uv run ruff format
uv run pyright
uv run pytest
uv run pytest --cov=app --cov-report=term-missing   # cobertura (99%+)
```

## Docker

### Topología por defecto: mismo host que Mosquitto/InfluxDB (gateway)

`docker-compose.yml` usa `network_mode: host` en `api`: el contenedor comparte
la pila de red del host directamente, así que dentro del contenedor
`localhost`/`127.0.0.1` **es el host real** (el gateway), no el contenedor.
Esto evita el error clásico de Docker donde `MQTT_HOST=localhost` dentro de un
contenedor con red bridge apuntaría al contenedor mismo, no al broker.

```bash
cp .env.example .env   # editar credenciales; MQTT_HOST=localhost, INFLUX_URL=http://localhost:8086
mkdir -p data && chmod 777 data   # ver nota de permisos abajo
docker compose up -d --build
```

- API: http://localhost:8000/

**Permisos de `data/`**: el contenedor corre como usuario no-root (`apiems`,
uid 999) y `data/tariffs.json` (tarifa eléctrica, editable vía `PUT
/api/v1/tariff`) se persiste con un volumen (`./data:/app/data`) para que
sobreviva a un `docker compose up --build`. El uid del contenedor casi nunca
coincide con el del host, y este proyecto se despliega en máquinas distintas
(dev, gateway) con uids distintos cada vez — por eso el directorio se deja
escribible por cualquiera (`chmod 777`) en vez de fijar un uid específico en
el Dockerfile, que solo funcionaría por coincidencia. Es config no sensible
(tarifas públicas de EPM), no secretos.

`network_mode: host` es Linux-only (no funciona en Docker Desktop Mac/Windows)
— asumido aquí porque el gateway (`iot-gate-imx8`) es Linux ARM64. Si el
backend corre en un host x86_64, ajustar la arquitectura de build según
corresponda (`docker buildx build --platform linux/arm64` o `linux/amd64`).

Sin nginx por defecto (no aporta nada frente al backend expuesto directo en
un entorno de LAN/pruebas, y consume recursos en un gateway ya ajustado).
`nginx/nginx.conf` queda en el repo por si en el futuro hace falta reverse
proxy (TLS, dominio propio, etc.) — para reactivarlo, agregar el servicio de
vuelta a `docker-compose.yml`.

### Alternativa: backend y broker en máquinas separadas

Si Mosquitto/InfluxDB **no** están en el mismo host que el backend, `network_mode:
host` no aporta nada (y en Mac/Windows ni siquiera está disponible) — usar la red
bridge normal de Compose y apuntar a IPs/hostnames alcanzables por red:

```yaml
# docker-compose.yml (variante red bridge)
services:
  api:
    build: .
    image: apiems-backend:latest
    ports:
      - "8000:8000"
    env_file:
      - .env
    environment:
      MQTT_HOST: 192.168.1.26        # IP real del gateway/broker, NO localhost
      INFLUX_URL: http://192.168.1.26:8086
    restart: unless-stopped
```

Checklist al migrar de "mismo host" a "máquinas separadas":

- [ ] Quitar `network_mode: host`
- [ ] Agregar `ports: ["8000:8000"]`
- [ ] `MQTT_HOST` / `INFLUX_URL` en `.env`: IP/hostname real del broker, nunca `localhost`
- [ ] Verificar que el firewall del host del broker permite conexiones entrantes
      desde la IP del host del backend en los puertos 1883 (MQTT) y 8086 (InfluxDB)

## Arquitectura

Separación Repository → Service → API.

```
app/
  api/v1/        # routers REST (auth, dashboard, realtime, history, ...)
  core/          # config, logging, middleware, security, exceptions
  services/      # influx, mqtt, websocket, analytics, kpis
  repositories/  # acceso a datos (Flux parametrizado)
  schemas/       # modelos Pydantic de entrada/salida
  main.py        # application factory
```

## Fases de desarrollo

1. ✅ Fundaciones — estructura, uv, logging, config, app base
2. ✅ Datos — cliente InfluxDB, cliente MQTT, repositories
3. ✅ Seguridad — JWT (login, refresh con rotación, logout, rutas protegidas)
4. ✅ Tiempo real — WebSocket `/ws`, ConnectionManager, estado en memoria
5. ✅ API REST — dashboard, history, consumption, export
6. ✅ Análisis — KPIs, analytics, reportes (Polars), cache TTL
7. ✅ Cierre — cobertura 99%+ (155 tests), Docker + Docker Compose, docs
8. ✅ Alertas — anomalías de consumo por bandas de percentiles (sin ML)
