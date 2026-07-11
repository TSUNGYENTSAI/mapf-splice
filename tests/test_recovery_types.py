"""Solver-neutral MAPF problem/solution/proposal/failure types."""
import pytest

from mapf_splice.domain import Cell, DomainError
from mapf_splice.planning import compile_path
from mapf_splice.recovery import (
    DEFAULT_RECOVERY_MAX_TIMESTEP,
    DEFAULT_RECOVERY_SEED,
    PYPIBT_SOURCE_COMMIT,
    RecoveryFailureReason,
    RecoveryPlanningFailure,
    RecoveryProposal,
    RecoverySolverMetadata,
    RecoveryState,
    ScopedMapfProblem,
    ScopedMapfSolution,
)
from mapf_splice.scenario import WarehouseMap

MAP = WarehouseMap(rows=("....", "....", "...."))


def _problem() -> ScopedMapfProblem:
    return ScopedMapfProblem(
        robot_ids=("R1", "R2"),
        starts={"R1": Cell(0, 0), "R2": Cell(0, 3)},
        goals={"R1": Cell(0, 3), "R2": Cell(0, 0)},
        warehouse_map=MAP,
        max_timestep=64,
        seed=0,
    )


def _solution() -> ScopedMapfSolution:
    return ScopedMapfSolution(
        robot_ids=("R1", "R2"),
        paths={
            "R1": (Cell(0, 0), Cell(0, 1)),
            "R2": (Cell(0, 3), Cell(0, 2)),
        },
        makespan=1,
    )


def test_problem_constructs_and_defaults_exist() -> None:
    problem = _problem()
    assert problem.robot_ids == ("R1", "R2")
    assert DEFAULT_RECOVERY_SEED == 0
    assert DEFAULT_RECOVERY_MAX_TIMESTEP >= 1
    assert PYPIBT_SOURCE_COMMIT == "a3c97f60413c6619a29a5022969896bc54877edc"


@pytest.mark.parametrize(
    "robot_ids",
    [(), ("R2", "R1"), ("R1", "R1")],
)
def test_problem_requires_sorted_unique_nonempty_ids(robot_ids) -> None:
    with pytest.raises(DomainError):
        ScopedMapfProblem(
            robot_ids=robot_ids,
            starts={r: Cell(0, 0) for r in robot_ids},
            goals={r: Cell(0, 1) for r in robot_ids},
            warehouse_map=MAP,
            max_timestep=64,
            seed=0,
        )


def test_problem_requires_matching_start_goal_keys() -> None:
    with pytest.raises(DomainError):
        ScopedMapfProblem(
            robot_ids=("R1", "R2"),
            starts={"R1": Cell(0, 0)},
            goals={"R1": Cell(0, 1), "R2": Cell(0, 2)},
            warehouse_map=MAP,
            max_timestep=64,
            seed=0,
        )


def test_problem_requires_positive_max_timestep() -> None:
    with pytest.raises(DomainError):
        ScopedMapfProblem(
            robot_ids=("R1",),
            starts={"R1": Cell(0, 0)},
            goals={"R1": Cell(0, 1)},
            warehouse_map=MAP,
            max_timestep=0,
            seed=0,
        )


def test_solution_constructs() -> None:
    solution = _solution()
    assert solution.makespan == 1
    assert set(solution.paths) == {"R1", "R2"}


def test_solution_requires_synchronized_lengths_matching_makespan() -> None:
    with pytest.raises(DomainError):
        ScopedMapfSolution(
            robot_ids=("R1", "R2"),
            paths={
                "R1": (Cell(0, 0), Cell(0, 1)),
                "R2": (Cell(0, 3),),
            },
            makespan=1,
        )


def test_failure_carries_reason_and_detail() -> None:
    failure = RecoveryPlanningFailure(
        reason=RecoveryFailureReason.SOLVER_NO_SOLUTION,
        detail="ran out of timesteps",
    )
    assert failure.reason.value == "solver-did-not-find-supported-solution"
    assert "timesteps" in failure.detail


def test_recovery_state_values() -> None:
    assert {s.value for s in RecoveryState} == {
        "not-attempted",
        "proposal-ready",
        "unsupported-or-failed",
    }


def test_proposal_holds_validated_paths_and_plans() -> None:
    solution = _solution()
    plans = {
        "R1": compile_path(
            solution.paths["R1"], robot_id="R1", plan_version=3, task_id="TA"
        ),
        "R2": compile_path(
            solution.paths["R2"], robot_id="R2", plan_version=3, task_id="TB"
        ),
    }
    proposal = RecoveryProposal(
        identity=(("R1", 2), ("R2", 2)),
        expected_plan_versions={"R1": 2, "R2": 2},
        starts={"R1": Cell(0, 0), "R2": Cell(0, 3)},
        goals={"R1": Cell(0, 1), "R2": Cell(0, 2)},
        solution=solution,
        plans=plans,
        metadata=RecoverySolverMetadata(
            solver="pibt",
            seed=0,
            max_timestep=64,
            makespan=1,
            source_commit=PYPIBT_SOURCE_COMMIT,
        ),
    )
    assert proposal.plans["R1"].version == 3
    assert proposal.metadata.solver == "pibt"
