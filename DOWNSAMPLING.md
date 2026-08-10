# Resumen por hora (downsampling) — especificación de trabajo

Documento para retomar esto en otra sesión, con otro agente o dentro de un mes.
Es autocontenido: no hace falta el historial de la conversación donde salió.

---

## El problema, con números medidos

El medidor lee **una vez por segundo** y publica **16 registros** por lectura.

```
16 puntos/segundo × 86.400 s  = 1.382.400 puntos por día, por medidor
                    × 30 días = 41.000.000 puntos al mes, por medidor
```

Medido en producción el 2026-08-10: **14.695.568 puntos** en el bucket
`telemetry_server` (≈ 10,6 días de un medidor). El volumen es **correcto** — no
hay duplicación ni escritura de más, aunque durante el diagnóstico se sospechó
que sí.

Consecuencia: cualquier consulta que barra 30 días recorre decenas de millones
de puntos para dibujar una gráfica de 720. Medido:

| endpoint | tiempo |
|---|---|
| `/analytics/compare` (2 × 30 días) | 60 s → timeout |
| `/costs/day` (1 día) | 58 s |
| `/variables` (`schema.fieldKeys`, 30 días) | 61 s → timeout |
| `/alerts` (banda de 90 días) | 61 s → timeout |
| `/devices` (no toca InfluxDB) | **4 ms** |

El hardware **no** es el límite: el servidor tiene 6 OCPU y 12 GB, InfluxDB
usaba 2 núcleos y 780 MB. Subir de 2 a 6 núcleos no cambió nada, porque
InfluxDB no paraleliza una sola consulta más allá de eso.

Prueba independiente: el gateway (NXP i.MX8M-Plus, Cortex-A53, 1,6 GB) sirve
los mismos valores rápido — porque consulta **rangos cortos** de su bucket
local. Nunca le piden 30 días.

### Lo que ya se hizo, y por qué no alcanza

Commits `d4bbb74` y `b7a79db`:

* `INFLUX_TIMEOUT_MS` configurable (antes 10 s fijos de la librería).
* Manejador de `TimeoutError` → 503 con mensaje **y con cabecera CORS**.
* `VARIABLES_LOOKBACK_DAYS` (30 → 7) y `ALERTS_BASELINE_DAYS` (90 → 30).

Con el timeout en 45 s el panel funciona. **Eso no es una solución**: una
pantalla que tarda 45 segundos se lee como colgada, y con dos medidores son 82
millones de puntos al mes, con diez son 410 millones.

---

## Diseño

Un segundo bucket con un punto por hora. Las consultas largas leen de ahí; el
detalle reciente sigue leyendo el crudo.

```
telemetry_server          crudo, 1 Hz          retención corta (p. ej. 30 d)
telemetry_server_hourly   1 punto por hora     retención larga (p. ej. 2 años)
```

### Qué guarda cada hora

| campo | agregación | para qué |
|---|---|---|
| contadores (`TotWh_import`, `TotWh_export`, `Q1Eq`…`Q4Eq`) | `last` | energía y costos |
| instantáneas (`TotW`, `PhV_*`, `A_*`, `TotPF`, `Hz`…) | `mean`, `min`, `max` | gráficas y bandas |

**`last` sobre contadores no pierde nada.** El total entre dos instantes es la
resta de los dos valores; los puntos intermedios no aportan. Un resumen horario
de un contador monótono da **exactamente** el mismo total que barrer los 3.600
puntos. Esto no es una aproximación.

**`mean` sola sobre instantáneas sí pierde.** Por eso van también `min` y
`max`: sin ellos, la banda [p10, p90] de las alertas se calcularía sobre
promedios, quedaría más angosta de lo real, y el detector marcaría como anómalo
un consumo normal. Una alerta que se equivoca se deja de leer.

### Qué consulta va a qué bucket

La regla vive en un solo lugar (ver Fase 2) y depende del rango pedido:

```
rango ≤ UMBRAL (por defecto 48 h)  → crudo
rango >  UMBRAL                    → resumen
```

Un umbral configurable, no escrito en el código: depende de la frecuencia de
lectura de cada instalación.

---

## Las alertas

Es la parte donde esto se puede hacer mal sin que nadie se entere.

Estado actual (`app/services/alerts/detector.py`):

1. `check_hourly` evalúa **cada lectura MQTT** contra una banda horaria.
2. `hourly_power_baseline` (`app/services/analytics/anomaly.py`) calcula
   [p10, p90] de `TotW` por hora local, sobre `ALERTS_BASELINE_DAYS`.
3. `daily_total_alert` compara el total de ayer contra su banda.

Con el resumen:

**`daily_total_alert`** — sin cambios de semántica. Usa `energy_total`, que con
`last` horario da el mismo número.

**`check_hourly`** — hay que cambiar qué se compara. Hoy compara **una lectura
de un segundo** contra la banda. Si la banda pasa a calcularse sobre agregados
horarios, comparar una muestra suelta contra ella es inconsistente: cualquier
pico dentro de la hora dispara una alerta.

**Decisión tomada:** comparar el **promedio de la hora en curso** contra la
banda de esa hora. Dos razones, y la segunda pesa más que el rendimiento:

* Alertar sobre una muestra de un segundo es ruido. El arranque de un motor
  dispara una alerta que no significa nada.
* "Hoy a las 3 de la tarde estás consumiendo más de lo habitual a esa hora" es
  una afirmación que se puede verificar; "en este segundo pasaste el umbral" no.

Efecto lateral bueno: el detector deja de necesitar el histórico completo en
cada mensaje MQTT.

