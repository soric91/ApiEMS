# PROMPT — Agregar Costos/Tarifa al Frontend EMS (incremental)

## ROL

Eres el mismo arquitecto frontend Senior que construyó este dashboard (React + TypeScript + Rsbuild + TailwindCSS) y que ya agregó alertas. Este NO es un proyecto nuevo — es una funcionalidad más sobre un frontend que **ya existe y ya funciona**.

---

## REGLA #1 — ANTES DE ESCRIBIR UNA SOLA LÍNEA

Igual que en la entrega anterior (alertas): lee `src/api/client.ts`, al menos dos módulos existentes de `src/api/*.ts`, los contextos en `src/context/`, y los componentes de `src/components/dashboard/` antes de tocar nada. Replica el estilo ya establecido — no lo reinventes.

**Prohibido**: cambiar el patrón de manejo de estado (Context API, nada de Redux/Zustand), reescribir código que ya funciona, renombrar/mover archivos existentes, o "aprovechar y refactorizar" algo sin relación directa con esta feature. Toda función/componente nuevo se **agrega**, no reemplaza comportamiento existente. Si tocar un archivo compartido es inevitable, el cambio debe ser aditivo y retrocompatible — todo lo que ya funcionaba (alertas, WebSocket, dashboard, históricos) debe seguir funcionando exactamente igual después.

---

## CONTEXTO — la funcionalidad nueva en el backend

El backend agregó costos en pesos colombianos (COP), derivados de la energía que ya mide (`kWh` importados/exportados) más una tarifa configurable — **no es una medición nueva, es aritmética sobre datos que el frontend ya consume** vía `/consumption` y `/export`.

### Endpoint de configuración de tarifa

```
GET /api/v1/tariff
PUT /api/v1/tariff
```

Mismo envelope `{success, message, data}` de siempre. Forma de `data`:

```json
{
  "excedente_cop_kwh": 114.34,
  "umbral_cs_kwh": 130.0,
  "periods": [
    { "month": "2026-01", "cu_cop_kwh": 859.19, "cargo_fijo_cop": 9090.0 },
    { "month": "2026-06", "cu_cop_kwh": 902.28, "cargo_fijo_cop": 9486.0 }
  ]
}
```

- `excedente_cop_kwh`: crédito por kWh exportado a la red.
- `periods`: historial de tarifa por mes calendario (`"YYYY-MM"`, validado server-side — mes inválido como `"2026-13"` devuelve 422). Cada entrada: `cu_cop_kwh` (costo por kWh importado ese mes) + `cargo_fijo_cop` (cargo fijo mensual de la factura).
- **`PUT` reemplaza la configuración COMPLETA, no hace merge parcial.** Para agregar el mes nuevo, el flujo correcto es: `GET` la config actual → agregar/editar la entrada en `periods` en el cliente → `PUT` el objeto completo de vuelta. Si el frontend arma un `PUT` desde cero sin traer primero el histórico existente, **borra los meses anteriores**. Esto es intencional del lado del backend — la responsabilidad de no perder historial es del frontend.

### Endpoint de costos

```
GET /api/v1/costs/day
GET /api/v1/costs/week
GET /api/v1/costs/month
GET /api/v1/costs/year
```

(mismo patrón de query param opcional `device_id` que ya usan `/consumption` y `/export`). Forma de `data`:

```json
{
  "period": "month",
  "device_id": null,
  "period_start": "2026-07-01T05:00:00Z",
  "period_end": "2026-07-17T16:00:35Z",
  "consumption_kwh": 126.72,
  "export_kwh": 155.39,
  "consumption_cost_cop": 116863.31,
  "export_credit_cop": 17767.29,
  "cargo_fijo_cop": 9486.0,
  "net_cost_cop": 108582.01,
  "cargo_fijo_included": true,
  "months_used": ["2026-06"],
  "stale_months": ["2026-07"]
}
```

**Reglas de dominio que la UI debe respetar (no son detalles cosméticos):**

