# PROMPT — Autenticación de servicio contra CRMBackend

**Para:** el agente que trabaja sobre `ApiEMS`.
**Estado del otro lado:** ya implementado, con migración y tests. Nada de lo
que sigue está pendiente en CRMBackend.
**Alcance de este documento:** solo `ApiEMS`. No toca `CRMweb` ni `gatewayEMS`.

---

## 0. Qué cambió y por qué te importa

`app/services/crm/client.py` hoy hace esto:

```python
response = await client.post(
    f"{self._base_url}/api/v1/auth/login",
    json={"email": self._email, "password": self._password},
)
```

Es decir: **ApiEMS se autentica como una persona.** `CRM_SERVICE_EMAIL` /
`CRM_SERVICE_PASSWORD` son las credenciales de un usuario real del CRM.

Los problemas concretos, no teóricos:

1. Esa contraseña abre **el panel del CRM completo**, no solo lo que ApiEMS
   necesita. Quien la lea en el `.env` entra a la interfaz web.
2. El rol mínimo que sirve hoy (`solo_lectura`) igual **ve toda la plataforma**
   — todos los clientes, todos los gateways.
3. **No hay forma de revocar solo a ApiEMS** sin desactivar una cuenta que
   alguien podría estar usando.
4. En los logs del CRM, ApiEMS aparece como esa persona. Un incidente no se
   puede atribuir.
5. Rotarla implica cambiarle la contraseña a una cuenta de usuario.

CRMBackend ahora expone **credenciales de servicio**: identidad propia,
solo lectura, permisos por separado, revocables sin tocar ninguna cuenta.

---

## 1. El contrato nuevo

### Obtener el token

```
POST {CRM_BASE_URL}/api/v1/service/token
Content-Type: application/json

{"client_id": "svc_...", "client_secret": "svcsec_..."}
```

Sin header `Authorization`: la credencial en el cuerpo **es** la autenticación.

**200:**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 3600,
  "permisos": ["tariffs:read", "fleet:read"],
  "scope_client_id": null
}
```

- `expires_in` — segundos. Por defecto **3600**. No lo hardcodees, leelo.
- `permisos` — lo que esa credencial puede leer, tal como está en el CRM ahora.
- `scope_client_id` — `null` si ve toda la plataforma; un uuid si quedó fijada
  a una empresa. Útil para no pasar un `client_id` que va a devolver vacío.

**401** para *cualquier* falla: identificador desconocido, secreto errado,
credencial desactivada, credencial vencida. Es a propósito — distinguirlas le
diría a alguien con un identificador filtrado si todavía sirve. No intentes
inferir la causa del cuerpo.

### Usar el token

```
Authorization: Bearer <access_token>
```

Y llega **exactamente** a dos rutas:

| Permiso        | Ruta                                   |
| -------------- | -------------------------------------- |
| `tariffs:read` | `GET /api/v1/tariffs`                  |
| `fleet:read`   | `GET /api/v1/fleet`                    |

Cualquier otra ruta responde **401**, incluida `GET /api/v1/tariffs/{id}`: se
abrió el listado, no el detalle. Y **ningún** permiso habilita escribir —
`POST /api/v1/tariffs` con un token de servicio es 401, no 403.

Un token válido pero sin el permiso correspondiente da **403**:

```json
{"error": {"code": "not_authorized", "message": "Role 'servicio' cannot read the fleet", "details": {}}}
```

Esa diferencia sí importa para vos: **401 = pedí un token nuevo; 403 = pedile
al administrador del CRM que amplíe los permisos.** Reintentar un 403 con un
token nuevo es un bucle.

---

## 2. Qué hacer en ApiEMS

### 2.1 Configuración — `app/core/config.py`

Reemplazar:

```python
CRM_BASE_URL: str = ""
CRM_SERVICE_EMAIL: str = ""
CRM_SERVICE_PASSWORD: str = ""
```

por:

```python
    # --- CRMBackend ---
    # Credencial de servicio, no de usuario: identidad propia, solo lectura,
    # revocable sin tocar la cuenta de ninguna persona. Se emite en el panel
    # del CRM (Servicios) y el secreto se muestra una sola vez.
    CRM_BASE_URL: str = ""
    CRM_CLIENT_ID: str = ""
    CRM_CLIENT_SECRET: str = ""
