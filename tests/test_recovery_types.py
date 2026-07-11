"""Solver-neutral MAPF problem/solution/proposal/failure types."""
from dataclasses import replace

import pytest

from mapf_splice.confirm import ConfirmedWaitForGraph
from mapf_splice.domain import ActionRef, Cell, DomainError
from mapf_splice.planning import compile_path
from mapf_splice.recovery import (
    DEFAULT_RECOVERY_MAX_TIMESTEP,
    DEFAULT_RECOVERY_SEED,
    PYPIBT_SOURCE_COMMIT,
    ConfirmedRecoveryIncident,
    RecoveryFailureReason,
    RecoveryIncidentRef,
    RecoveryPlanningFailure,
    RecoveryProposal,
    RecoverySolverMetadata,
    RecoveryState,
    ScopedMapfProblem,
    ScopedMapfSolution,
)
from mapf_splice.scenario import WarehouseMap

MAP = WarehouseMap(rows=("....", "....", "...."))


def _confirmed_incident(
    core, scope, *, graph_scope=None, tick=7, graph_tick=None, sccs=None
):
    return ConfirmedRecoveryIncident(
        RecoveryIncidentRef(core, scope, tick),
        ConfirmedWaitForGraph(
            graph_scope if graph_scope is not None else scope,
            tick if graph_tick is None else graph_tick,
            (),
            (("R1", "R2"),) if sccs is None else sccs,
        ),
    )


@pytest.mark.parametrize(
    ("core", "scope"),
    [
        ((), (("R1", 1), ("R2", 1))),
        ((("R1", 1),), (("R1", 1), ("R2", 1))),
        ((("R1", 1), ("R1", 1)), (("R1", 1), ("R2", 1))),
        ((("R2", 1), ("R1", 1)), (("R1", 1), ("R2", 1))),
        ((("R1", 1), ("R3", 1)), (("R1", 1), ("R2", 1))),
    ],
)
def test_incident_rejects_malformed_trigger_core(core, scope) -> None:
    with pytest.raises(DomainError):
        _confirmed_incident(core, scope)


@pytest.mark.parametrize(
    "overrides",
    [
        {"sccs": (("R1",),)},
        {"sccs": (("R1", "R1"),)},
        {"sccs": (("R1", "R3"),)},
        {"sccs": ()},
        {"graph_scope": (("R1", 1), ("R3", 1))},
        {"graph_tick": 8},
    ],
)
def test_incident_rejects_malformed_confirmed_graph(overrides) -> None:
    with pytest.raises(DomainError):
        _confirmed_incident(
            (("R1", 1), ("R2", 1)),
            (("R1", 1), ("R2", 1)),
            **overrides,
        )


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
        "install-failed",
        "installed",
        "executing",
        "admission-failed",
        "admission-stalled",
        "completed",
    }


def test_proposal_holds_validated_paths_and_plans() -> None:
    proposal = _valid_proposal()
    assert proposal.plans["R1"].version == 3
    assert proposal.metadata.solver == "pibt"


def _valid_proposal() -> RecoveryProposal:
    solution = _solution()
    plans = {
        "R1": compile_path(
            solution.paths["R1"], robot_id="R1", plan_version=3, task_id="TA"
        ),
        "R2": compile_path(
            solution.paths["R2"], robot_id="R2", plan_version=3, task_id="TB"
        ),
    }
    return RecoveryProposal(
        incident_ref=RecoveryIncidentRef(
            (("R1", 2), ("R2", 2)), (("R1", 2), ("R2", 2)), 7
        ),
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


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: replace(p, robot_id="RX"),
        lambda p: replace(p, version=4),
        lambda p: replace(
            p,
            actions=(
                replace(p.actions[0], ref=ActionRef(p.robot_id, p.version, 1)),
            ),
        ),
        lambda p: replace(p, phase_goal=Cell(2, 2)),
        lambda p: replace(
            p, actions=(replace(p.actions[0], start=Cell(2, 2)),)
        ),
        lambda p: replace(
            p, actions=(replace(p.actions[0], end=Cell(2, 2)),)
        ),
        lambda p: replace(
            p,
            actions=(
                replace(
                    p.actions[0],
                    dependencies=(ActionRef("RX", p.version, 0),),
                ),
            ),
        ),
    ],
)
def test_proposal_rejects_specs_that_diverge_from_solution(mutation) -> None:
    valid = _valid_proposal()
    plans = dict(valid.plans)
    plans["R1"] = mutation(plans["R1"])
    with pytest.raises(DomainError):
        RecoveryProposal(
            incident_ref=valid.incident_ref,
            expected_plan_versions=valid.expected_plan_versions,
            starts=valid.starts,
            goals=valid.goals,
            solution=valid.solution,
            plans=plans,
            metadata=valid.metadata,
        )


def test_proposal_rejects_cross_plan_dependency_cycle() -> None:
    valid = _valid_proposal()
    plans = dict(valid.plans)
    first, second = plans["R1"], plans["R2"]
    plans["R1"] = replace(
        first,
        actions=(
            replace(first.actions[0], dependencies=(second.actions[0].ref,)),
        ),
    )
    plans["R2"] = replace(
        second,
        actions=(
            replace(second.actions[0], dependencies=(first.actions[0].ref,)),
        ),
    )
    with pytest.raises(DomainError):
        RecoveryProposal(
            incident_ref=valid.incident_ref,
            expected_plan_versions=valid.expected_plan_versions,
            starts=valid.starts,
            goals=valid.goals,
            solution=valid.solution,
            plans=plans,
            metadata=valid.metadata,
        )
