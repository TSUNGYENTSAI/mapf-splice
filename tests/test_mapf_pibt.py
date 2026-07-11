"""The PyPIBT adapter: solves scoped problems and returns typed results only.

numpy is optional (the ``recovery`` extra); skip when unavailable.
"""
import pytest

pytest.importorskip("numpy")

from mapf_splice import mapf_pibt  # noqa: E402
from mapf_splice.domain import Cell  # noqa: E402
from mapf_splice.recovery import (  # noqa: E402
    RecoveryFailureReason,
    RecoveryPlanningFailure,
    ScopedMapfProblem,
    ScopedMapfSolution,
)
from mapf_splice.scenario import WarehouseMap  # noqa: E402


def _problem(rows, starts, goals, *, max_timestep=64, seed=0) -> ScopedMapfProblem:
    robot_ids = tuple(sorted(starts))
    return ScopedMapfProblem(
        robot_ids=robot_ids,
        starts=starts,
        goals=goals,
        warehouse_map=WarehouseMap(rows=tuple(rows)),
        max_timestep=max_timestep,
        seed=seed,
    )


def test_solve_returns_synchronized_solution_reaching_goals() -> None:
    problem = _problem(
        ("....." , "....."),
        starts={"R1": Cell(0, 0), "R2": Cell(0, 4)},
        goals={"R1": Cell(0, 4), "R2": Cell(0, 0)},
    )
    result = mapf_pibt.solve(problem)
    assert isinstance(result, ScopedMapfSolution)
    assert result.robot_ids == ("R1", "R2")
    for robot_id in ("R1", "R2"):
        path = result.paths[robot_id]
        assert all(isinstance(cell, Cell) for cell in path)
        assert path[0] == problem.starts[robot_id]
        assert path[-1] == problem.goals[robot_id]
    assert result.makespan == len(result.paths["R1"]) - 1


def test_solve_is_deterministic_under_fixed_seed() -> None:
    kwargs = dict(
        rows=("....." , "....."),
        starts={"R1": Cell(0, 0), "R2": Cell(0, 4)},
        goals={"R1": Cell(0, 4), "R2": Cell(0, 0)},
    )
    first = mapf_pibt.solve(_problem(**kwargs))
    second = mapf_pibt.solve(_problem(**kwargs))
    assert isinstance(first, ScopedMapfSolution)
    assert first.paths == second.paths


def test_solve_reports_no_solution_when_goals_unreachable_in_bound() -> None:
    # A single-width corridor cannot swap two agents at any timestep.
    problem = _problem(
        ("....." ,),
        starts={"R1": Cell(0, 0), "R2": Cell(0, 4)},
        goals={"R1": Cell(0, 4), "R2": Cell(0, 0)},
    )
    result = mapf_pibt.solve(problem)
    assert isinstance(result, RecoveryPlanningFailure)
    assert result.reason is RecoveryFailureReason.SOLVER_NO_SOLUTION


def test_solve_rejects_goal_on_obstacle_as_unsupported() -> None:
    problem = _problem(
        ("..#.." ,),
        starts={"R1": Cell(0, 0)},
        goals={"R1": Cell(0, 2)},  # obstacle cell
    )
    result = mapf_pibt.solve(problem)
    assert isinstance(result, RecoveryPlanningFailure)
    assert result.reason is RecoveryFailureReason.UNSUPPORTED_SCOPE


def test_missing_solver_stack_is_a_typed_failure(monkeypatch) -> None:
    def _boom():
        raise ImportError("numpy not installed")

    monkeypatch.setattr(mapf_pibt, "_import_pibt", _boom)
    problem = _problem(
        ("....." ,),
        starts={"R1": Cell(0, 0)},
        goals={"R1": Cell(0, 4)},
    )
    result = mapf_pibt.solve(problem)
    assert isinstance(result, RecoveryPlanningFailure)
    assert result.reason is RecoveryFailureReason.SOLVER_UNAVAILABLE
