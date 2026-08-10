# Por qué el panel se puso lento, y en qué orden arreglarlo

Documento autocontenido: no hace falta el historial de la conversación donde
salió. Complementa `DOWNSAMPLING.md`, que cubre solo la última fase.

**Leer la Fase 0 antes que nada.** No es rendimiento: es una fuga de datos entre
empresas que apareció mientras se investigaba la lentitud.

---

## Qué cambió respecto de cuando andaba rápido

Comparación de `git diff 0cbe57b^ HEAD -- app/repositories/influx.py`, donde
`0cbe57b` es el primer commit de la reforma de identidad.

### 1. `field_keys` no existía

Es un método **nuevo**, agregado junto con `GET /variables`. Antes el panel
tenía la lista de variables escrita a mano y no consultaba InfluxDB para saber
qué graficar.

Es la consulta más cara del proyecto: `schema.fieldKeys` **con predicado no lee
el índice** pese al nombre — por debajo hace `range |> filter |> keys |>
distinct`, o sea barre datos. Corre en cada carga del panel.

### 2. El filtro de equipos cambió de forma

| | antes | ahora |
|---|---|---|
| con `device_id` | `r.identify_device == _device_id` | **igual** |
| sin `device_id` | *sin filtro* (había un solo cliente) | `contains(value: r.identify_device, set: _devices)` |

`==` sobre un tag se empuja al índice y descarta series **antes de leerlas**.
`contains()` es una función por fila: Flux la evalúa después de leer, así que
lee todo y filtra en memoria.

### 3. Y el volumen creció

El medidor lee **1 vez por segundo** con **16 registros**: 1,38 M de puntos por
día, 41 M al mes, por medidor. Medido el 2026-08-10: **14.695.568 puntos** en
`telemetry_server` (≈ 10,6 días). **El volumen es correcto** — no hay
duplicación, aunque durante el diagnóstico se creyó que sí.

Los dos cambios de código son de julio; lo que cambió en agosto es el volumen.
Con 100.000 puntos ninguno se nota. Con 14,7 millones, los dos duelen.

### Tiempos medidos en producción

| endpoint | tiempo |
|---|---|
| `/analytics/compare` (2 × 30 días) | 60 s → timeout |
| `/costs/day` (1 día) | 58 s |
| `/variables` | 61 s → timeout |
| `/alerts` (banda de 90 días) | 61 s → timeout |
| `/devices` (no toca InfluxDB) | **4 ms** |

Hardware descartado: 6 OCPU y 12 GB; InfluxDB usaba 2 núcleos y 780 MB. Se pasó
de 2 a 6 núcleos **sin ningún cambio** — InfluxDB no paraleliza una consulta más
allá de eso. El gateway (Cortex-A53, 1,6 GB) sirve los mismos valores rápido
porque consulta rangos cortos de su bucket local.

---

## Fase 0 · La clave del caché filtra datos entre empresas

**Prioridad máxima. No es rendimiento, es aislamiento.**

### El defecto

`app/core/cache.py`, decorador `@cached`. La clave se arma con `str()` de cada
argumento:

```python
key_parts = [str(_normalize(a, ttl_seconds)) for a in args]
```

El primer argumento de las funciones cacheadas es el repositorio. Un objeto sin
`__str__` propio se convierte en `<ScopedInfluxRepository object at 0x7f3a…>`:
**su dirección de memoria**.

Y `app/dependencies/influx.py` crea uno **nuevo en cada petición**:

```python
def get_influx_repository(fleet: CurrentFleet, inner=Depends(...)) -> ScopedInfluxRepository:
    return ScopedInfluxRepository(inner, fleet.device_ids)
```

Cuando la petición termina, el objeto se libera y su dirección queda libre. La
siguiente petición —de **otra empresa**— puede recibir un objeto en esa misma
dirección, generar la misma clave, y llevarse el resultado cacheado del cliente
anterior.

No es teórico. Está escrito en `tests/conftest.py`:

> *"sin esto, un objeto FakeInfluxRepository de un test previo cuyo `id()` de
> memoria fue reutilizado por el GC podría 'acertar' una clave de caché ajena"*

Alguien lo vio en los tests, puso `clear_all_caches()` para que no molestara
ahí, y no siguió el hilo. **En producción no hay nada que limpie el caché entre
peticiones.**

### Qué se puede filtrar

`cached_energy_total`, `cached_energy_series`, `cached_instant_series` — consumo,
series y costos de una empresa servidos a otra. Requiere que coincidan la
dirección **y** el resto de los argumentos (variable, rango, agregación). Con
dos clientes es improbable; con veinte pidiendo los rangos por defecto, no.

### El arreglo

La clave tiene que describir **de quién son los datos**, no dónde está el
objeto. Opciones, de mejor a peor:

1. `ScopedInfluxRepository` expone una propiedad estable de identidad —el
   `frozenset` de equipos, o mejor el `client_id`— y `_normalize` la usa.
2. Un `__str__` en `ScopedInfluxRepository` que devuelva esa identidad.

La 1 es preferible: hace explícito que la identidad es parte del contrato del
caché, en vez de esconderlo en un método mágico que alguien puede borrar.

