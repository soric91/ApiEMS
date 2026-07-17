# PROMPT — Costos en Consumo/Exportación, Analítica y Reportes (incremental sobre prompt_frontend_costos.md)

## ROL

Eres el mismo arquitecto frontend Senior que construyó este dashboard (React + TypeScript + Rsbuild + TailwindCSS) y que ya agregó alertas y las tarjetas de costo del dashboard (`prompt_frontend_costos.md`). Este NO es un proyecto nuevo — es la continuación directa de esa entrega: llevar costos a las páginas de Consumo/Exportación, Analítica y Reportes, que hasta ahora solo mostraban kWh.

---

## REGLA #1 — ANTES DE ESCRIBIR UNA SOLA LÍNEA

Lee `src/api/costs.ts` y `src/api/tariff.ts` (ya existen de la entrega anterior), y las páginas actuales de Consumption, Export, Analytics y Reports antes de tocar nada. Replica el estilo ya establecido — no lo reinventes.

**Prohibido**: reescribir código que ya funciona (dashboard, alertas, tarjetas de costo existentes), renombrar/mover archivos existentes, o "aprovechar y refactorizar" algo sin relación directa con esta feature. Todo lo nuevo se **agrega**. Si tocar un archivo compartido (`src/api/costs.ts`, tipos) es inevitable, el cambio debe ser aditivo — todo lo que ya funcionaba debe seguir funcionando exactamente igual después.

---

## CONTEXTO — qué agregó el backend

Nada de esto es una medición nueva: sigue siendo aritmética sobre `kWh` ya existentes. Son 3 piezas, pensadas exactamente para resolver lo que la entrega anterior dejó corto (solo dashboard, sin rango libre ni por-bucket ni reportes).

### 1. Endpoint nuevo: costo de un rango libre

```
GET /api/v1/costs/range?from={ISO8601}&to={ISO8601}&device_id={opcional}
```

Mismo envelope `{success, message, data}`, misma forma de `data` que `/costs/{day,week,month,year}` (ver `prompt_frontend_costos.md` para el detalle campo por campo — no cambia). Diferencias puntuales:

- `from`/`to` son **obligatorios**, UTC ISO 8601 (mismo formato que ya usan `/consumption`, `/export`, `/reports/custom`).
- `period` en la respuesta viene como `"custom"`.
- `cargo_fijo_included` es **siempre `true`** en este endpoint (a diferencia de `/costs/day` y `/costs/week`) — un rango libre es una elección explícita del usuario, no una vista parcial automática, así que siempre incluye el cargo fijo de cada mes calendario que el rango toca.
- `400` si `from >= to` (mismo patrón de validación que ya manejan `/reports/custom` y las páginas de históricos con rango).
- `401` sin token, igual que el resto de la API.

Este es el endpoint que le faltaba a **Analytics** (comparar rangos arbitrarios) y a cualquier selector de fecha custom en Consumption/Export.

### 2. Campo nuevo en TODOS los `/costs/*`: `series`

`/costs/day`, `/costs/week`, `/costs/month`, `/costs/year` y el nuevo `/costs/range` ahora devuelven un array `series` — **campo agregado, no rompe nada que ya lea `data` como antes**:

```json
{
  "period": "month",
  "consumption_kwh": 126.72,
  "consumption_cost_cop": 114336.92,
  "cargo_fijo_cop": 9486.0,
  "net_cost_cop": ...,
  "cargo_fijo_included": true,
  "months_used": ["2026-06"],
  "stale_months": ["2026-07"],
  "series": [
    {
      "time": "2026-07-01T05:00:00Z",
      "consumption_kwh": 4.2,
      "export_kwh": 5.1,
      "consumption_cost_cop": 3791.98,
      "export_credit_cop": 583.13,
      "net_cost_cop": 3208.85
    }
  ]
}
```

Un punto de `series` por cada bucket — **mismo bucketing que ya usan `/consumption` y `/export`** para ese mismo `period` (hora a hora en `day`, diario en `month`, etc.). Esto es lo que permite graficar costo junto al kWh en Consumption/Export, en vez de solo mostrar el total agregado.

### 3. Campo nuevo en `/reports/*`: `costs`

