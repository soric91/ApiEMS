"""Qué puede graficar este cliente, hoy.

Existe para que el panel deje de tener una lista fija de variables. Con una
lista fija pasan dos cosas malas a la vez: un equipo que reporta menos muestra
tarjetas vacías —una gráfica de tensión de fase C que nunca tuvo un dato— y un
equipo que reporta más no muestra lo que sí tiene.

La respuesta cruza dos fuentes:

* **CRMBackend** dice qué variables existen y qué significan (nombre, unidad,
  magnitud, fase, si es contador).
* **InfluxDB** dice cuáles llegaron a tener una lectura.

Se devuelven **todas** las que el CRM declara, cada una marcada con
`con_datos`. El panel grafica solo las marcadas —una variable que nunca
publicó no merece una gráfica vacía— pero necesita ver el resto para poder
distinguir dos fallas que se parecen y se arreglan en lados opuestos:

* **Ninguna variable declarada.** Falta configurar el medidor en el CRM.
* **Varias declaradas, ninguna con datos.** El CRM está bien; lo que falla es
  la adquisición o el almacenamiento.

Filtrar acá borraba esa diferencia: el panel veía una lista vacía en los dos
casos y mandaba a configurar un CRM que ya estaba completo.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.dependencies.auth import CurrentFleet
from app.dependencies.influx import get_influx_repository
from app.repositories.scoped import ScopedInfluxRepository
from app.schemas.common import ApiResponse
from app.schemas.variables import VariableDisponible
from app.services.influx.cache import cached_field_keys

router = APIRouter(prefix="/variables", tags=["Variables"])

RepoDep = Annotated[ScopedInfluxRepository, Depends(get_influx_repository)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

# Cuánto hacia atrás se mira para decidir si una variable "tiene datos" sale de
# `VARIABLES_LOOKBACK_HOURS`. Un equipo apagado el fin de semana no debería
# desaparecer del panel el lunes, y una ventana muy larga hace que esta consulta
# —que recorre datos, no el índice— domine el costo del panel.


@router.get(
    "",
    summary="Variables declaradas, marcando cuáles tienen datos",
    response_model=ApiResponse[list[VariableDisponible]],
)
async def list_variables(
    repo: RepoDep, fleet: CurrentFleet, settings: SettingsDep
) -> ApiResponse[list[VariableDisponible]]:
    """Las variables que este cliente tiene cargadas, con `con_datos` cada una.

    El panel dibuja solo las que lo traen en `true`. Si la fase C no reportó
    nunca, no se dibuja su gráfica — en vez de mostrarla vacía.
    """
    ventana = timedelta(hours=settings.VARIABLES_LOOKBACK_HOURS)
    reportaron = set(await cached_field_keys(repo, ventana))

    disponibles = [
        VariableDisponible(
            nombre=variable.nombre,
            etiqueta=variable.etiqueta,
            unidad=variable.unidad,
            magnitud=variable.magnitud,
            fase=variable.fase,
            acumulativa=variable.acumulativa,
            equipos=sorted(variable.equipos),
            con_datos=variable.nombre in reportaron,
        )
        for variable in fleet.variables
    ]
    return ApiResponse(data=disponibles)