### Tests que tienen que existir

* Dos repositorios de **empresas distintas** con los mismos argumentos **no**
  comparten entrada de caché. Se verifica mutando: con la clave por dirección,
  este test tiene que fallar.
* Dos repositorios de la **misma** empresa **sí** la comparten — si no, el
  caché no sirve para nada.
* Y quitar `clear_all_caches()` del `conftest`: existía para tapar justo esto.
  Si con la clave arreglada los tests siguen pasando sin él, está bien resuelto.

---

## Fase 1 · Cachear `field_keys`

**Solo después de la Fase 0.** Cachear con la clave rota multiplica la fuga.

`GET /variables` responde dos cosas: qué variables declara el CRM —ya viene de
`fleet.variables`, cacheado 5 minutos, gratis— y cuáles tienen datos, que es la
consulta cara.

Esa segunda respuesta **no cambia entre cargas del panel**. Que `PhV_phsC` tenga
datos es cierto ahora y dentro de una hora.

* `@cached` sobre `field_keys` con TTL de una hora (configurable).
* La primera carga después de reiniciar paga el costo; el resto es instantáneo.
* Una variable recién dada de alta aparece hasta una hora tarde — aceptable para
  algo que se carga a mano en el CRM. Documentarlo donde se ve, no solo acá.

Es la mejor relación esfuerzo/beneficio de todo el documento: quita la consulta
más cara del panel sin tocar arquitectura.

---

## Fase 2 · Que `contains` vuelva a ser `==` donde se pueda

El panel ya elige **siempre** un medidor (selector encadenado de gateway y
equipo, `DeviceContext` selecciona el primero al cargar). O sea, la mayoría de
las consultas *podrían* llevar `device_id` y usar el filtro indexado.

* Revisar qué endpoints pierden el `device_id` por el camino y por qué.
* Donde el recorte por conjunto sea inevitable (varios equipos a la vez),
  medir si conviene una consulta por equipo en paralelo en vez de un `contains`
  sobre todos.
* **No quitar el recorte.** Existe para impedir que un cliente vea datos de
  otro; es la misma clase de problema que la Fase 0.

---

## Fase 3 · Resumen por hora

Ver `DOWNSAMPLING.md`. Es lo único que arregla el fondo —41 M de puntos al mes
por medidor— pero las fases 1 y 2 pueden devolver tiempos usables sin tocar la
arquitectura, así que van antes.

---

## Criterio de terminado

Volver `INFLUX_TIMEOUT_MS` a **10.000** (hoy está en 45.000) y que el panel
responda. Mientras haga falta un timeout largo, el problema está tapado.

| | hoy | objetivo |
|---|---|---|
| `/variables` | 61 s | < 1 s |
| `/analytics/compare` | timeout | < 2 s |
| `/costs/day` | 58 s | < 2 s |

---

## Advertencias del terreno

Se pagaron caras. Vale releerlas antes de tocar el servidor.

**El timeout de adentro tiene que ser más corto que el de afuera.** nginx corta
a los 60 s. Con `INFLUX_TIMEOUT_MS=60000` gana nginx, devuelve un **504 sin
cabecera CORS**, y el navegador lo reporta como problema de permisos. Se
perdieron horas mirando `CORS_ORIGINS` por un fallo que no tenía relación.

**Una excepción no controlada sale sin CORS.** `ServerErrorMiddleware` está por
**fuera** del middleware de CORS: un 500 sin manejar nunca lleva la cabecera.
Por eso existe el manejador de `TimeoutError` que devuelve 503.

**`docker restart` no relee el `.env`.** Las variables se fijan al **crear** el
contenedor. Hace falta `docker compose up -d --force-recreate`. Comprobar con
`docker exec <contenedor> env | grep <VARIABLE>` antes de concluir nada.

**ApiEMS corre en red `host`.** No hay DNS de Docker: `influx_server` no
resuelve. Va `http://localhost:8086`. Apuntar al dominio público desde adentro
hace que el tráfico dé la vuelta por el router y no vuelva — se manifiesta como
timeout, no como error de conexión.

**Dos `device_name` para el mismo `identify_device` parten la serie.**
`device_name` es un tag; dos valores son dos series. Ninguna consulta hace
`group()`, así que `_records` aplana ambas y los puntos se duplican. En
contadores, `difference()` cuenta cada salto entre series como consumo y el
total **sale inflado sin ningún error**. Pasa al renombrar un equipo en el CRM.
Verificar con `schema.tagValues(tag: "device_name")` antes de dar por buenos los
totales.

---

## Lo que NO hay que hacer

* **No borrar el bucket.** El volumen es correcto: 1 medidor a 1 Hz con 16
  registros son 41 M de puntos al mes. Durante el diagnóstico se supuso una
  lectura cada 10 segundos, se concluyó que sobraban datos, y se estuvo cerca
  de vaciarlo.
* **No agrandar el servidor.** Ya se probó: de 2 a 6 núcleos, sin cambio.
* **No subir más el timeout.** 45 s ya es inaceptable para una pantalla.
* **No quitar el recorte por empresa** para ganar velocidad.
