# Bug conocido (naming, no funcional): `device_id` significa dos cosas distintas según la capa

**Estado:** no rompe nada hoy. Documentado para no perder el contexto si en el futuro hay que tocarlo.

## El problema

- **`DeviceReading.device_id`** (payload MQTT, script `gatewayems`, `app/schemas/mqtt.py`): entero, el **modbus slave id**. Sin cambios — sigue siendo exactamente lo que el script define.
- **`DeviceSnapshot.device_id`** (salida de la API: `/realtime/*`, `/dashboard`, y el parámetro `?device_id=` de `/history`, `/reports`, `/analytics`, `/costs`) — mismo nombre de campo, pero desde el commit `d24023f` (2026-08-06) contiene el **UUID** (`identify_device`), no el entero.

Mismo nombre (`device_id`), significado distinto según si estás mirando el payload crudo de MQTT o la respuesta de la API de ApiEMS.

## Por qué se hizo así

`identify_device` (UUID) es el único tag que InfluxDB tiene y que es único en toda la flota — el `device_id` entero (modbus slave) se repite entre gateways distintos (dos gateways pueden tener ambos un equipo con modbus_id=11). Confirmado en vivo contra InfluxDB real (`192.168.1.26:8086`, measurement `Modbus_Data`, 2026-08-06): cada punto trae `device_id`, `device_name`, `device_type` e `identify_device` como tags separados.

No se renombró el campo de salida de la API (se quedó llamando `device_id` en vez de pasar a `equipment_id` o similar) para no romper el contrato ya en uso (`/history?device_id=`, frontend, etc.) sin que el usuario lo pidiera explícitamente.

## Cuándo esto puede morder

- Si alguien lee el código de `gatewayems` y el de ApiEMS al mismo tiempo, esperando que "device_id" signifique lo mismo en los dos — no es así.
- Si se agrega un endpoint nuevo o un log que mezcle ambos sin dejarlo explícito.
- Si en el futuro se decide que el filtro real debería volver a ser el entero (por lo que sea) — hay que revisar todos los puntos listados abajo, no solo uno.

## Dónde vive cada pieza (si hay que cambiar el nombre)

- `app/repositories/influx.py` — `_DEVICE_FILTER` (filtra `r.identify_device`), `list_device_ids()`.
- `app/services/realtime/state.py` — `RealtimeState.update()`.
- `app/services/websocket/manager.py` — `_data_message()`.
- `app/services/alerts/detector.py` — `check_hourly()`.
- `app/schemas/realtime.py` — `DeviceSnapshot.device_id`.
- Frontend: `frontendEMS/src/api/types.ts` (`DeviceSnapshot`), todo lo que llame `getHistory`/`getReport`/etc. con `device_id`.

## Arreglo propuesto (no aplicado, pendiente de decisión)

Separar los dos conceptos con nombres distintos en toda la API — por ejemplo `equipment_id` (UUID) para lo que hoy es `device_id` en la salida, dejando claro que no tiene relación con el `device_id` (modbus slave) del payload de `gatewayems`. Requiere tocar los mismos archivos listados arriba, más `app/api/v1/*.py` (nombre del query param) y el frontend (`src/api/*.ts`, `src/api/types.ts`).
