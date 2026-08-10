from app.models.variables import (
    CUMULATIVE_VARIABLES,
    INSTANT_VARIABLES,
    Variable,
    is_cumulative,
)


def test_counters_are_cumulative() -> None:
    assert is_cumulative(Variable.POWER_ACTIVE_TOTAL_POS)
    assert is_cumulative(Variable.POWER_ACTIVE_TOTAL_NEG)
    # Contadores de energía reactiva: los cuatro cuadrantes (IEC 60375).
    assert is_cumulative(Variable.POWER_REACTIVE_QUAD1)
    assert is_cumulative(Variable.POWER_REACTIVE_QUAD2)
    assert is_cumulative(Variable.POWER_REACTIVE_QUAD3)
    assert is_cumulative(Variable.POWER_REACTIVE_QUAD4)
    assert {
        Variable.POWER_ACTIVE_TOTAL_POS,
        Variable.POWER_ACTIVE_TOTAL_NEG,
        Variable.POWER_REACTIVE_QUAD1,
        Variable.POWER_REACTIVE_QUAD2,
        Variable.POWER_REACTIVE_QUAD3,
        Variable.POWER_REACTIVE_QUAD4,
    } == CUMULATIVE_VARIABLES


def test_instant_variables_exclude_counters() -> None:
    assert not is_cumulative(Variable.POWER_ACTIVE_INST_TOTAL)
    assert frozenset() == INSTANT_VARIABLES & CUMULATIVE_VARIABLES
    assert frozenset(Variable) == INSTANT_VARIABLES | CUMULATIVE_VARIABLES
