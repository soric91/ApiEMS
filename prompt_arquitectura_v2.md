# PROMPT — Rediseño de API (ApiEMS) + integración con CRMBackend + frontend escalable

## ROL

Eres el mismo arquitecto que ya construyó ApiEMS (FastAPI + InfluxDB + MQTT) y frontendEMS (React/TS/Rsbuild). Este documento es el resultado de auditar **ApiEMS**, **frontendEMS** y **CRMBackend** (proyecto hermano, en `/home/kurosaki/Documentos/projects/CRMBackend`) para resolver cuatro pedidos concretos del dueño del producto:

1. Eliminar endpoints redundantes entre ApiEMS y su frontend.
2. Exportar históricos con intervalo elegible (N minutos / N horas), no fijo.
3. Visualizador escalable a múltiples medidores, trifásico por defecto (todas las variables de fase).
4. Definir cómo ApiEMS convive con CRMBackend: CRMBackend pasa a ser dueño de **tarifas** y de **si un cliente está habilitado** — ApiEMS no debe reconstruir esa lógica.

**El Dashboard actual (`/dashboard`, `/dashboard/cards`, `/dashboard/status`, `Dashboard.tsx`) queda fuera de alcance — al usuario le gusta como está.** Todo lo de abajo aplica a Analytics, Consumo/Exportación, Costos, Reportes, Histórico y Tarifa.

---

## REGLA #1 — ANTES DE ESCRIBIR UNA SOLA LÍNEA

- **No tocar `/dashboard*` ni `Dashboard.tsx`.**
- **No reimplementar en ApiEMS nada que sea responsabilidad de CRMBackend** (tarifas, si el cliente está habilitado, jerarquía cliente→sede→gateway→equipo). ApiEMS **consume** esa API, no la duplica.
- CRMBackend es código de otro repo — no se edita desde aquí. Si al diseñar la integración falta un endpoint o un campo en CRMBackend, se anota en la sección **"Gaps para pedirle al CRM"** de este documento y se para ahí — el usuario lo construye él mismo.
- Nada de lo que ya funciona se borra de un día para otro. Los endpoints marcados "deprecar" se dejan funcionando y se migra el frontend primero; se retiran en una fase aparte, explícita, con aprobación previa.
- Los endpoints nuevos siguen exactamente los patrones ya establecidos en `app/api/v1/*.py`: `ApiResponse[T]` envelope, `Annotated[..., Depends(...)]`, `CurrentUser` en toda ruta protegida, docstrings explicando la regla de dominio (no el qué, el por qué).

---

## CONTEXTO — auditoría de lo que existe hoy

### 1. Mapa de redundancia real en ApiEMS (evidencia, no intuición)

Se leyó el código de cada router y de los servicios que invoca. Hallazgo central: **`app/services/reports/builder.py::build_report()` ya es el agregador completo y cacheado** (usa `cached_energy_series`/`cached_energy_total`, no reconsulta InfluxDB si otro endpoint ya pidió lo mismo). Los siguientes endpoints son versiones parciales, más viejas, de lo mismo que `build_report` ya arma — y **no comparten caché con reports** porque llaman las funciones "crudas" en vez de las `cached_*`:

