# PROMPT — Resumen general exportable en Analítica (incremental)

## ROL

Eres el mismo arquitecto frontend Senior que construyó este dashboard (React + TypeScript + Rsbuild + TailwindCSS) y que ya agregó alertas y costos. Este NO es un proyecto nuevo — es una funcionalidad más sobre un frontend que **ya existe y ya funciona**.

---

## REGLA #1 — ANTES DE ESCRIBIR UNA SOLA LÍNEA

Lee la página de Analítica actual, `src/api/analytics.ts` (o donde vivan esas llamadas), y si el proyecto ya exporta algo a PDF en cualquier otra página, lee cómo lo hace (qué librería usa) **antes de agregar una nueva**. Replica el estilo ya establecido — no lo reinventes.

**Prohibido**: reescribir código que ya funciona, renombrar/mover archivos existentes, meter una segunda librería de PDF si ya hay una en el proyecto, o "aprovechar y refactorizar" algo sin relación directa con esta feature. Todo lo nuevo se **agrega**.

---

## CONTEXTO — el endpoint nuevo

```
GET /api/v1/analytics/summary?from={opcional}&to={opcional}&device_id={opcional}
```

**Default distinto al resto de `/analytics/*`**: si no mandás `from`/`to`, el rango por defecto es **los últimos 30 días** (no "hoy" como los demás endpoints de analítica) — necesita varias semanas de muestras por hora para que "hora pico" signifique algo real, no el ruido de un solo día.

Respuesta real (mismo envelope `{success, message, data}` de siempre):

```json
{
  "period_start": "2026-06-19T16:42:35Z",
  "period_end": "2026-07-19T16:42:35Z",
  "device_id": null,
  "consumption_daily_kwh": 4.39,
  "consumption_weekly_kwh": 49.62,
  "consumption_monthly_kwh": 142.82,
  "export_daily_kwh": 2.76,
  "export_monthly_kwh": 174.15,
  "hourly_profile": [
    { "hour": 0, "power_avg_w": 558.59, "power_max_w": 792.05, "power_min_w": 368.77, "sample_count": 17 },
    { "hour": 1, "power_avg_w": 547.70, "power_max_w": 694.06, "power_min_w": 385.62, "sample_count": 17 }
  ],
  "peak_consumption_hour": 3,
  "peak_export_hour": 16,
  "efficiency": {
    "tariff_month": "2026-06",
    "stale": true,
    "cu_cop_kwh": 902.28,
    "excedente_cop_kwh": 114.34,
    "export_kwh": 174.15,
    "potential_savings_cop": 137219.75
  }
}
```

**Reglas de dominio que la UI debe respetar (no son detalles cosméticos):**

1. **`hourly_profile` tiene 24 puntos (hora 0-23), `power_avg_w` puede ser positivo (importando de la red) o negativo (exportando/generando)**. Graficarlo como barras con dos colores — positivo en el color que ya usan para importación, negativo en el color que ya usan para exportación/favor. NO es un gráfico nuevo desde cero si la página de Consumo/Exportación ya tiene un estilo de barras import/export: reusar esa paleta.
2. **`peak_consumption_hour` y `peak_export_hour` pueden venir en `null`** — si el rango no tuvo ninguna hora con ese signo (ej. una casa que nunca exportó en 30 días → `peak_export_hour: null`). No asumir que siempre hay valor; si es `null`, no resaltar nada en el gráfico para esa métrica y omitir esa línea del resumen en vez de mostrar "hora null" o "hora 0" por defecto.
3. **`efficiency` puede venir en `null`** — pasa cuando no hay NINGUNA tarifa registrada todavía (ni siquiera una antigua de la cual estimar). Si es `null`, no mostrar la sección de recomendación de eficiencia en absoluto (ni "$0 de ahorro" — eso sería inventar un dato).
4. **`efficiency.potential_savings_cop` es una COTA SUPERIOR ilustrativa, no una promesa de ahorro exacto** — asume que TODA la energía exportada del mes se pudo haber autoconsumido en vez de exportado, algo que en la práctica depende de qué aparatos se puedan mover a esa hora. El texto en la UI debe dejar esto claro (ej. "podrías haber ahorrado hasta ~$137.220 COP este mes si hubieras desplazado consumo a tus horas de mayor generación" — con "hasta", no "vas a ahorrar"). Mismo espíritu de transparencia que ya se usa con `stale_months` en costos: nunca presentar una estimación como un hecho.
5. **`efficiency.stale: true`** = la tarifa usada para el cálculo es de un mes anterior (`tariff_month`), no la del mes actual — mismo patrón visual de advertencia que ya existe para `stale_months` en `/costs/*`.
6. Igual que en costos: montos en COP formateados con `Intl.NumberFormat('es-CO', {style:'currency', currency:'COP'})` o equivalente.

---

## QUÉ CONSTRUIR

### 1. Capa API
`getAnalyticsSummary(from?, to?, deviceId?)` en el mismo módulo donde ya viven las demás llamadas de analítica. Tipos alineados a la forma de arriba.

### 2. Sección de resumen en Analítica
- Cards de consumo/exportación diario-semanal-mensual (mismo estilo que el dashboard).
- Gráfico de barras de `hourly_profile` (24 horas), resaltando visualmente `peak_consumption_hour` y `peak_export_hour` cuando no son `null` (ej. un borde o marcador distinto en esa barra).
- Card de "recomendación de eficiencia" — solo si `efficiency` no es `null` — con el texto en tono de estimado (ver regla 4) y el badge de "tarifa desactualizada" si `stale` es `true`.

### 3. Exportar a PDF
Botón "Exportar resumen" en Analítica que renderiza esta sección (cards + gráfico) a PDF. Si el proyecto YA tiene una librería de export a PDF usada en otra página, reusarla exactamente igual. Si no existe ninguna, la más liviana que ya se pueda integrar sin fricción con el stack actual (evaluar `jsPDF` + `html2canvas` o similar, la que joda menos con Rsbuild) — pero confirmá primero que de verdad no hay nada ya montado antes de sumar la dependencia.

---

## FASES

**Fase 1 — Capa de datos**
`getAnalyticsSummary` + tipos.

**Fase 2 — UI del resumen**
Cards + gráfico horario con picos resaltados + card de eficiencia condicional. Sin botón de exportar todavía.

**Fase 3 — Exportar a PDF**
Botón de exportar, reusando librería existente si la hay.

Al final de cada fase: build sin errores, probar en navegador contra el backend real corriendo, confirmar que dashboard/alertas/costos/históricos siguen funcionando igual que antes, y solo entonces detenerse a esperar aprobación.