```

Y en `.env.example`:

```dotenv
# --- CRMBackend (credencial de servicio) ---
# Se emiten en el CRM: Servicios → Nueva credencial. El secreto se muestra
# una sola vez; si se pierde, se rota, no se recupera.
# Pedí solo los permisos que ApiEMS necesita:
#   tariffs:read → GET /api/v1/tariffs
#   fleet:read   → GET /api/v1/fleet
CRM_BASE_URL=http://localhost:8000
CRM_CLIENT_ID=svc_...
CRM_CLIENT_SECRET=svcsec_...
```

**Borrá `CRM_SERVICE_EMAIL` y `CRM_SERVICE_PASSWORD`** del `.env.example` y del
`.env`. Dejarlas ahí invita a que alguien las vuelva a llenar.

Si `ENVIRONMENT == "production"`, el validador que ya existe para `JWT_SECRET`
debería exigir también que `CRM_CLIENT_SECRET` empiece con `svcsec_` cuando
`CRM_BASE_URL` está configurado — un secreto vacío se detecta al arrancar, no
en la primera petición de costos.

### 2.2 El cliente HTTP — `app/services/crm/client.py`

Reescribir `_login` como intercambio de credencial:

```python
async def _fetch_token(self, client: httpx.AsyncClient) -> str:
    if not self.configured:
        raise CrmClientError(
            "CRM_BASE_URL/CRM_CLIENT_ID/CRM_CLIENT_SECRET sin configurar"
        )
    response = await client.post(
        f"{self._base_url}/api/v1/service/token",
        json={
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        },
    )
    if response.status_code != _HTTP_OK:
        # El CRM responde el mismo 401 para credencial desconocida, secreto
        # errado, desactivada y vencida. No hay nada que distinguir acá.
        raise CrmClientError(
            f"credencial de servicio rechazada por CRMBackend: "
            f"HTTP {response.status_code}"
        )
    payload = response.json()
    self._token = payload["access_token"]
    # `expires_in` viene del servidor: no asumas 3600, es configurable.
    self._token_expires_at = time.monotonic() + payload["expires_in"] - _SKEW_SECONDS
    self._permissions = frozenset(payload["permisos"])
    return self._token
```

Cambios respecto de lo que hay hoy:

**(a) Renovar por tiempo, no solo por 401.** El cliente actual guarda el token
para siempre y lo renueva únicamente cuando le rebota. Un token de servicio
dura una hora; conocer `expires_in` permite renovarlo antes de que falle y
ahorra un round-trip fallido por hora.

```python
_SKEW_SECONDS = 60  # renovar un minuto antes: relojes y latencia

def _token_is_fresh(self) -> bool:
    return self._token is not None and time.monotonic() < self._token_expires_at
```

Usá `time.monotonic()`, no `datetime.now()`: un ajuste de reloj del sistema no
debe hacer que el token parezca vencido o eterno.

**(b) Mantener el reintento por 401**, que ya está bien: cubre el caso de que la
credencial se haya rotado del otro lado mientras el token seguía en memoria.

**(c) No reintentar un 403.** El código actual reintenta solo en 401, así que ya
está bien — pero dejalo explícito en un comentario, porque es fácil "arreglarlo"
mal después.

**(d) Nunca loguear `CRM_CLIENT_SECRET`.** Ni en un mensaje de error, ni en un
`repr`, ni en un log de debug. Si `CrmClient` gana un `__repr__`, que muestre
`client_id` y no el secreto.

### 2.3 Consumir `/fleet` — `app/services/crm/client.py`

Método nuevo, que es lo que resuelve el gap #3 de `prompt_arquitectura_v2.md`:

```python
async def get_fleet(
    self, *, nivel: str = "variables", client_id: str | None = None
) -> dict[str, Any]:
    """Árbol completo: empresas → sedes → gateways → equipos → variables."""