| Endpoint | Qué calcula | Redundante con | Por qué es redundante |
|---|---|---|---|
| `GET /consumption/{day,week,month,year}` | `period_summary(POWER_ACTIVE_TOTAL_POS, ...)` | `reports.consumption_kwh` / `consumption_series` | Mismo dato, sin caché compartida con `/reports` |
| `GET /export/{day,week,month,year}` | `period_summary(POWER_ACTIVE_TOTAL_NEG, ...)` | `reports.export_kwh` / `export_series` | Ídem |
| `GET /costs/{day,week,month,year}` | `_cost_for_period()` — vuelve a llamar `period_summary` para POS **y** NEG, sin caché | `reports.costs` | `build_report` calcula exactamente este `CostBreakdown` con las series ya cacheadas; `/costs/day` repite las 2 consultas a InfluxDB que `/consumption/day` + `/export/day` ya hicieron |
| `GET /analytics` (overview, sin sufijo) | `consumption_kwh, export_kwh, max_demand, load_factor, base_load` de un periodo (default: hoy) | `reports.max_demand/load_factor/base_load/consumption_kwh/export_kwh` (`ReportData` para `report_type="daily"`) | Es un subconjunto literal de `/reports/daily` |
| `GET /kpis` | `compute_kpis()` — potencia/voltaje/corriente/FP + consumo/exportación por periodo | `reports.kpis` (mismo `compute_kpis()`, llamado dentro de `build_report`) | Misma función, dos endpoints |
| `GET /dashboard/cards` | Reformatea `/dashboard` como lista de tarjetas | `/dashboard` | Fuera de alcance (ver Regla #1), se deja igual |

**No son redundantes** (tienen forma/propósito propio, se mantienen tal cual):
- `GET /history`, `/history/downsample`, `/history/range` — única fuente de series crudas por variable individual (voltaje, corriente, FP, etc.), nada más las expone.
- `GET /analytics/daily-profile`, `/analytics/monthly-profile` (perfil semanal), `/analytics/compare`, `/analytics/summary` — patrones/comparaciones que `/reports` no calcula.
- `GET /costs/range` — rango libre arbitrario, no atado a día/semana/mes/año calendario; `/reports/custom` cubre un caso parecido pero con distinto formato de entrada (`start`/`stop` vs `from`/`to` — unificar en la Fase 2).
- `/realtime/*`, `/alerts`, `/dashboard*` — dominio propio, no tocar.

### 2. Veredicto de diseño

**`/reports/{daily,weekly,monthly,yearly,custom}` pasa a ser la fuente única para vistas de periodo fijo** (consumo + exportación + costo + kpis + demanda pico + factor de carga + carga base, todo en una sola llamada, ya cacheado). Se **deprecan** `/consumption/*`, `/export/*`, `/costs/{day,week,month,year}` (no `/costs/range`, que sigue viva) y el `/analytics` bare (overview). `/kpis` se deprecia como endpoint standalone — su valor pasa a consumirse únicamente vía `reports.kpis`.

Consecuencia en frontend: `ConsumptionExport.tsx` deja de llamar `getConsumption` + `getExport` + `getCosts` por separado (3 round-trips) y pasa a leer **un solo** `getReport(period)` (ya usado por `Reports.tsx`). Esto no es "quitarle una pestaña al usuario" — la pestaña de Consumo/Exportación sigue existiendo, solo cambia de dónde saca los números.

---

## QUÉ CONSTRUIR

### FASE 1 — Consolidar endpoints redundantes (solo ApiEMS + frontendEMS, sin CRM todavía)

1. Añadir a `ReportData` (`app/schemas/reports.py`) los campos que a día de hoy solo expone `/consumption`/`/export` (revisar que `consumption_series`/`export_series` ya cubran lo que `EnergySummary.series` daba — si sí, no falta nada).
2. Migrar `ConsumptionExport.tsx` a `getReport(period)` (frontend). Confirmar en el navegador que muestra los mismos números que antes.
3. Marcar `/consumption/*`, `/export/*`, `/costs/{day,week,month,year}`, `/kpis`, `/analytics` (bare) como `deprecated=True` en el decorador de FastAPI (aparecen tachados en `/docs`, siguen funcionando) — no borrar código todavía.
4. Checkpoint: build sin errores, `Reports.tsx`/`ConsumptionExport.tsx`/`Analytics.tsx` probados contra backend real, dashboard sigue intacto. Esperar aprobación antes de la Fase 2 (que sí borra código).

**Fase 1.5 (tras aprobación explícita)**: borrar `app/api/v1/consumption.py`, `export.py`, `energy_router_factory.py`, los 4 endpoints day/week/month/year de `costs.py`, el bare `GET /analytics`, y `app/api/v1/kpis.py`. Quitar sus imports de `router.py` y sus archivos `src/api/*.ts` equivalentes en frontendEMS.

---

### FASE 2 — Exportación con intervalo elegible

Hoy `History.tsx` solo ofrece un binario: "raw" (`interval_seconds` fijo en 300) o "downsample" (`target_points` fijo en 500) — el usuario no elige nada. `Reports.tsx` exporta el CSV con el intervalo que le tocó al `report_type` (1h para diario, 1 día para semanal/mensual/anual) sin poder cambiarlo.

**Backend**: `GET /history` ya acepta `interval_seconds` libre — no hace falta tocar el backend, el límite (`MAX_POINTS = 5000` en `app/api/v1/history.py`) ya protege contra un intervalo demasiado fino en un rango largo. Lo que falta es exponerlo en la UI.

**Frontend — `History.tsx`**:
- Reemplazar el toggle "raw vs downsample" por un selector explícito: **"Agrupar cada"** con opciones en minutos (1, 5, 15, 30) y horas (1, 3, 6, 12, 24), que se traduce directo a `interval_seconds` al llamar `getHistory`.
- Si el usuario pide un intervalo que excede `MAX_POINTS` para el rango elegido, mostrar el mismo mensaje que ya devuelve el backend (400) en vez de un error genérico — el backend ya lo explica bien ("amplía el interval o reduce el rango").
- El botón "Exportar CSV" exporta exactamente los puntos que están en pantalla (mismo intervalo que el usuario eligió) — no un intervalo distinto silencioso.

**Frontend — `Reports.tsx`**: el CSV de un reporte sigue usando el intervalo natural del periodo (no tiene sentido pedir "cada 5 minutos" en un reporte anual). Si se quiere granularidad fina sobre un rango de reporte, ese es el caso de uso de `History.tsx`, no de Reports — no dupliques el selector ahí.

Checkpoint: exportar CSV desde History con 3 intervalos distintos (1 min, 1 hora, 12 horas) sobre el mismo rango, confirmar que el archivo tiene la cantidad de filas esperada y que el rango largo con intervalo muy fino da el 400 esperado, no un crash.

---

### FASE 3 — Visualizador escalable a múltiples medidores, trifásico por defecto

**Gap encontrado en `app/models/variables.py`**: el catálogo solo tiene fase A y B (`CURRENT_A/B`, `VOLTAGE_A/B`, `POWER_ACTIVE_INST_A/B`). Un sistema trifásico real necesita fase C. Si el hardware/firmware ya mide C pero ApiEMS no lo modela, es la primera brecha a cerrar:

```python
# app/models/variables.py — agregar
CURRENT_C = "CURRENT_C"
VOLTAGE_C = "VOLTAGE_C"
POWER_ACTIVE_INST_C = "POWER_ACTIVE_INST_C"
```

Confirmar primero con el payload real que llega por MQTT (`DeviceReading.data`, `app/schemas/mqtt.py`) si la fase C ya viene y hoy se descarta silenciosamente (el `dict[str, float]` de `data` acepta cualquier clave; si el JSON trae `CURRENT_C` y el enum no la tiene, en los endpoints que hacen `data.get(Variable.CURRENT_C.value, 0.0)` simplemente no se usa, no truena — hay que revisar si además pasa algo así en la escritura a InfluxDB antes de asumir que "ya está guardado, solo falta exponerlo").

**Multi-medidor**: la mayoría de endpoints ya aceptan `device_id: str | None` (patrón ya establecido) — el backend no es el cuello de botella, es el frontend, que hoy asume un solo dispositivo implícito en varias pantallas. Construir:

1. Un selector de medidor global (context de React, no por página) que liste los `device_id` disponibles — usar `GET /realtime/latest` (ya devuelve `list[DeviceSnapshot]`, uno por dispositivo activo) como fuente, no inventar un endpoint nuevo.
2. Ese `device_id` seleccionado se propaga a todas las llamadas de Analytics/History/Reports/Costos que ya aceptan el parámetro — no hay que tocar el backend para esto.
3. Vista de detalle por medidor: tarjetas de las 3 fases (A/B/C) en vez de 2, usando el enum extendido de arriba. Si un medidor es monofásico, ocultar la fase que no reporta (`data.get(...)` viene ausente) en vez de mostrar un 0 engañoso — ahí sí hay un cambio de comportamiento real: hoy `dashboard.py` usa `data.get(Variable.VOLTAGE_B.value, 0.0)`, que muestra "0 V" para un medidor monofásico en vez de "N/A". Igual patrón a evitar en las tarjetas nuevas.

Checkpoint: con al menos 2 `device_id` distintos reportando por MQTT (se puede simular con `modbus-simulator` si existe en el entorno), confirmar que el selector cambia todos los paneles y que un medidor monofásico no muestra fases fantasma.

---

### FASE 4 — Formato de ingesta tolerante + identidad de dispositivo alineada con CRMBackend

**Hallazgo importante**: el tópico MQTT nuevo que va a usar el gateway es

```
gatewayems/modbus/74/7d8704bd-5fe0-4686-972e-a71febc718d7
```

Formato: `{prefijo}/modbus/{modbus_id}/{gateway_uuid}`. Ese `gateway_uuid` es **el mismo UUID que ya existe en CRMBackend** (`Gateway.uuid`, `app/models/gateway.py`) — es el identificador que el firmware usa para pedir su configuración (`GET /gateway-config/{gateway_uuid}/config` en CRMBackend). Esto es una coincidencia deliberada, no casualidad: el objetivo es que **el mismo UUID identifique al gateway tanto en el plano de control (CRMBackend) como en el plano de telemetría (ApiEMS)**.

Hoy ApiEMS **no lee nada del tópico** — se suscribe a un tópico fijo (`MQTT_TOPIC=gatewayems/modbus`, sin wildcard) y el `device_id` que usa en InfluxDB y en `RealtimeState` es `str(reading.device_id)`, un entero arbitrario que pone el script de adquisición (`DeviceReading.device_id: int`) y que **no tiene ninguna relación con el UUID de CRMBackend**. Con un solo gateway esto no dolía; con varios, dos gateways distintos podrían mandar `device_id=74` cada uno con datos completamente distintos y ApiEMS los mezclaría en la misma clave de `RealtimeState._devices`.

**Cambios necesarios** (`app/services/mqtt/client.py`, `app/schemas/mqtt.py`):

1. Suscribirse con wildcard: `gatewayems/modbus/+/+` (o `.../#` si el segmento del `modbus_id` puede tener más niveles) en vez del tópico fijo actual.
2. En `_on_message`, extraer `modbus_id` y `gateway_uuid` de `message.topic` (mismo patrón que ya usa CRMBackend en `app/core/mqtt.py::_uuid_from_topic` — mirarlo como referencia, no importarlo, es otro repo).
3. Definir la identidad canónica de dispositivo en ApiEMS como **`gateway_uuid`** (no el entero `device_id` del payload) — es el campo estable que sobrevive a que cambie el firmware, el puerto Modbus, o que se reemplace el hardware del medidor. El entero `modbus_id`/`device_id` queda como metadato (a qué equipo dentro del gateway corresponde), no como clave.
4. Tolerancia de formato: si el payload JSON no trae exactamente los campos de `DeviceReading` hoy (p. ej. si en el futuro el script de adquisición cambia de forma), que el error de validación de Pydantic (`mqtt_payload_invalid`, ya logueado) no tumbe el consumidor — eso **ya pasa hoy** (`try/except ValidationError` en `_on_message`), no es un gap nuevo. Lo que sí falta es togear el nuevo dato del tópico junto al payload en un modelo interno propio, por ejemplo:

```python
class NormalizedReading(BaseModel):
    gateway_uuid: str       # de la ruta del tópico — identidad estable
    modbus_id: int          # de la ruta del tópico — metadato
    device_name: str        # del payload
    device_type: str
    timestamp: datetime
    data: dict[str, float]  # tolerante: cualquier variable que venga, se guarda
```

5. Este es el "formato de guardado" pedido: `NormalizedReading` es el contrato interno único que todo lo demás (InfluxDB, `RealtimeState`, WebSocket) consume — venga como venga el payload crudo de MQTT, se normaliza a esto antes de tocar cualquier otra pieza del sistema.

Checkpoint: con el simulador o con datos reales publicando bajo el tópico nuevo con al menos 2 UUIDs de gateway distintos, confirmar que `RealtimeState` los mantiene separados y que InfluxDB los tagea con `device_id=<gateway_uuid>` (revisar `app/repositories/influx.py::_DEVICE_FILTER`, que ya filtra por `r.device_id` — solo cambia qué valor se le pasa, no la lógica de filtrado).

---

### FASE 5 — Integración con CRMBackend: tarifas y habilitación de cliente

**Lo que CRMBackend ya expone y ApiEMS debe empezar a consumir:**

- `GET /tariffs` / `POST /tariffs` / `PATCH /tariffs/{id}` — tarifa mensual (`valor_importado`, `valor_excedente`), un registro por mes, **a nivel de plataforma** (no por cliente — ver gap más abajo). Reemplaza el archivo local `data/tariffs.json` (`TARIFF_CONFIG_PATH`, `app/services/tariff/store.py`).
- `Client.puede_ver_consumo` (booleano en CRMBackend, `app/models/client.py`) — **esta es la respuesta a "si el cliente está habilitado o no"**. ApiEMS no vuelve a preguntarse esto: confía en que si el frontend le llega una sesión válida, CRMBackend ya la dejó pasar.

**Arquitectura de convivencia propuesta:**

1. **Autenticación**: hoy ApiEMS tiene su propio login de un solo usuario (`API_USERNAME`/`API_PASSWORD`, JWT propio, `app/api/v1/auth.py`). Con CRMBackend siendo multi-cliente multi-usuario (`UserRole.ADMIN/TECNICO/CLIENTE`, `AccessScope`), ese login propio de ApiEMS queda obsoleto para el flujo real de un cliente. Propuesta: el frontend se autentica contra **CRMBackend** (que ya sabe quién es el usuario, a qué cliente pertenece y si `puede_ver_consumo`), recibe su JWT de ahí, y ApiEMS pasa a **validar ese mismo JWT** en vez de emitir el suyo — necesita compartir el secreto de firma o, mejor, que CRMBackend exponga un JWKS/endpoint de verificación y ApiEMS lo consuma como issuer externo. Esto es un cambio de confianza, no una feature chica — amerita su propio prompt/fase de implementación cuando se decida encararlo, aquí solo se dejó identificada la dirección.
2. **Tarifas**: nuevo cliente HTTP en ApiEMS (`app/services/tariff/crm_client.py` o similar) que llama `GET /tariffs` de CRMBackend con caché en memoria (la tarifa cambia una vez al mes, no hace falta pegarle a CRM en cada request de costo — mismo espíritu que `cached_energy_total`). `app/services/tariff/store.py` deja de leer el JSON local y pasa a usar este cliente. El shape no es idéntico: `TariffConfig` (ApiEMS) tiene `periods: list[TariffPeriod]` con `cu_cop_kwh`/`cargo_fijo_cop`/`month` (formato `"YYYY-MM"`); `TariffRead` (CRMBackend) tiene `mes: date`/`valor_importado`/`valor_excedente`. Escribir un adaptador que mapee `TariffRead` → `TariffPeriod` (¡ojo con el nombre! `valor_importado` en CRM ≈ `cu_cop_kwh` en ApiEMS, `valor_excedente` ≈ `excedente_cop_kwh` — no son literalmente el mismo campo, `excedente_cop_kwh` en ApiEMS hoy vive en `TariffConfig` a nivel raíz, no por mes; confirmar con el usuario si CRM lo pensó igual antes de asumir el mapeo 1:1).
3. **`GET /tariff` y `PUT /tariff` de ApiEMS**: se deprecan igual que los de la Fase 1 — `Tariff.tsx` pasa a hablar directo con CRMBackend (`/tariffs`) para editar, o sigue hablando con ApiEMS pero ApiEMS hace de proxy hacia CRM (recomendado: proxy, así el frontend de consumo no necesita dos orígenes/dos tokens distintos).
4. **`puede_ver_consumo`**: no es algo que ApiEMS consulte por request (latencia innecesaria) — es una condición que ya se resolvió en el login contra CRMBackend. Si el usuario no puede ver consumo, CRMBackend simplemente no le da un token para esa sección (o el frontend no muestra las pestañas), ApiEMS nunca se entera de la regla, solo atiende requests que ya vienen autorizados.

---

## GAPS PARA PEDIRLE AL CRM (no construir esto en ApiEMS — avisar al usuario)

1. **Sin mapeo `equipment`/`gateway` ↔ `device_id` de InfluxDB.** CRMBackend modela `Client → Site → Gateway → Equipment → Variable`, pero ninguno de esos modelos guarda el `device_id`/`gateway_uuid` que va a aparecer taggeado en InfluxDB. Con la Fase 4 de este documento, `gateway_uuid` sí va a ser el mismo valor que `Gateway.uuid` — pero eso es una convención implícita, no un contrato garantizado por un endpoint. Si se quiere que el frontend resuelva "¿qué measurements de InfluxDB le pertenecen a este cliente?" sin hardcodear la convención, hace falta que CRMBackend confirme (en su documentación o en un campo explícito) que `Gateway.uuid` **es** el device_id de telemetría — o exponga un endpoint tipo `GET /gateways/{id}/telemetry-id`. **Parcialmente cerrado:** `GET /api/v1/fleet` (sección siguiente) entrega `gateway.uuid` junto con el cliente, el sitio, los equipos y los registros que cuelgan de él, así que la resolución ya no requiere hardcodear la cadena. Lo que sigue faltando es que un campo se llame explícitamente "esto es el id de telemetría" — hoy la igualdad `Gateway.uuid == device_id de InfluxDB` sigue siendo una convención, aunque ahora una convención documentada de los dos lados.
2. **Tarifas son de plataforma, no por cliente.** `Tariff` en CRMBackend no tiene `client_id` — una sola tarifa vigente para todos. Si en algún momento distintos clientes tienen contratos/tarifas distintas (razonable en un CRM multi-tenant real), este modelo no lo soporta todavía. Confirmar con el usuario si es intencional (tarifa regulada única, ej. mismo operador CREG para todos) o si falta.
3. ~~**No hay endpoint "todo lo que este usuario puede ver" en un solo tiro.**~~ **RESUELTO en CRMBackend.** Existe `GET /api/v1/fleet` — ver la sección siguiente. La cadena de N llamadas ya no hace falta.

---

## CONTRATO NUEVO EN CRMBackend — `GET /api/v1/fleet`

*Agregado en CRMBackend después de escribir este documento. Resuelve el gap #3 y
cierra parcialmente el #1.*

Devuelve, **en una sola petición**, el árbol completo de todo lo que el que
llama tiene permitido ver: cliente → sitios → gateways → equipos → variables,
anidado. Reemplaza la cadena `/clients/{id}/sites` → `/sites/{id}/gateways` →
`/gateways/{id}/equipment` → `/equipment/{id}/variables`.

```
GET /api/v1/fleet?client_id=<uuid>&nivel=variables&search=&limit=50&offset=0
Authorization: Bearer <token>
If-None-Match: "<hash>"
```

### Parámetros

| Parámetro   | Valores                                          | Default     |
| ----------- | ------------------------------------------------ | ----------- |
| `nivel`     | `sitios` \| `gateways` \| `equipos` \| `variables` | `variables` |
| `client_id` | uuid                                             | —           |
| `search`    | texto, coincide contra `nombre_empresa`          | —           |
| `limit`     | 1..200, **pagina sobre los clientes**            | `50`        |
| `offset`    | ≥ 0                                              | `0`         |

`nivel` corta la profundidad. Una colección por debajo del nivel pedido llega
como **`null`**, no como `[]` — "no lo pediste" y "no hay ninguno" son
respuestas distintas y ApiEMS no debe confundirlas.

### Respuesta

```json
{
  "items": [{
    "id": "…", "nombre_empresa": "Empresa Norte", "estado": "activo",
    "puede_ver_consumo": true,
    "sites": [{
      "id": "…", "nombre": "Planta Norte", "direccion": null,
      "timezone": "America/Bogota", "latitud": null, "longitud": null,
      "gateways": [{
        "id": "…",
        "uuid": "4f50cc89-8030-4654-a2cc-4a1ec34ab37a",
        "numero_serie": "GW-NORTE", "firmware_version": "2.4.1",
        "estado": "online", "ultima_conexion": "2026-08-05T22:41:03Z",
        "ip_actual": "10.0.0.31",
        "intervalo_lectura_segundos": 60, "hora_inicio": 0, "hora_fin": 23,
        "equipment": [{
          "id": "…", "nombre_dispositivo": "Medidor_Principal",
          "device_type": "CT_Meter", "tipo": "analizador",
          "marca": "chint", "modelo": "DTSU666",
          "modbus_id": 11, "transporte": "rtu",
          "variables": [{
            "id": "…", "nombre": "Voltaje A",
            "registro_modbus": 8198,
            "notacion_registro": "hex", "registro_display": "0x2006",
            "tipo_registro": "holding", "tipo_dato": "float32",
            "escala": "1.0000", "unidad": "V"
          }]
        }]
      }]
    }]
  }],
  "total": 1, "limit": 50, "offset": 0
}
```

### Lo que esto habilita en ApiEMS

**Fase 3 (selector de medidores).** El frontend pide `?nivel=equipos` una vez y
arma el selector completo sin `Promise.all` ni N llamadas. `nombre_dispositivo`
es el nombre que el firmware usa para titular la sección del dispositivo, así
que es el mismo string que va a aparecer en la telemetría.

**Fase 4 (identidad de dispositivo).** `gateway.uuid` es el valor que el
firmware usa como identidad en sus tópicos MQTT y en sus llamadas a CRMBackend.
Esto convierte la convención implícita del gap #1 en algo consultable: dado un
`gateway_uuid` que llegó taggeado en InfluxDB, este endpoint dice a qué cliente,
sitio, equipos y registros pertenece. Sigue sin haber un campo llamado
`telemetry_id`, pero ya no hace falta inventarlo — el `uuid` **es** ese valor y
ahora viaja en un contrato documentado.

**Interpretar una lectura.** `registro_modbus` es la dirección numérica real y
`registro_display` la misma escrita en la base en la que se cargó
(`notacion_registro`). Para comparar contra una hoja de datos usar
`registro_display`; para hablar Modbus, `registro_modbus`. `escala` es un
multiplicador y llega como string decimal — parsear con `Decimal`, no con
`float`, si el valor va a multiplicar dinero.

**Fase 5 (`puede_ver_consumo`).** Viene en la raíz de cada cliente, así que si
en algún momento ApiEMS necesita verificarlo en vez de confiar en el login, sale
de acá sin una llamada extra.

### ETag — cómo consultarlo barato

La respuesta trae `ETag`. Mandando ese valor en `If-None-Match` en la siguiente
consulta, CRMBackend responde **304 Not Modified** sin cuerpo si nada cambió.

```python
# app/services/crm/fleet_client.py (sugerido)
headers = {"Authorization": f"Bearer {token}"}
if cached_etag:
    headers["If-None-Match"] = cached_etag

response = await http.get(f"{CRM_BASE_URL}/api/v1/fleet", headers=headers)
if response.status_code == 304:
    return cached_fleet          # nada cambió
cached_etag = response.headers["ETag"]
cached_fleet = response.json()
```

Detalles que importan al implementarlo:

- La huella cubre la **página entera**, `total` incluido: un cliente creado
  fuera de la ventana actual igual invalida el ETag.
- `estado` se deriva de `ultima_conexion` (offline a los 5 minutos sin
  contacto), así que **el ETag cambia cuando un gateway se calla**. Es una señal
  útil, pero significa que la huella no es estable a lo largo del día: no sirve
  como caché de larga duración, sirve para no volver a serializar el árbol.
- Bajar el `nivel` **no** evita eso: `estado` vive en el gateway, así que hasta
  `?nivel=gateways` se invalida cuando uno se cae. Si a ApiEMS le sirviera una
  huella puramente topológica, hay que pedirle a CRMBackend un parámetro para
  excluir la conectividad — hoy no existe.
- Cada combinación de parámetros tiene su propia huella. Cachear por
  `(client_id, nivel, limit, offset)`, no por endpoint.

### Lo que NO trae

- **Ninguna credencial.** Ni `credential`, ni `credential_hash`, ni cuándo se
  emitió. Un listado no lleva secretos.
- **Ni `config_habilitada` ni las versiones de configuración.** Eso vive en
  `GET /api/v1/gateways/{id}/config-status` y es asunto del panel del CRM, no de
  un consumidor de telemetría.
- **Ni tarifas.** Siguen en `/api/v1/tariffs` (Fase 5).

### Autenticación — sin cambios todavía

`GET /fleet` se autentica igual que `/tariffs`: con el JWT de un **usuario** de
CRMBackend, audiencia `crm` o `monitor`. No hay token de servicio
máquina-a-máquina. Un login de rol `cliente` queda confinado a su propia empresa
automáticamente, y pasarle `client_id=<otra empresa>` devuelve una página vacía,
nunca datos ajenos.

Que ApiEMS se autentique como servicio y no como usuario prestado es una pieza
aparte — credencial de servicio con audiencia propia — y está agendada como la
**siguiente fase del lado de CRMBackend**. Hasta entonces, ApiEMS usa una cuenta
de CRMBackend con el rol mínimo que le sirva.

---

## FASES (resumen ejecutable)

**Fase 1** — Consolidar `/reports` como fuente única de periodo fijo; deprecar `/consumption`, `/export`, `/costs/{day..year}`, `/kpis`, `/analytics` bare; migrar frontend. *(Fase 1.5, con aprobación aparte: borrar código deprecado.)*

**Fase 2** — Selector de intervalo (minutos/horas) en `History.tsx`, export CSV respeta ese intervalo.

**Fase 3** — Agregar fase C al catálogo de variables (si el hardware la reporta), selector de medidor global en frontend, tarjetas trifásicas que ocultan fases ausentes en vez de mostrar 0. *El selector se alimenta de `GET /api/v1/fleet?nivel=equipos` de CRMBackend — una llamada, no N.*

**Fase 4** — Ingesta MQTT por wildcard, identidad de dispositivo = `gateway_uuid` (alineado con CRMBackend), modelo `NormalizedReading` como contrato interno único. *`GET /api/v1/fleet` resuelve `gateway_uuid` → cliente/sitio/equipos/registros, con caché por ETag.*

**Fase 5** — Cliente HTTP hacia CRMBackend para tarifas (con caché), adaptador de shape `TariffRead` → `TariffConfig`, deprecar `/tariff` local, dirección propuesta para migrar auth a CRMBackend como issuer (implementación en un prompt aparte, no aquí).

Cada fase: build sin errores, probar contra backend real, confirmar que Dashboard y Alertas (fuera de alcance) siguen intactos, y solo entonces pedir aprobación para la siguiente.
