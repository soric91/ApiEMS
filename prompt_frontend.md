# PROMPT — Frontend EMS Residencial (React + Rsbuild + TailwindCSS)

## ROL

Eres un arquitecto de software Senior especializado en React, TypeScript, dashboards de datos en tiempo real y visualización de energía. También tienes ojo de diseñador: sabes construir interfaces modernas, animadas e interactivas sin caer en excesos.

NO quiero un ejemplo. NO quiero una demo. Quiero un proyecto listo para producción siguiendo buenas prácticas.

---

## CONTEXTO DEL SISTEMA (LEER ANTES DE ESCRIBIR CÓDIGO)

El backend (`ApiEMS`, FastAPI) ya existe, está completo y corriendo. Este frontend es su consumidor. **El contrato de la API es la fuente de verdad — no inventes campos ni endpoints.**

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

**Antes de escribir cada módulo de `src/api/`, consulta el spec OpenAPI real** (via `/docs` o `/openapi.json`) para los schemas exactos de request/response de ese dominio. Los endpoints y su forma general están listados abajo, pero los tipos de datos exactos (campos, nullability) deben verificarse contra el spec real, no asumirse.

### Modelo de medición (dominio — respetar en toda la UI)

La residencia tiene generación solar, pero **el único punto de medición es el medidor bidireccional en la acometida**. El sistema NO mide generación solar bruta ni consumo bruto de la casa — solo el balance neto con la red:

- `POWER_ACTIVE_TOTAL_POS` → energía **importada** de la red (contador acumulativo, kWh)
- `POWER_ACTIVE_TOTAL_NEG` → energía **exportada** a la red (contador acumulativo, kWh)
- `POWER_ACTIVE_INST_TOTAL`, `VOLTAGE_A/B`, `CURRENT_A/B`, `FACTOR_POTENCIA_TOTAL` → instantáneas

**La UI nunca debe mostrar ni etiquetar "generación solar" o "autoconsumo"** — esos datos no existen en el backend. Usa siempre "importado / exportado / balance neto". `POWER_ACTIVE_INST_TOTAL` negativo = exportando (excedente solar cubre y sobra); positivo = importando de la red.

Catálogo completo de variables (usadas en `/history` y en la suscripción WebSocket):

```
CURRENT_A, CURRENT_B, VOLTAGE_A, VOLTAGE_B,
POWER_ACTIVE_INST_A, POWER_ACTIVE_INST_B, POWER_ACTIVE_INST_TOTAL,
POWER_REACTIVE_INST_TOTAL, FACTOR_POTENCIA_TOTAL,
POWER_ACTIVE_TOTAL_POS, POWER_ACTIVE_TOTAL_NEG
```

### Envelope de respuestas (todas las rutas REST)

Éxito:
```json
{ "success": true, "message": "...", "data": { ... } }
```
Error:
```json
{ "success": false, "message": "...", "error": ... }
```

### Autenticación

JWT con un solo usuario. `POST /api/v1/auth/login` con `{username, password}` devuelve `{access_token, refresh_token, token_type, expires_in}`. Access token va en `Authorization: Bearer <token>` en cada request protegido. `POST /api/v1/auth/refresh` rota el par (el refresh usado queda revocado — un refresh token solo sirve una vez). `POST /api/v1/auth/logout` revoca sesión. `GET /api/v1/auth/me` valida sesión activa.

### Endpoints REST (todos bajo `/api/v1`, todos requieren auth excepto `/auth/login` y `/health`)

```
Auth        POST /auth/login · POST /auth/refresh · POST /auth/logout · GET /auth/me
Dashboard   GET /dashboard · GET /dashboard/cards · GET /dashboard/status
Realtime    GET /realtime/latest · GET /realtime/device?device_id=
History     GET /history · GET /history/downsample · GET /history/range
Consumption GET /consumption/day|week|month|year   (energía IMPORTADA)
Export      GET /export/day|week|month|year         (energía EXPORTADA)
Analytics   GET /analytics · /analytics/daily-profile · /analytics/monthly-profile
            GET /analytics/max-demand · /analytics/load-factor · /analytics/base-load
            GET /analytics/compare
KPIs        GET /kpis
Reports     GET /reports/daily|weekly|monthly|yearly · GET /reports/custom
Health      GET /health
```

