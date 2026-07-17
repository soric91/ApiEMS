from app.models.variables import (
    CUMULATIVE_VARIABLES,
    INSTANT_VARIABLES,
    Variable,
    is_cumulative,
)


def test_counters_are_cumulative() -> None:
    assert is_cumulative(Variable.POWER_ACTIVE_TOTAL_POS)
    assert is_cumulative(Variable.POWER_ACTIVE_TOTAL_NEG)
    assert {
        Variable.POWER_ACTIVE_TOTAL_POS,
        Variable.POWER_ACTIVE_TOTAL_NEG,
    } == CUMULATIVE_VARIABLES


def test_instant_variables_exclude_counters() -> None:
    assert not is_cumulative(Variable.POWER_ACTIVE_INST_TOTAL)
    assert frozenset() == INSTANT_VARIABLES & CUMULATIVE_VARIABLES
    assert frozenset(Variable) == INSTANT_VARIABLES | CUMULATIVE_VARIABLES
