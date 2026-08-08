"""Lo que el panel necesita saber de una medición para dibujarla."""

from pydantic import BaseModel, Field


class VariableDisponible(BaseModel):
    """Una medición que este cliente tiene cargada en el CRM.

    Todo lo descriptivo viene del catálogo de CRMBackend. ApiEMS solo agrega
    el hecho que el CRM no puede saber: si llegaron lecturas (`con_datos`).
    """

    nombre: str = Field(description="Nombre canónico IEC 61850, p. ej. `PhV_phsA`")
    etiqueta: str = Field(description="Cómo mostrarla: `Tensión fase A`")
    unidad: str = Field(description="Vacío si es adimensional, como el factor de potencia")
    magnitud: str | None = Field(
        description="Qué mide. El panel agrupa por esto en vez de tener campos fijos"
    )
    fase: str | None = Field(description="`A`, `B`, `C`, `N` o `total`")
    acumulativa: bool = Field(
        description="Contador monótono: solo admite diferencias, nunca promedios"
    )
    equipos: list[str] = Field(description="Los equipos que la reportan")
    con_datos: bool = Field(
        description=(
            "Si tuvo al menos una lectura en la ventana reciente. En `false` la "
            "variable existe en el CRM pero nunca publicó: no se grafica, y "
            "la diferencia importa porque son dos problemas distintos"
        )
    )
