"""Catálogo de variables del medidor y reglas de agregación.

Regla de dominio central: los contadores acumulativos (energía importada /
exportada) son monótonos crecientes. Jamás admiten mean/max/min — solo
`difference()` para energía en un rango y `last()` para valor puntual.
"""

from enum import StrEnum


class Variable(StrEnum):
    """Las mediciones que ApiEMS sabe consultar.

    El valor es el nombre IEC 61850 con el que el gateway publica y con el que
    queda guardada en InfluxDB. El identificador de Python se mantuvo en
    inglés porque lo usan catorce módulos; cambiarlo habría sido un renombrado
    masivo sin ninguna ganancia.

    Estos nombres tienen que coincidir con el catálogo de CRMBackend
    (app/domain/measurements.py), que es donde se cargan las variables. No hay
    traducción entre medio: el nombre que se elige en el CRM es el que el
    gateway publica y el que se consulta acá.
    """

    # --- Tensión ---
    VOLTAGE_A = "PhV_phsA"
    VOLTAGE_B = "PhV_phsB"
    VOLTAGE_C = "PhV_phsC"
    # --- Corriente ---
    CURRENT_A = "A_phsA"
    CURRENT_B = "A_phsB"
    CURRENT_C = "A_phsC"
    # --- Potencia activa ---
    POWER_ACTIVE_INST_A = "W_phsA"
    POWER_ACTIVE_INST_B = "W_phsB"
    POWER_ACTIVE_INST_C = "W_phsC"
    POWER_ACTIVE_INST_TOTAL = "TotW"
    # --- Potencia reactiva y aparente ---
    POWER_REACTIVE_INST_TOTAL = "TotVAr"
    POWER_APPARENT_INST_TOTAL = "TotVA"
    # --- Factor de potencia ---
    FACTOR_POTENCIA_TOTAL = "TotPF"
    # --- Frecuencia ---
    FREQUENCY = "Hz"
    # --- Contadores acumulativos (kWh). Medidor bidireccional en la acometida.
    # Monótonos crecientes: jamás admiten mean/max/min, solo difference() para
    # la energía de un rango y last() para el valor puntual.
    POWER_ACTIVE_TOTAL_POS = "TotWh_import"
    POWER_ACTIVE_TOTAL_NEG = "TotWh_export"
    # --- Contadores acumulativos de energía reactiva (kvarh). Igual que los
    # activos: monótonos crecientes, solo difference()/last(). Los cuatro
    # cuadrantes (IEC 60375): Q1/Q2 es reactiva importada de la red (Q1
    # inductiva, Q2 capacitiva) y Q3/Q4 reactiva exportada a la red (Q3
    # capacitiva, Q4 inductiva).
    POWER_REACTIVE_QUAD1 = "Q1Eq"
    POWER_REACTIVE_QUAD2 = "Q2Eq"
    POWER_REACTIVE_QUAD3 = "Q3Eq"
    POWER_REACTIVE_QUAD4 = "Q4Eq"


class Aggregation(StrEnum):
    MEAN = "mean"
    MAX = "max"
    MIN = "min"
    LAST = "last"


CUMULATIVE_VARIABLES: frozenset[Variable] = frozenset(
    {
        Variable.POWER_ACTIVE_TOTAL_POS,
        Variable.POWER_ACTIVE_TOTAL_NEG,
        Variable.POWER_REACTIVE_QUAD1,
        Variable.POWER_REACTIVE_QUAD2,
        Variable.POWER_REACTIVE_QUAD3,
        Variable.POWER_REACTIVE_QUAD4,
    }
)

# Los cuadrantes de energía reactiva en orden q1..q4. Los dos primeros
# importan reactiva de la red y los dos últimos la exportan (IEC 60375).
REACTIVE_QUADRANTS: tuple[Variable, ...] = (
    Variable.POWER_REACTIVE_QUAD1,
    Variable.POWER_REACTIVE_QUAD2,
    Variable.POWER_REACTIVE_QUAD3,
    Variable.POWER_REACTIVE_QUAD4,
)

INSTANT_VARIABLES: frozenset[Variable] = frozenset(set(Variable) - CUMULATIVE_VARIABLES)

INSTANT_AGGREGATIONS: frozenset[Aggregation] = frozenset(
    {Aggregation.MEAN, Aggregation.MAX, Aggregation.MIN, Aggregation.LAST}
)


def is_cumulative(variable: Variable) -> bool:
    return variable in CUMULATIVE_VARIABLES


class InvalidAggregationError(ValueError):
    """Agregación no permitida para la variable (p. ej. mean sobre un contador)."""

    def __init__(self, variable: Variable, aggregation: Aggregation) -> None:
        super().__init__(
            f"'{aggregation}' no permitida sobre '{variable}': los contadores "
            "acumulativos solo admiten difference()/last()"
        )
        self.variable = variable
        self.aggregation = aggregation
