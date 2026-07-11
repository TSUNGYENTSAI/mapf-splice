"""Solver-independent validation of synchronized recovery solutions."""
import pytest

from mapf_splice.domain import Cell
from mapf_splice.recovery import (
    RecoveryValidationError,
    ScopedMapfProblem,
    ScopedMapfSolution,
    validate_synchronized_solution,
)
from mapf_splice.scenario import WarehouseMap

OPEN = WarehouseMap(rows=("....." , "....." , "....."))


def _problem(starts, goals, warehouse=OPEN) -> ScopedMapfProblem:
    return ScopedMapfProblem(
        robot_ids=tuple(sorted(starts)),
        starts=starts,
        goals=goals,
        warehouse_map=warehouse,
        max_timestep=64,
        seed=0,
    )


def _solution(paths) -> ScopedMapfSolution:
    makespan = len(next(iter(paths.values()))) - 1
    return ScopedMapfSolution(
        robot_ids=tuple(sorted(paths)), paths=paths, makespan=makespan
    )


def test_valid_solution_passes() -> None:
    problem = _problem(
        {"R1": Cell(0, 0), "R2": Cell(2, 0)},
        {"R1": Cell(0, 2), "R2": Cell(2, 2)},
    )
    solution = _solution(
        {
            "R1": (Cell(0, 0), Cell(0, 1), Cell(0, 2)),
            "R2": (Cell(2, 0), Cell(2, 1), Cell(2, 2)),
        }
    )
    validate_synchronized_solution(solution, problem=problem)


def test_missing_participant_is_rejected() -> None:
    problem = _problem(
        {"R1": Cell(0, 0), "R2": Cell(2, 0), "R3": Cell(1, 0)},
        {"R1": Cell(0, 2), "R2": Cell(2, 2), "R3": Cell(1, 2)},
    )
    solution = _solution(
        {
            "R1": (Cell(0, 0), Cell(0, 1), Cell(0, 2)),
            "R2": (Cell(2, 0), Cell(2, 1), Cell(2, 2)),
        }
    )
    with pytest.raises(RecoveryValidationError, match="coverage"):
        validate_synchronized_solution(solution, problem=problem)


def test_wrong_start_is_rejected() -> None:
    problem = _problem({"R1": Cell(0, 0)}, {"R1": Cell(0, 2)})
    solution = _solution({"R1": (Cell(0, 1), Cell(0, 2))})
    with pytest.raises(RecoveryValidationError, match="start"):
        validate_synchronized_solution(solution, problem=problem)


def test_wrong_goal_is_rejected() -> None:
    problem = _problem({"R1": Cell(0, 0)}, {"R1": Cell(0, 2)})
    solution = _solution({"R1": (Cell(0, 0), Cell(0, 1))})
    with pytest.raises(RecoveryValidationError, match="goal"):
        validate_synchronized_solution(solution, problem=problem)


def test_cell_on_obstacle_is_rejected() -> None:
    warehouse = WarehouseMap(rows=("..#..",))
    problem = _problem({"R1": Cell(0, 0)}, {"R1": Cell(0, 3)}, warehouse=warehouse)
    solution = _solution(
        {"R1": (Cell(0, 0), Cell(0, 1), Cell(0, 2), Cell(0, 3))}
    )
    with pytest.raises(RecoveryValidationError, match="traversable"):
        validate_synchronized_solution(solution, problem=problem)


def test_non_adjacent_transition_is_rejected() -> None:
    problem = _problem({"R1": Cell(0, 0)}, {"R1": Cell(0, 2)})
    solution = _solution({"R1": (Cell(0, 0), Cell(0, 2), Cell(0, 2))})
    with pytest.raises(RecoveryValidationError, match="adjacent"):
        validate_synchronized_solution(solution, problem=problem)


def test_vertex_collision_is_rejected() -> None:
    problem = _problem(
        {"R1": Cell(0, 0), "R2": Cell(1, 1)},
        {"R1": Cell(0, 2), "R2": Cell(1, 1)},
    )
    solution = _solution(
        {
            "R1": (Cell(0, 0), Cell(0, 1), Cell(0, 2)),
            "R2": (Cell(1, 1), Cell(0, 1), Cell(1, 1)),
        }
    )
    with pytest.raises(RecoveryValidationError, match="vertex collision"):
        validate_synchronized_solution(solution, problem=problem)


def test_opposite_edge_swap_is_rejected() -> None:
    problem = _problem(
        {"R1": Cell(0, 0), "R2": Cell(0, 1)},
        {"R1": Cell(0, 1), "R2": Cell(0, 0)},
    )
    solution = _solution(
        {"R1": (Cell(0, 0), Cell(0, 1)), "R2": (Cell(0, 1), Cell(0, 0))}
    )
    with pytest.raises(RecoveryValidationError, match="swap"):
        validate_synchronized_solution(solution, problem=problem)
