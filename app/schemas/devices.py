"""El inventario de medidores tal como lo necesita el panel."""

from pydantic import BaseModel, Field


class DeviceDisponible(BaseModel):
    """Un medidor dado de alta, con dónde está instalado.

    Trae la sede y el gateway porque el panel agrupa por ellos: una lista
    plana de veinte medidores no se puede recorrer, y un medidor sin datos no
    se explica solo — si su gateway está caído, el medidor está bien.
    """

    device_id: str = Field(description="El `identify_device` con el que viajan sus lecturas")
    nombre: str = Field(description="Cómo se llama el equipo, para mostrar")
    modbus_id: int | None = Field(description="Su dirección en el bus, única dentro del gateway")
    sede_id: str
    sede: str
    gateway_id: str
    gateway: str = Field(description="Número de serie del gateway que lo lee")
    gateway_en_linea: bool = Field(
        description=(
            "Si el gateway que lo lee reportó hace poco. Lo decide el CRM con su "
            "propio umbral; acá solo se transporta, para que el panel del cliente "
            "y el del operador no puedan discrepar"
        )
    )