1. **`net_cost_cop` puede ser NEGATIVO** — significa que el crédito por exportación superó el costo de lo importado, es decir, saldo a favor del usuario ese periodo. La UI debe mostrar ese caso como algo positivo (verde, "a tu favor"), nunca como un error o en rojo por ser negativo.
2. **`cargo_fijo_included` es `false` en `day` y `week`** — el cargo fijo es un concepto mensual de factura, no tiene sentido prorratearlo en una consulta de "hoy". Cuando sea `false`, la UI debe indicarlo explícitamente (ej. "no incluye cargo fijo"), no mostrar `cargo_fijo_cop: 0` como si fuera parte real del cálculo.
3. **`stale_months` no vacío = advertencia visible, nunca oculta.** Significa que uno o más meses del rango consultado no tienen tarifa registrada y el backend usó la más reciente anterior como estimado. La UI debe mostrar algo tipo "tarifa estimada con datos de {mes} — actualiza la tarifa de {stale_month}", no debe tratarlo como un valor confiable silencioso. Esto es la misma filosofía de transparencia que ya se aplicó en las alertas (nunca ocultar que un dato es una estimación).
4. Los montos vienen en **COP sin decimales de centavos relevantes** — formatear como moneda colombiana (`Intl.NumberFormat('es-CO', {style:'currency', currency:'COP'})` o equivalente), no mostrar más de 0-2 decimales.

---

## QUÉ CONSTRUIR

### 1. Capa API

- `src/api/tariff.ts`: `getTariff()`, `updateTariff(config)` — mismo patrón de los demás módulos.
- `src/api/costs.ts`: `getCosts(period, deviceId?)` — mismo patrón que ya use `consumption.ts`/`export.ts` para `day/week/month/year`.

Tipos alineados exactamente a las formas de JSON de arriba, agregados donde ya vivan los tipos de la API existente.

### 2. Dónde vive el estado

Costos y tarifa **no son tiempo real** (no hay WebSocket involucrado) — no necesitan un contexto tipo `RealtimeContext`. Evaluar si alcanza con estado local por componente/página (probable que sí, ya que cada card de costo es una llamada independiente a `/costs/{period}`) antes de crear un contexto nuevo. Si varias páginas/widgets necesitan la MISMA tarifa configurada simultáneamente, ahí sí se justifica un contexto ligero — pero no lo asumas de entrada, evalúalo contra lo que exista.

### 3. UI

- **Tarjetas de costo en el dashboard** (junto a las tarjetas de consumo/exportación que ya existen): "Costo de hoy" y/o "Costo del mes", mostrando `net_cost_cop` formateado en COP, con la codificación de color ya establecida en el proyecto (favor/positivo en verde, costo/negativo en el tono que ya se usa para importación). Badge de advertencia si `stale_months` no está vacío.
- **Página o sección de configuración de tarifa** (`/tariff` o similar, o un modal/panel desde configuración): formulario para ver el historial de `periods`, agregar/editar el mes actual (selector de mes, no texto libre), editar `excedente_cop_kwh` y `umbral_cs_kwh`. Al guardar, debe traer primero la config actual (`GET`) y enviar el objeto completo (`PUT`) — nunca un `PUT` armado solo con el mes que se está editando.
- Opcional: agregar el costo como columna/línea extra en las páginas de Consumption/Export ya existentes, si encaja natural sin reestructurar esas páginas.

No es necesario que la edición de tarifa tenga su propia ruta dedicada si ya existe una sección de "Configuración" en la app — usar el patrón que ya exista para configuración/ajustes en vez de inventar uno nuevo.

---

## FASES

**Fase 1 — Capa de datos**
`src/api/tariff.ts`, `src/api/costs.ts`, tipos.

**Fase 2 — Tarjetas de costo en el dashboard**
Lectura únicamente (`GET /costs/*`), sin tocar la edición de tarifa todavía. Manejo correcto de `net_cost_cop` negativo, `cargo_fijo_included`, `stale_months`.

**Fase 3 — Configuración de tarifa**
Formulario de `periods` + `excedente_cop_kwh` + `umbral_cs_kwh`, con el flujo GET-antes-de-PUT para no perder historial.

Al final de cada fase: build sin errores, probar en navegador contra el backend real corriendo, confirmar que dashboard/alertas/históricos siguen funcionando igual que antes, y solo entonces detenerse a esperar aprobación.
