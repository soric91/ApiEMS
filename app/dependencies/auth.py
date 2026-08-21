"""Quién llama, y qué equipos tiene permitido ver.

ApiEMS ya no autentica a nadie: verifica lo que CRMBackend firmó. Todo lo que
sigue son las tres compuertas que un token tiene que pasar antes de tocar un
dato, en este orden:

1. **Audiencia.** Solo `monitor`, la web de clientes. Un token de operador del
   CRM es válido, y aun así no abre el consumo de ningún cliente.
2. **Cambio de contraseña pendiente.** Un token con scope `password_change`
   sale de una contraseña que puso un administrador, no de una que el dueño
   de la cuenta eligió. Acá no vale nada.
3. **`puede_ver_consumo`.** El permiso vive en el CRM y es el único lugar
   donde se decide. Si está en falso, no hay consumo que mostrar.

Recién después de las tres se resuelve la flota, que es lo que acota cada
consulta a los equipos de esa empresa.
"""

from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.crm_identity import CrmIdentity, CrmIdentityVerifier, InvalidIdentityError
from app.core.logging import get_logger
from app.services.crm.client import CrmClientError
from app.services.crm.fleet import ClientFleet, FleetDirectory

logger = get_logger("apiems.auth")

_bearer = HTTPBearer(auto_error=False, description="Access token emitido por CRMBackend")


def get_verifier(request: Request) -> CrmIdentityVerifier:
    return cast(CrmIdentityVerifier, request.app.state.identity_verifier)


def get_fleet_directory(request: Request) -> FleetDirectory:
    return cast(FleetDirectory, request.app.state.fleet_directory)


def _unauthorized(reason: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=reason,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden(reason: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=reason)


def get_current_identity(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    verifier: Annotated[CrmIdentityVerifier, Depends(get_verifier)],
) -> CrmIdentity:
    """El cliente detrás del token, o 401."""
    if credentials is None:
        raise _unauthorized("Credenciales no proporcionadas")

    try:
        identity = verifier.verify(credentials.credentials)
    except InvalidIdentityError as exc:
        raise _unauthorized(exc.reason) from exc

    if identity.must_change_password:
        # El CRM ya limita ese token a sus dos rutas de cambio de contraseña.
        # Repetirlo acá evita que una sesión a medio empezar lea consumo.
        raise _forbidden("Tenés que cambiar la contraseña en el CRM antes de ver el consumo")
    if identity.client_id is None:
        # La audiencia `monitor` es de clientes; sin empresa no hay nada que
        # mostrar y no existe un caso legítimo que llegue hasta acá.
        raise _forbidden("Este token no pertenece a ninguna empresa")

    return identity


CurrentIdentity = Annotated[CrmIdentity, Depends(get_current_identity)]


async def get_current_fleet(
    identity: CurrentIdentity,
    directory: Annotated[FleetDirectory, Depends(get_fleet_directory)],
) -> ClientFleet:
    """Los equipos de la empresa de quien llama.

    Todo endpoint que lea datos depende de esto, no de la identidad pelada:
    así el recorte por cliente no es algo que cada ruta tenga que acordarse de
    aplicar, sino la única forma de saber qué equipos existen.
    """
    assert identity.client_id is not None
    try:
        fleet = await directory.for_client(identity.client_id)
    except CrmClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo consultar la flota en el CRM",
        ) from exc

    if not fleet.puede_ver_consumo and not identity.impersonated:
        # La marca la pone y la saca un administrador del CRM, y su sentido es
        # que el cliente no vea el consumo. Frenar también a quien administra
        # haría imposible revisar una empresa antes de habilitarla — que es
        # justo cuando hay algo que revisar.
        raise _forbidden("Esta empresa no tiene habilitada la vista de consumo")
    if identity.impersonated:
        logger.info(
            "consumo_por_suplantacion",
            admin_id=identity.user_id,
            client_id=identity.client_id,
            puede_ver_consumo=fleet.puede_ver_consumo,
        )
    return fleet


CurrentFleet = Annotated[ClientFleet, Depends(get_current_fleet)]


def allowed_device(fleet: ClientFleet, device_id: str | None) -> str | None:
    """Valida un `device_id` pedido contra la flota del cliente.

    Un equipo ajeno responde 404 y no 403: confirmar que existe ya sería
    contar algo de otra empresa.
    """
    if device_id is None:
        return None
    if device_id not in fleet.device_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dispositivo {device_id} no encontrado",
        )
    return device_id