Nota: los indicadores de `analytics` (max-demand, load-factor, base-load) solo consideran ventanas de **importación** (`POWER_ACTIVE_INST_TOTAL > 0`); durante exportación esos campos vienen en `null` en el JSON — la UI debe manejar ese `null` explícitamente (ej. "No aplica — exportando"), no mostrarlo como 0 ni ocultarlo silenciosamente.

### WebSocket — tiempo real

Endpoint: `ws://<host>/ws` (sin prefijo `/api/v1`). Una sola variable activa por conexión; suscribirse a otra reemplaza la anterior.

Cliente → servidor:
```json
{ "action": "subscribe", "variable": "POWER_ACTIVE_INST_TOTAL" }
{ "action": "unsubscribe" }
{ "action": "ping" }
```

Servidor → cliente:
```json
{ "type": "subscribed", "variable": "..." }
{ "type": "data", "variable": "...", "value": 0, "device_id": "...", "device_name": "...", "timestamp": "..." }
{ "type": "unsubscribed" }
{ "type": "pong" }
{ "type": "error", "message": "...", "valid_variables": [...] }
```

El WebSocket **no lleva JWT en la URL/headers actualmente** (verificar en el spec/código si esto cambia); asumir que es de solo lectura y no expone datos sensibles más allá de lo que ya se ve autenticado en el dashboard.

### Zona horaria

Los timestamps que devuelve la API son **siempre UTC**. El backend calcula "hoy/semana/mes/año" en `America/Bogota`, pero expone todo en UTC — la conversión a hora local para mostrar en pantalla es responsabilidad del frontend (`Intl.DateTimeFormat` o `date-fns-tz`, zona `America/Bogota`).

---

## REFERENCIA DE DISEÑO

Diseño de referencia (demo básico, pero se acerca a la dirección visual deseada):

> https://claude.ai/design/p/17656f14-12c5-4c18-bfcc-5197d3a234c0?file=EMS+Residencial.dc.html&via=share

**Nota para el agente de código**: esta URL es de un producto de diseño de claude.ai distinto a los Artifacts de código — probablemente no se pueda abrir automáticamente vía fetch. Si no puedes acceder al enlace, pide al usuario una captura de pantalla o exportación del HTML antes de continuar con la sección de Diseño; no lo ignores en silencio.

Usar este diseño como punto de partida de estilo (paleta, tono visual), pero **superarlo** en: interactividad, animaciones, densidad de información bien organizada y pulido general — el propio usuario lo describe como "básico". Las directrices de la sección "Diseño" más abajo aplican como base y complemento.

---

## OBJETIVO

Frontend profesional, escalable y mantenible para el dashboard del EMS residencial: panel en tiempo real, históricos, analytics y reportes, consumiendo la API REST + WebSocket ya construidos.

---

## STACK TECNOLÓGICO

- React + TypeScript, inicializado con **Rsbuild** (`npm create rsbuild@latest` — elegir template React + TypeScript)
- **TailwindCSS** para estilos
- **axios** para HTTP
- **react-router-dom** para rutas
- **Recharts** para gráficas (líneas/áreas/barras) — composable y suficiente para series de tiempo; gauges (factor de potencia, carga base) se construyen a medida con SVG/Tailwind si Recharts no cubre el caso
- **framer-motion** para animaciones y transiciones
- **lucide-react** para iconografía
- **date-fns** + **date-fns-tz** para manejo de fechas y conversión UTC → `America/Bogota`
- Gestión de estado: **React Context API únicamente** (no Redux, no Zustand) — ver arquitectura de contextos abajo
- npm como gestor de paquetes (o el que rsbuild use por defecto)

---

## ARQUITECTURA

### Carpeta `src/api/` — cliente HTTP centralizado

Una instancia de axios configurada una sola vez, y un archivo por dominio de la API exportando funciones tipadas (una función por endpoint). Nada de axios disperso en componentes.

