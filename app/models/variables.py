"""Catálogo de variables del medidor y reglas de agregación.

Regla de dominio central: los contadores acumulativos (energía importada /
exportada) son monótonos crecientes. Jamás admiten mean/max/min — solo
`difference()` para energía en un rango y `last()` para valor puntual.
"""

from enum import StrEnum


class Variable(StrEnum):
    CURRENT_A = "CURRENT_A"
    CURRENT_B = "CURRENT_B"
    CURRENT_C = "CURRENT_C"
    VOLTAGE_A = "VOLTAGE_A"
    VOLTAGE_B = "VOLTAGE_B"
    VOLTAGE_C = "VOLTAGE_C"
    POWER_ACTIVE_INST_A = "POWER_ACTIVE_INST_A"
    POWER_ACTIVE_INST_B = "POWER_ACTIVE_INST_B"
    POWER_ACTIVE_INST_C = "POWER_ACTIVE_INST_C"
    POWER_ACTIVE_INST_TOTAL = "POWER_ACTIVE_INST_TOTAL"
    POWER_REACTIVE_INST_TOTAL = "POWER_REACTIVE_INST_TOTAL"
    FACTOR_POTENCIA_TOTAL = "FACTOR_POTENCIA_TOTAL"
    # Contadores acumulativos (kWh) — medidor bidireccional en la acometida
    POWER_ACTIVE_TOTAL_POS = "POWER_ACTIVE_TOTAL_POS"  # energía importada de la red
    POWER_ACTIVE_TOTAL_NEG = "POWER_ACTIVE_TOTAL_NEG"  # energía exportada a la red


class Aggregation(StrEnum):
    MEAN = "mean"
    MAX = "max"
    MIN = "min"
    LAST = "last"


CUMULATIVE_VARIABLES: frozenset[Variable] = frozenset(
    {Variable.POWER_ACTIVE_TOTAL_POS, Variable.POWER_ACTIVE_TOTAL_NEG}
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