```

Detalles que importan:

- **Cachear por ETag.** La respuesta trae `ETag`; mandalo en `If-None-Match` y
  el CRM responde **304 sin cuerpo** si nada cambió. Es la razón por la que ese
  endpoint existe con ese header.
- **La huella cambia cuando un gateway se calla**, porque `estado` se deriva de
  `ultima_conexion` (offline a los 5 minutos). O sea: no sirve como caché de
  larga duración, sirve para no volver a parsear el árbol.
- **Cachear por `(nivel, client_id, limit, offset)`**, no por endpoint: cada
  combinación tiene su propia huella.
- Para el selector de medidores de la Fase 3 alcanza `nivel=equipos`, que es un
  documento bastante más chico.

`gateway.uuid` de esa respuesta **es** el `gateway_uuid` que llega taggeado en
InfluxDB. Eso convierte la convención implícita del gap #1 en algo consultable.

### 2.4 Degradación — importante

Si CRMBackend está caído o la credencial fue revocada, **ApiEMS tiene que
seguir sirviendo lecturas**. El consumo histórico vive en InfluxDB y no depende
del CRM para nada.

Concretamente:

- Un `CrmClientError` al pedir tarifas **no** debe convertirse en un 500 de
  `/reports` o `/costs`. Serví el último valor cacheado y logueá un warning.
- Si nunca hubo un valor cacheado, devolvé el consumo **sin valorizar** con un
  campo que diga que el costo no está disponible — mejor que un error que borra
  también los kWh, que sí son correctos.
- El arranque de la aplicación no debe bloquearse esperando al CRM.

### 2.5 Lo que NO hay que hacer

- **No valides el JWT del CRM por tu cuenta.** No compartas `JWT_SECRET` entre
  los dos servicios. El token de servicio es opaco para ApiEMS: se guarda, se
  manda, se renueva. Nada más.
- **No guardes el token en disco** ni en Influx. Memoria del proceso; al
  reiniciar se pide otro, que cuesta un round-trip.
- **No pidas más permisos "por las dudas".** Si ApiEMS solo valoriza consumo,
  `tariffs:read` alcanza. Ampliar después no requiere rotar el secreto.
- **No uses la credencial de servicio para el login de usuarios del frontend.**
  Son cosas distintas; ver la sección 4.

---

## 3. Tests que faltan del lado de ApiEMS

Con `httpx.MockTransport`, que es lo que `CrmClient` ya soporta:

1. El token se pide una sola vez y se reutiliza mientras esté fresco.
2. Cerca del vencimiento se renueva **antes** de que el servidor lo rechace.
3. Un 401 en una petición dispara **una** renovación y **un** reintento, no un
   bucle.
4. Un 403 **no** se reintenta.
5. Un 304 en `/fleet` devuelve lo cacheado sin volver a parsear.
6. El ETag se cachea por combinación de parámetros: pedir `nivel=equipos`
   después de `nivel=variables` no devuelve el árbol equivocado.
7. Con `CrmClientError`, `/reports` sigue respondiendo con los kWh.
8. El secreto no aparece en ningún log ni en ningún mensaje de excepción.

El punto 8 se testea de verdad: capturá los logs y afirmá que el valor de
`CRM_CLIENT_SECRET` no está en la salida.

---

## 4. Lo que este cambio NO resuelve

La Fase 5 del `prompt_arquitectura_v2.md` proponía además que **el frontend se
autenticara contra CRMBackend** y que ApiEMS validara ese mismo JWT de usuario.
Eso sigue pendiente y **es un problema distinto**:

| | Credencial de servicio | Login de usuario |
|---|---|---|
| Quién | ApiEMS, un proceso | una persona en el navegador |
| Qué prueba | que el proceso es quien dice | quién es y a qué empresa pertenece |
| Alcance | solo lectura, dos rutas | lo que su rol permita |
| Sirve para | traer tarifas y la flota | decidir qué ve un cliente en su panel |

La credencial de servicio **no** te dice qué usuario está mirando la pantalla.
Sigue haciendo falta decidir si el login del frontend se migra a CRMBackend
como issuer. Ese cambio necesita su propio documento — y del lado del CRM,
probablemente un endpoint de verificación o un JWKS.

Hasta entonces, `API_USERNAME` / `API_PASSWORD` de ApiEMS siguen como están.

---

## 5. Cómo conseguir la credencial

No la generes vos ni la inventes. En el CRM:

1. Entrar como **administrador** (un `tecnico` no puede: la credencial
   sobrevive a quien la crea).
2. **Servicios → Nueva credencial.**
3. Nombre: `ApiEMS`. Descripción: para qué es — lo que se lea ahí dentro de un
   año decide si se puede revocar sin miedo.
4. Permisos: solo los que ApiEMS use hoy.
5. Alcance: *Toda la plataforma*, salvo que ApiEMS sirva a una sola empresa.
6. Vencimiento: ponele uno. Una credencial sin vencimiento es una que nadie
   vuelve a mirar.
7. Copiar `client_id` y `client_secret` al `.env`. **El secreto se muestra una
   sola vez.** Si se pierde, se rota — no se recupera.

Si se filtra: **Servicios → Rotar secreto.** El anterior deja de servir en el
momento; los tokens ya emitidos siguen valiendo como mucho una hora.

---

## 6. Orden sugerido

1. Config + `.env.example`, borrando las variables viejas.
2. `CrmClient`: intercambio de credencial, renovación por tiempo, sin logs del
   secreto.
3. Tests 1–4 y 8.
4. `get_fleet` con caché por ETag. Tests 5–6.
5. Degradación cuando el CRM no está. Test 7.
6. Conectar `get_tariff_config()` a la fuente remota — recordá que sigue
   abierto lo de `cargo_fijo`, que el modelo del CRM no tiene.

Cada paso: build sin errores, tests en verde, y confirmar que Dashboard y
Alertas siguen intactos antes de seguir.