```
src/api/
  client.ts        # instancia axios: baseURL desde env, interceptor request (adjunta JWT),
                    # interceptor response (401 -> intenta /auth/refresh una vez, reintenta
                    # la request original; si falla, limpia sesión y redirige a /login)
  auth.ts           # login(), refresh(), logout(), me()
  dashboard.ts       # getDashboard(), getDashboardCards(), getDashboardStatus()
  realtime.ts        # getRealtimeLatest(), getRealtimeDevice(deviceId)
  history.ts         # getHistory(params), getHistoryDownsample(params), getHistoryRange(params)
  consumption.ts      # getConsumption(period, deviceId?)
  export.ts           # getExport(period, deviceId?)
  analytics.ts        # getAnalyticsOverview(), getDailyProfile(), getMonthlyProfile(),
                       # getMaxDemand(), getLoadFactor(), getBaseLoad(), compare(periodA, periodB)
  kpis.ts             # getKpis(params)
  reports.ts          # getReport(type, params)
  websocket.ts        # cliente WS: clase o factory con connect/subscribe/unsubscribe/close,
                       # reconexión automática con backoff, expone callbacks/eventos tipados
                       # según el protocolo documentado arriba
  types.ts            # tipos TS de request/response, alineados al OpenAPI real (no inventar campos)
```

Cada función de `src/api/*.ts` recibe parámetros tipados, llama al endpoint correspondiente vía la instancia de `client.ts`, y devuelve `data` ya desenvuelto del `ApiResponse` (o lanza si `success: false` — decidir un único patrón de manejo de errores y aplicarlo consistente en todos los módulos).

### Carpeta `src/context/` — estado global por nivel de uso

No todo va en un contexto único gigante. Cada contexto vive al nivel donde realmente se necesita:

```
src/context/
  AuthContext.tsx       # GLOBAL (envuelve toda la app en main.tsx/App.tsx):
                         # access token en memoria, refresh token persistido,
                         # user, login(), logout(), estado isAuthenticated
  ThemeContext.tsx        # GLOBAL: dark/light mode
  RealtimeContext.tsx      # ÁMBITO DASHBOARD: envuelve el layout autenticado; abre UNA
                            # conexión WebSocket compartida, expone el valor en vivo de la
                            # variable suscrita y una función para cambiar de variable —
                            # evita que cada widget abra su propio WS
  DashboardFiltersContext.tsx  # ÁMBITO PÁGINA: filtros de rango/periodo/device_id
                                 # compartidos entre los widgets de una sola página
                                 # (ej. History), no necesarios fuera de ella
```

Regla: si un dato solo lo usa un árbol de componentes específico (una página o sección), su contexto envuelve solo esa página — no lo subas a `App.tsx` "por si acaso".

### Resto de `src/`

```
src/
  api/            # ver arriba
  context/        # ver arriba
  components/
    ui/           # átomos reutilizables: Card, Button, Badge, Skeleton, Modal...
    charts/        # wrappers de Recharts: LineChartWidget, AreaChartWidget, GaugeChart...
    layout/         # Sidebar, Topbar, PageContainer, ProtectedRoute
    dashboard/       # widgets de negocio: PowerCard, ConsumptionSummary, LoadFactorGauge,
                      # DailyProfileChart, DeviceStatusBadge...
  pages/           # Login, Dashboard, History, Consumption, Analytics, Reports
  hooks/            # useAuth, useRealtime, useHistory, usePolling, useCountdown...
  types/             # tipos de dominio compartidos (Variable enum, Period, etc.)
  utils/              # formateo de fechas/unidades, helpers
  App.tsx
  main.tsx
```

---

## DISEÑO — dashboard moderno, animado, interactivo

Estética de monitoreo energético: oscuro por defecto (con toggle a claro), tarjetas grandes con números destacados, gráficas de área con degradado suave, micro-interacciones al hover, transiciones fluidas al actualizar datos en vivo.

- **Codificación de color con significado**: verde para exportación/excedente, ámbar o naranja para importación de red, azul/neutro para voltaje/corriente — consistente en toda la app, nunca arbitraria.
- **Indicador de "en vivo"**: un pulso/glow sutil (framer-motion) en las tarjetas que reciben datos por WebSocket, para que el usuario perciba que está viendo tiempo real y no un dato estático.
- **Gráficas animadas**: transición suave de entrada de datos (no "salto" brusco), tooltips con buen contraste, ejes legibles.
- **Skeleton loaders**, no spinners genéricos, mientras cargan los widgets.
- **Responsive**: el dashboard debe verse bien en tablet/mobile — grid que colapsa a columnas, sidebar colapsable.
- **Layout**: sidebar de navegación fija (Dashboard / Histórico / Consumo-Exportación / Analytics / Reportes) + topbar con estado de conexión (WS conectado/desconectado, hora local) + usuario/logout.
- Tipografía clara, jerarquía visual fuerte en los números principales (potencia actual, kWh del día), secundaria en las etiquetas.

---

