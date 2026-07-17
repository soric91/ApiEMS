# PROMPT — Agregar Alertas de Consumo al Frontend EMS (incremental)

## ROL

Eres el mismo arquitecto frontend Senior que construyó este dashboard (React + TypeScript + Rsbuild + TailwindCSS). Este NO es un proyecto nuevo — es una funcionalidad nueva sobre un frontend que **ya existe y ya funciona** contra la API real.

---

## REGLA #1 — ANTES DE ESCRIBIR UNA SOLA LÍNEA

**El frontend ya está construido y consumiendo los endpoints anteriores (auth, dashboard, realtime, history, consumption, export, analytics, kpis, reports + WebSocket).** No es un lienzo en blanco.

Antes de tocar código:

1. Lee `src/api/client.ts` y al menos dos módulos existentes de `src/api/*.ts` — replica exactamente su estilo (manejo de errores, forma de exportar funciones, tipado).
2. Lee `src/api/websocket.ts` completo — vas a **extender** su manejo de mensajes, no reescribirlo.
3. Lee `src/context/*` existentes — identifica a qué nivel (global vs. por página) vive cada uno, y sigue el mismo criterio para lo nuevo.
4. Lee los componentes de `src/components/dashboard/` y `src/components/layout/` para igualar convenciones de nombres, props y estilo visual ya establecido.

**Prohibido**: reescribir el cliente WebSocket desde cero, cambiar el patrón de manejo de estado (sigue siendo Context API, no introduzcas Redux/Zustand/otra librería), renombrar o mover archivos existentes, o "aprovechar y refactorizar" código que ya funciona y no tiene relación con esta feature. Si algo existente te parece mejorable, no lo toques salvo que sea estrictamente necesario para esta feature — anótalo aparte, no lo cambies sin que se pida.

Toda función/componente nuevo debe integrarse **agregando**, no modificando comportamiento de lo que ya funciona. Si tocar un archivo compartido (ej. `websocket.ts`) es inevitable, el cambio debe ser aditivo y retrocompatible: los mensajes `data`/`subscribed`/`unsubscribed`/`pong`/`error` que ya maneja deben seguir funcionando exactamente igual.

---

## CONTEXTO — la funcionalidad nueva en el backend

El backend agregó un sistema de alertas de consumo **sin machine learning**: compara lecturas contra bandas de percentiles (P10-P90) calculadas sobre el historial real (Polars), con clasificación tipo Tukey. Ya está en producción, probado con más de 50 días de datos reales.

### Endpoint nuevo

```
GET /api/v1/alerts?device_id=&limit=50
```

Respuesta (mismo envelope `{success, message, data}` de siempre):

```json
{
  "success": true,
  "message": "OK",
  "data": {
    "recent": [
      {
        "kind": "hourly_power",
        "severity": "high",
        "device_id": "11",
        "variable": "POWER_ACTIVE_INST_TOTAL",
        "value": 5000.0,
        "expected_low": 90.0,
        "expected_high": 115.0,
        "bucket": 10,
        "timestamp": "2026-04-20T10:00:00Z",
        "message": "Potencia importada inusual a las 10:00 (5000 W; lo típico para esta hora es entre 90 y 115 W)"
      }
    ],
    "daily_total": null
  }
}
```

Campos:
- `kind`: `"hourly_power"` (anomalía en tiempo real, generada por cada mensaje MQTT) | `"daily_total"` (comparación del último día completo contra su banda de día de semana, calculada bajo demanda al llamar el endpoint)
- `severity`: `"moderate"` | `"high"` — qué tan lejos cae el valor de la banda esperada
- `bucket`: hora local 0-23 si `kind="hourly_power"`; día de semana 0=lunes..6=domingo si `kind="daily_total"`
- `expected_low`/`expected_high`: la banda P10/P90 contra la que se comparó
- `daily_total` puede venir **`null`**: o bien ayer estuvo dentro de lo normal, o no hay suficiente historial todavía para ese día de semana — la UI no debe distinguir estos dos casos (el backend no los distingue), simplemente no mostrar nada si es `null`.
- `recent` es una lista en memoria del backend (máx. 200, se pierde si el backend reinicia) — **no es historial persistente**, es "lo que ha pasado desde que el backend arrancó".

### WebSocket — mensaje nuevo

El cliente WS ya existente maneja mensajes `data`/`subscribed`/`unsubscribed`/`pong`/`error`. Se agrega un tipo nuevo:

```json
{
  "type": "alert",
  "kind": "hourly_power",
  "severity": "high",
  "device_id": "11",
  "variable": "POWER_ACTIVE_INST_TOTAL",
  "value": 5000.0,
  "expected_low": 90.0,
  "expected_high": 115.0,
  "bucket": 10,
  "timestamp": "2026-04-20T10:00:00Z",
  "message": "..."
}
```

**Diferencia importante de comportamiento**: los mensajes `data` solo llegan al cliente si está suscrito a esa variable específica. Los mensajes `alert` llegan a **TODOS los clientes conectados, sin importar a qué variable estén suscritos (o si no están suscritos a ninguna)**. El cliente WS debe estar preparado para recibir un `alert` en cualquier momento, independiente del estado de suscripción — no asumir que solo llegan mensajes relacionados con la variable activa.

---

## QUÉ CONSTRUIR

### 1. Capa API — `src/api/alerts.ts` (archivo nuevo)

Función `getAlerts(params?: { deviceId?: string; limit?: number })` que llama a `GET /api/v1/alerts`, usando la MISMA instancia de `client.ts` que ya usan los demás módulos. Tipos en el mismo lugar donde ya viven los tipos de la API (extender `src/api/types.ts` si así está organizado, o seguir el patrón que ya exista).

### 2. Cliente WebSocket — extender `src/api/websocket.ts`

Agregar el caso `"alert"` al switch/manejador de mensajes existente, sin alterar el manejo de los demás tipos. Exponer un callback/evento nuevo (ej. `onAlert`) siguiendo el mismo patrón que ya use el cliente para `onData` o equivalente.

### 3. Contexto — decidir el nivel correcto

Las alertas son relevantes en **todo el dashboard autenticado**, no en una sola página — igual de "global dentro del área autenticada" que `RealtimeContext`. Evaluar: ¿se agrega a `RealtimeContext` (ya tiene la conexión WS abierta) o se crea `AlertsContext` separado que consume la misma conexión WS ya existente? Preferir **reutilizar la conexión WS que ya administra `RealtimeContext`** en vez de abrir una segunda — el backend ya expone un solo `/ws`, no crear una segunda conexión. Si `AlertsContext` se crea aparte, debe recibir los eventos de alerta a través del cliente WS ya compartido, no instanciar su propio WebSocket.

Estado a exponer: lista de alertas recibidas en la sesión actual (vía WS, en tiempo real) + fetch inicial de `GET /alerts` al montar (para no perder lo que pasó antes de que el usuario abriera la pestaña) + contador de no leídas.

### 4. UI

- **Indicador en el Topbar** (ya existe la Topbar): ícono de campana (`lucide-react`) con badge de conteo cuando hay alertas no vistas, animación sutil (framer-motion) al llegar una nueva por WebSocket — coherente con el resto de indicadores "en vivo" ya definidos en el diseño original.
- **Panel/dropdown de alertas recientes** al hacer click en la campana: lista de `recent`, cada ítem con color según `severity` (usar la misma codificación de color por significado ya establecida en el proyecto — no inventar una paleta nueva para esto) y el `message` que ya viene armado desde el backend (no reconstruir el texto en el frontend).
- **Toast/notificación efímera** cuando llega una alerta nueva por WebSocket mientras el usuario está en cualquier página del dashboard (no solo si tiene el panel abierto).
- Opcional, solo si encaja natural: mostrar `daily_total` (si no es `null`) como una tarjeta destacada en `/dashboard`, ya que es información de "cómo estuvo ayer" con valor de un vistazo.

No es necesario crear una página `/alerts` dedicada para esta primera entrega — el panel del Topbar cubre el caso de uso. Si el diseño ya tiene espacio natural para una vista de historial más completa, se puede agregar, pero no es requisito.

---

## FASES (igual de disciplinado, solo que más corto por ser incremental)

**Fase 1 — Capa de datos**
`src/api/alerts.ts`, tipos, extensión de `src/api/websocket.ts` con el caso `alert` (verificar que los mensajes existentes `data`/`subscribed`/etc. siguen funcionando exactamente igual — probar en el navegador contra el backend real, no asumir).

**Fase 2 — Contexto**
Wiring del estado de alertas (fetch inicial + eventos WS en vivo), reutilizando la conexión existente.

**Fase 3 — UI**
Campana + badge + panel + toast.

Al final de cada fase: build sin errores, probar en navegador contra el backend real corriendo (`uv run uvicorn app.main:app --reload` en el backend), confirmar que **ninguna funcionalidad previa se rompió** (dashboard en vivo, históricos, etc. siguen funcionando idénticos a antes), y solo entonces detenerse a esperar aprobación.