---

## Fases

Cada una termina con `uv run pytest -q`, `uv run ruff check app tests` y
`uv run pyright app tests` en verde, y con su commit.

### Fase 1 · La tarea que llena el resumen

* Definir la tarea Flux que agrega por hora del crudo al bucket resumido.
  Guardar el `.flux` versionado (sugerencia: `infra/downsampling.flux`), no
  solo creado a mano en la interfaz de InfluxDB — si vive únicamente en el
  servidor, se pierde al recrear el contenedor y nadie sabe qué hacía.
* Tarea aparte para contadores (`last`) y para instantáneas (`mean/min/max`),
  o una sola con dos ramas. Los `_field` de contadores salen del catálogo del
  CRM (`acumulativa`), no de una lista escrita a mano.
* Documentar cómo se crea (`influx task create`) y cómo se rellena el histórico
  ya existente de una sola vez.
* **Verificación obligatoria:** para un día cualquiera, el `energy_total` desde
  el crudo y desde el resumen tienen que dar el **mismo** número. Si difieren,
  la tarea está mal y todo lo que sigue hereda el error.

### Fase 2 · El repositorio elige el bucket

* `INFLUX_BUCKET_HOURLY` y `RAW_WINDOW_HOURS` en `app/core/config.py`.
* En `app/repositories/influx.py`, un único punto que decida bucket y campo
  según el rango. **No repetir la decisión en cada método** — con seis métodos
  decidiendo por su cuenta, alguno va a quedar leyendo el bucket equivocado y
  el síntoma será un número raro, no un error.
* Métodos afectados: `instant_series`, `instant_reduce`, `energy_total`,
  `energy_series`, `field_keys`.
* `field_keys` merece atención propia: `schema.fieldKeys` **con predicado no
  lee el índice** pese al nombre — por debajo hace
  `range |> filter |> keys |> distinct`, o sea barre datos. Contra el bucket
  resumido cuesta una fracción.
* Tests: que un rango corto vaya al crudo y uno largo al resumen; que el borde
  exacto del umbral esté cubierto.

### Fase 3 · Alertas sobre agregados

* `hourly_power_baseline` lee del resumen y arma la banda con `mean/min/max`.
* `check_hourly` compara el promedio de la hora en curso.
* Tests: que una lectura puntual fuera de rango **no** dispare alerta si el
  promedio de la hora es normal; que un promedio horario anómalo **sí** la
  dispare.

### Fase 4 · Retención

* Retención corta en el crudo, larga en el resumen.
* Antes de acortar la del crudo, confirmar que el resumen tiene el histórico
  rellenado. Al revés se pierden datos y no hay vuelta atrás.

---

## Objetivos medibles

| | antes | objetivo |
|---|---|---|
| Gráfica de 30 días | ~41 M de puntos, 45 s | ~720 filas, < 1 s |
| `/analytics/compare` | timeout | < 2 s |
| `/variables` | 61 s | < 1 s |
| `INFLUX_TIMEOUT_MS` | 45.000 | volver a 10.000 |

El último es el que confirma que funcionó: si con 10 segundos de timeout todo
responde, el problema se arregló de verdad. Mientras haga falta un timeout
largo, sigue tapado.

---

## Advertencias del terreno

Se pagaron caras hoy. Vale releerlas antes de tocar el servidor.

**El timeout de adentro tiene que ser más corto que el de afuera.** nginx corta
a los 60 s. Con `INFLUX_TIMEOUT_MS=60000` gana nginx, devuelve un **504 sin
cabecera CORS**, y el navegador lo reporta como problema de permisos. Se pierden
horas mirando `CORS_ORIGINS` por un fallo que no tiene nada que ver.

**Una excepción no controlada sale sin CORS.** `ServerErrorMiddleware` está por
**fuera** del middleware de CORS, así que un 500 sin manejar nunca lleva la
cabecera. Cualquier fallo nuevo va a parecer un problema de CORS. Por eso existe
el manejador de `TimeoutError`.

**`docker restart` no relee el `.env`.** Las variables se fijan al **crear** el
contenedor. Hay que `docker compose up -d --force-recreate`. Comprobar siempre
con `docker exec <contenedor> env | grep <VARIABLE>` antes de concluir nada.

**El contenedor de ApiEMS corre en red `host`.** No hay DNS de Docker: el
nombre `influx_server` no resuelve. La URL correcta es `http://localhost:8086`.
Apuntar al dominio público desde adentro hace que el tráfico dé la vuelta por el
router y no vuelva — se manifiesta como timeout, no como error de conexión.

**Dos `device_name` para el mismo `identify_device` parten la serie.**
`device_name` es un tag; dos valores son dos series. Ninguna consulta hace
`group()`, así que `_records` aplana ambas y los puntos se duplican. En
contadores, `difference()` cuenta cada salto entre series como consumo y **el
total sale inflado**, sin ningún error. Pasa al renombrar un equipo en el CRM.
Verificar con `schema.tagValues(tag: "device_name")` antes de dar por buenos los
totales, y considerar `group()` o `drop(columns:["device_name"])` en la Fase 2.

---

## Lo que NO hay que hacer

* **No borrar el bucket.** El volumen es correcto. Durante el diagnóstico se
  creyó que sobraban datos y se estuvo cerca de vaciarlo; era un error de
  cálculo (se supuso 10 s entre lecturas en vez de 1 s).
* **No agrandar el servidor.** Ya se probó: de 2 a 6 núcleos, sin cambio.
* **No subir más el timeout.** 45 s ya es inaceptable para una pantalla.
