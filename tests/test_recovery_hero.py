"""Hero K=3/4/5 end-to-end recovery-proposal acceptance and read-only parity."""
from pathlib import Path

import pytest

pytest.importorskip("numpy")

from mapf_splice.deadlock import ContainmentState  # noqa: E402
from mapf_splice.domain import Cell  # noqa: E402
from mapf_splice.recovery import (  # noqa: E402
    RecoveryProposal,
    RecoveryState,
    plan_recovery,
    validate_synchronized_solution,
)
from mapf_splice.scenario import load_scenario  # noqa: E402
from mapf_splice.simulation import DeterministicSimulator  # noqa: E402
from mapf_splice.trace import EventKind  # noqa: E402

ROOT = Path(__file__).parents[1]
SCENARIO = ROOT / "scenarios/compact-three-robot/scenario.json"

EXPECTED_SCOPE = (("R1", 2), ("R2", 2), ("R3", 2))
EXPECTED_STARTS = {"R1": Cell(10, 7), "R2": Cell(10, 8), "R3": Cell(11, 8)}
EXPECTED_GOALS = {"R1": Cell(12, 16), "R2": Cell(12, 4), "R3": Cell(7, 2)}


def _run_to_proposal(horizon: int) -> DeterministicSimulator:
    scenario = load_scenario(SCENARIO)
    sim = DeterministicSimulator.from_scenario(scenario, committed_horizon=horizon)
    for _ in range(60):
        sim.tick()
        if any(
            event.kind is EventKind.RECOVERY_PROPOSAL_READY
            for event in sim.trace.events
        ):
            return sim
    pytest.fail(f"K={horizon} never produced a recovery proposal")


@pytest.mark.parametrize("horizon", [3, 4, 5])
def test_hero_confirms_and_proposes_over_full_scope(horizon: int) -> None:
    sim = _run_to_proposal(horizon)
    containment = sim.deadlock_controller.containment

    # Confirmation / quiescence timing and scope.
    quiescence = [
        e for e in sim.trace.events if e.kind is EventKind.QUIESCENCE_REACHED
    ]
    assert quiescence[0].tick == 18
    assert containment.confirmation_tick == 18
    assert containment.state is ContainmentState.CONFIRMED_DEADLOCK
    assert containment.identity == EXPECTED_SCOPE

    # Confirmed graph: 3 edges, cyclic SCC is R1<->R2, R3 transitively blocked.
    graph = containment.confirmed_graph
    assert len(graph.edges) == 3
    assert graph.cyclic_sccs == (("R1", "R2"),)
    assert "R3" not in {r for scc in graph.cyclic_sccs for r in scc}

    # Recovery proposal covers all three robots.
    assert containment.recovery_state is RecoveryState.PROPOSAL_READY
    proposal = containment.recovery_proposal
    assert isinstance(proposal, RecoveryProposal)
    assert proposal.identity == EXPECTED_SCOPE
    assert set(proposal.plans) == {"R1", "R2", "R3"}
    assert set(proposal.solution.robot_ids) == {"R1", "R2", "R3"}

    # Starts = authoritative tick-18 positions; goals = current task-phase goals.
    assert proposal.starts == EXPECTED_STARTS
    assert proposal.goals == EXPECTED_GOALS
    assert proposal.expected_plan_versions == {"R1": 2, "R2": 2, "R3": 2}

    # Paths reach every goal; makespan is the empirical hero value.
    for robot_id in ("R1", "R2", "R3"):
        path = proposal.solution.paths[robot_id]
        assert path[0] == EXPECTED_STARTS[robot_id]
        assert path[-1] == EXPECTED_GOALS[robot_id]
    assert proposal.solution.makespan == 12
    assert proposal.metadata.seed == 0
    assert proposal.metadata.solver == "pibt"

    # Project validation passes and ADG plans target new (uninstalled) versions.
    validate_synchronized_solution(proposal.solution, problem=_rebuild_problem(sim))
    for robot_id in ("R1", "R2", "R3"):
        plan = proposal.plans[robot_id]
        assert plan.version == 3
        assert plan.phase_goal == EXPECTED_GOALS[robot_id]
        assert sim.world.robots[robot_id].plan_version == 2  # not installed


def _rebuild_problem(sim: DeterministicSimulator):
    # Reconstruct the exact problem the planner used, to re-run validation.
    from mapf_splice.recovery import DEFAULT_RECOVERY_MAX_TIMESTEP, ScopedMapfProblem

    return ScopedMapfProblem(
        robot_ids=("R1", "R2", "R3"),
        starts=EXPECTED_STARTS,
        goals=EXPECTED_GOALS,
        warehouse_map=sim.warehouse_map,
        max_timestep=DEFAULT_RECOVERY_MAX_TIMESTEP,
        seed=0,
    )


def test_hero_recovery_is_reproducible() -> None:
    first = _run_to_proposal(3).deadlock_controller.containment.recovery_proposal
    second = _run_to_proposal(3).deadlock_controller.containment.recovery_proposal
    assert first == second


@pytest.mark.parametrize("horizon", [3, 4, 5])
def test_recovery_planning_is_read_only_snapshot_parity(horizon: int) -> None:
    sim = _run_to_proposal(horizon)
    world = sim.world

    def snapshot():
        return (
            {r: rob.position for r, rob in world.robots.items()},
            {r: rob.plan_version for r, rob in world.robots.items()},
            {r: rob.active_task_id for r, rob in world.robots.items()},
            {r: rob.payload_task_id for r, rob in world.robots.items()},
            {t: task.status for t, task in world.tasks.items()},
            {r: plan.version for r, plan in world.plans.items()},
            world.reservations.reservation_snapshot(),
        )

    before = snapshot()
    # Re-run the planner explicitly (the runtime already ran it once); it must
    # neither install plans nor mutate any authoritative state.
    identity = sim.deadlock_controller.containment.identity
    result = plan_recovery(world, identity, sim.warehouse_map)
    assert isinstance(result, RecoveryProposal)
    assert snapshot() == before
    # Deterministic: the re-run matches the runtime's stored proposal.
    assert result == sim.deadlock_controller.containment.recovery_proposal