```
GET /api/v1/reports/{daily,weekly,monthly,yearly,custom}
```

La respuesta ahora trae un campo `costs` embebido, con la MISMA forma que `/costs/{period}` de arriba (incluyendo su propio `series`):

```json
{
  "report_type": "monthly",
  "consumption_kwh": 126.72,
  "export_kwh": 155.39,
  "net_balance_kwh": -28.67,
  "kpis": { ... },
  "max_demand": { ... },
  "load_factor": { ... },
  "base_load": { ... },
  "costs": {
    "period": "month",
    "consumption_cost_cop": 114336.92,
    "cargo_fijo_cop": 9486.0,
    "net_cost_cop": ...,
    "cargo_fijo_included": true,
    "stale_months": [],
    "series": [ ... ]
  },
  "generated_at": "..."
}
```

Importante: `costs.period` usa los valores de `CostPeriod` (`"day"/"week"/"month"/"year"/"custom"`), NO los mismos strings que `report_type` (`"daily"/"weekly"/"monthly"/"yearly"/"custom"`) — son dos convenciones de nombre distintas para el mismo concepto, no lo asumas igual al tipar la respuesta.

Esto significa que **la página de Reportes ya NO necesita una llamada aparte a `/costs/*`** — el costo del reporte viene incluido en la misma respuesta, calculado sobre los mismos puntos que el resto del reporte (sin ida y vuelta extra a la base de datos del lado del backend, y sin request extra del lado del frontend).

**Reglas de dominio (las mismas de siempre, se repiten porque ahora aplican en más lugares):**

1. `net_cost_cop` negativo = a favor del usuario (verde, nunca error).
2. `cargo_fijo_included=false` solo en `day`/`week` — en `range` y en `costs` de reportes mensuales/anuales SIEMPRE es `true`.
3. `stale_months` no vacío = advertencia visible siempre, en cualquier página que muestre costos, no solo el dashboard.

---

## QUÉ CONSTRUIR

### 1. Capa API (ajustes a lo que ya existe)

- `src/api/costs.ts`: agregar `getCostsRange(from, to, deviceId?)` junto al `getCosts(period, deviceId?)` que ya existe.
- Actualizar el tipo de `CostBreakdown`/`CostsResponse` ya definido para incluir `series: CostPoint[]` (nuevo tipo `CostPoint`).
- Actualizar el tipo de la respuesta de `/reports/*` para incluir `costs: CostBreakdown`.

### 2. Consumption / Export

Si estas páginas ya tienen selector de rango (day/week/month/year o fechas custom), usar `getCosts(period)` o `getCostsRange(from, to)` según corresponda y graficar `series` como una línea/barra adicional de costo superpuesta o debajo del gráfico de kWh existente — evaluar si encaja sin reestructurar el gráfico actual antes de agregarlo.

### 3. Analytics

Si Analytics ya tiene comparación de rangos o selector de fechas libre, ahí es donde entra `getCostsRange`. No inventar una sección nueva de "análisis de costos" si no la pidieron — solo conectar el dato donde ya hay un hueco (comparaciones que hoy solo muestran kWh).

### 4. Reports

Dejar de llamar a `/costs/*` por separado en esta página si ya lo estaban haciendo — usar `data.costs` que ya viene en la respuesta de `/reports/*`. Mostrar el desglose de costo como sección del reporte (y en el PDF/Excel si el reporte ya exporta a esos formatos), con las mismas reglas de negativo/cargo fijo/stale de siempre.

---

## FASES

**Fase 1 — Capa de datos**
`getCostsRange` en `src/api/costs.ts`, tipos actualizados (`series` en costos, `costs` en reportes).

**Fase 2 — Reports**
Es la más simple: no requiere nueva llamada, solo leer `data.costs` de la respuesta que ya se pide. Buen punto de partida.

**Fase 3 — Consumption / Export**
Graficar `series` de costo junto al kWh existente.

**Fase 4 — Analytics**
Conectar `getCostsRange` donde ya exista comparación de rangos custom.

Al final de cada fase: build sin errores, probar en navegador contra el backend real corriendo, confirmar que dashboard/alertas/tarjetas de costo/históricos siguen funcionando igual que antes, y solo entonces detenerse a esperar aprobación.