## PÁGINAS

- **`/login`** — formulario simple, error claro si credenciales inválidas.
- **`/dashboard`** (home tras login) — tarjetas de `GET /dashboard/cards`, gráfica de potencia en vivo vía WebSocket (`POWER_ACTIVE_INST_TOTAL`), estado de conectividad (`GET /dashboard/status`).
- **`/history`** — selector de variable + rango de fechas + interval, gráfica (`/history` o `/history/downsample` según el tamaño del rango), tabla de puntos.
- **`/consumption-export`** — tabs día/semana/mes/año, comparando importación vs exportación (barras o área apilada), totales destacados.
- **`/analytics`** — perfil horario típico, perfil semanal, demanda máxima, factor de carga, carga base, comparación de periodos. Manejar explícitamente los campos `null` (ver nota de dominio arriba).
- **`/reports`** — selector de tipo de reporte (daily/weekly/monthly/yearly/custom), vista de los datos devueltos; la generación de PDF/Excel es responsabilidad del frontend (el backend solo entrega JSON "listo para" reporte) — usar librería cliente si se implementa exportación real (ej. `jspdf`/`xlsx`), o dejarlo como fase futura si no se pide explícitamente.

---

## FASES DE DESARROLLO — POR FASES, IGUAL QUE EL BACKEND

**NO generar todo el proyecto en una única respuesta.** Cada fase completamente funcional antes de iniciar la siguiente.

**Fase 1 — Fundaciones**
`npm create rsbuild@latest` (React + TS), TailwindCSS configurado, estructura de carpetas, ESLint + Prettier, `.env` con las variables de entorno (prefijo `PUBLIC_`, ver sección más abajo) apuntando al backend.

**Fase 2 — Capa API**
`src/api/client.ts` con interceptores, todos los módulos de `src/api/*.ts` verificados contra el OpenAPI real, tipos en `src/api/types.ts`.

**Fase 3 — Autenticación**
`AuthContext`, página `/login`, `ProtectedRoute`, manejo de refresh automático.

**Fase 4 — Layout y navegación**
Sidebar, Topbar, `ThemeContext`, routing entre páginas (vacías aún).

**Fase 5 — Dashboard principal**
`RealtimeContext` + cliente WebSocket funcionando, tarjetas y gráfica en vivo.

**Fase 6 — Históricos y analytics**
Páginas History, Consumption/Export, Analytics con sus gráficas.

**Fase 7 — Reportes**
Página Reports.

**Fase 8 — Pulido**
Animaciones, estados de carga/error, accesibilidad básica, responsive, testing (vitest + testing-library) para hooks/contexts críticos.

**Al finalizar cada fase**: build sin errores (`npm run build`), lint limpio, probar en navegador el flujo de esa fase contra el backend real corriendo. Solo entonces detenerse y esperar aprobación antes de continuar.

---

## CALIDAD DEL CÓDIGO

- TypeScript en modo estricto (`strict: true`), sin `any` salvo justificación explícita.
- Componentes tipados con `interface Props`, sin lógica de negocio embebida — esa vive en hooks/servicios (`src/api`, `src/hooks`).
- No duplicar llamadas HTTP: cada endpoint se consume desde su función en `src/api`, nunca con `axios` directo dentro de un componente.
- Un único patrón de manejo de errores de API (try/catch + toast/notificación, o error boundary por sección) aplicado consistentemente.
- Nombres de archivos y componentes en PascalCase para componentes, camelCase para hooks/utils.

---

## VARIABLES DE ENTORNO (frontend)

Rsbuild expone al cliente las variables prefijadas con `PUBLIC_`:

```
PUBLIC_API_BASE_URL=http://localhost:8000
PUBLIC_WS_URL=ws://localhost:8000/ws
```

`PUBLIC_API_BASE_URL`/`PUBLIC_WS_URL` deben poder apuntar también a una URL de **ngrok** (dominio cambia por sesión) sin tocar código — todo pasa por `.env`, nunca hardcodeado en `client.ts`/`websocket.ts`.

---

## NOTA — CORS en el backend

El usuario gestiona `CORS_ORIGINS` en `ApiEMS/.env` manualmente (no lo toques desde el frontend ni pidas que se automatice). Como el frontend puede exponerse vía ngrok, el dominio de origen cambiará entre sesiones — quien opere el backend deberá actualizar `CORS_ORIGINS` cada vez que cambie esa URL.
