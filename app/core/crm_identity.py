"""Quién está mirando, según CRMBackend.

ApiEMS dejó de emitir tokens. Los emite el CRM, que es el único que sabe qué
clientes existen, a qué empresa pertenece cada persona y si tiene permitido
ver su consumo. Acá solo se verifican.

La verificación usa la clave **pública** que el CRM publica en su JWKS. Eso es
deliberado: este servicio puede comprobar que un token salió del CRM y sigue
sin poder fabricar uno. Con un secreto compartido las dos capacidades serían
la misma, y comprometer ApiEMS alcanzaría para entrar al CRM como quien sea.
"""

from dataclasses import dataclass

import jwt
from jwt import PyJWKClient

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger("apiems.identity")


class InvalidIdentityError(Exception):
    """El token no vino del CRM, venció, o no sirve para esta superficie."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class CrmIdentity:
    """Lo que el CRM afirma sobre quien llama."""

    user_id: str
    role: str
    # La empresa a la que pertenece. `None` en cuentas internas del CRM, que
    # no deberían llegar acá — la audiencia `monitor` ya las deja afuera.
    client_id: str | None
    # `password_change` mientras la contraseña siga siendo la que generó un
    # administrador. Ese token abre el cambio de contraseña en el CRM y nada
    # más; acá vale exactamente lo mismo que ninguno.
    scope: str
    # El token lo pidió un administrador del CRM para mirar los datos de esta
    # empresa. Sigue nombrando una sola, así que nada de lo que consulta datos
    # cambia por esto — lo único que cambia es que `puede_ver_consumo` deja de
    # aplicar, porque esa marca decide lo que ve el cliente y no quien lo
    # administra.
    impersonated: bool = False

    @property
    def must_change_password(self) -> bool:
        return self.scope == "password_change"


class CrmIdentityVerifier:
    """Verifica tokens del CRM contra su JWKS, cacheando las claves.

    `PyJWKClient` mantiene su propio caché y vuelve a pedir el JWKS solo si
    aparece un `kid` que no conoce — que es exactamente lo que pasa cuando el
    CRM rota su clave. No hace falta reiniciar nada para adoptarla.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._jwks: PyJWKClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self._settings.CRM_BASE_URL)

    def _client(self) -> PyJWKClient:
        if self._jwks is None:
            self._jwks = PyJWKClient(
                self._settings.crm_jwks_url,
                cache_keys=True,
                lifespan=self._settings.CRM_JWKS_CACHE_SECONDS,
            )
        return self._jwks

    def verify(self, token: str) -> CrmIdentity:
        """Devuelve quién es, o falla. Nunca devuelve una identidad a medias."""
        if not self.configured:
            raise InvalidIdentityError("CRM_BASE_URL sin configurar")

        try:
            signing_key = self._client().get_signing_key_from_jwt(token).key
            claims = jwt.decode(  # pyright: ignore[reportUnknownMemberType]
                token,
                signing_key,
                # Un solo algoritmo, nunca una lista. La clave pública es
                # pública: si acá se aceptara HS256, cualquiera podría firmar
                # con ese PEM como secreto y pasar. Es el ataque de confusión
                # de algoritmos, y fijarlo es toda la defensa.
                algorithms=["RS256"],
                audience=self._settings.CRM_JWT_AUDIENCE,
                options={"require": ["exp", "sub", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidIdentityError("Token inválido o vencido") from exc
        except Exception as exc:
            logger.warning("jwks_unreachable", error=str(exc))
            raise InvalidIdentityError("No se pudo verificar el token") from exc

        return CrmIdentity(
            user_id=str(claims["sub"]),
            role=str(claims.get("role", "")),
            client_id=(
                str(claims["client_id"]) if claims.get("client_id") else None
            ),
            scope=str(claims.get("scope", "full")),
            impersonated=claims.get("impersonated") is True,
        )
