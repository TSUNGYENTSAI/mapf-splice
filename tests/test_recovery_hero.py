"""Hero proposal generation, atomic installation, and K-admission boundary."""
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import MappingProxyType

import pytest

pytest.importorskip("numpy")

from mapf_splice.deadlock import ContainmentState  # noqa: E402
from mapf_splice.domain import (  # noqa: E402
    ActionStatus,  # noqa: E402
    Cell,
    Plan,
)
from mapf_splice.recovery import (  # noqa: E402
    RecoveryInstallFailure,
    RecoveryInstallFailureReason,
    RecoveryProposal,
    RecoveryState,
    commit_recovery_splice,
    validate_synchronized_solution,
)
from mapf_splice.replay import FrameRecorder  # noqa: E402
from mapf_splice.scenario import load_scenario  # noqa: E402
from mapf_splice.simulation import DeterministicSimulator  # noqa: E402
from mapf_splice.trace import EventKind  # noqa: E402
from mapf_splice.world import WorldStateError  # noqa: E402

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
    # Prospective trigger core and affected scope both span all three robots
    # (the hero forms a prospective 3-cycle); the confirmed cycle is smaller.
    assert containment.scope_identity == EXPECTED_SCOPE
    assert containment.trigger_core_identity == EXPECTED_SCOPE

    # Confirmed graph over the full scope: 3 edges, cyclic SCC is only R1<->R2,
    # R3 transitively blocked (confirmed cycle smaller than the scope).
    graph = containment.confirmed_graph
    assert {(m[0]) for m in graph.scope} == {"R1", "R2", "R3"}
    assert len(graph.edges) == 3
    assert graph.cyclic_sccs == (("R1", "R2"),)
    assert "R3" not in {r for scc in graph.cyclic_sccs for r in scc}

    # Recovery proposal covers all three robots.
    assert containment.recovery_state is RecoveryState.INSTALLED
    proposal = containment.recovery_proposal
    assert isinstance(proposal, RecoveryProposal)
    assert proposal.scope_identity == EXPECTED_SCOPE
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
        assert sim.world.robots[robot_id].plan_version == 3


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


def test_adg_recovery_uses_explicit_bounded_prefix_admission() -> None:
    sim = _run_to_proposal(3)
    proposal = sim.deadlock_controller.containment.recovery_proposal
    sim.tick()  # first ordinary admission attempt for the installed group

    result = sim.deadlock_controller.containment.recovery_admission
    grants = {robot.robot_id: len(robot.granted_actions) for robot in result.robots}
    assert grants == {"R1": 0, "R2": 0, "R3": 2}
    assert sim.world.reservations.plan_initialized(sim.world.plans["R3"])
    assert sim.deadlock_controller.containment.recovery_state is RecoveryState.EXECUTING
    assert any(
        event.kind is EventKind.ACTION_STARTED and event.robot_id == "R3"
        for event in sim.trace.events
    )
    assert proposal.plans["R1"].actions[0].dependencies == (
        proposal.plans["R2"].actions[0].ref,
    )
    assert proposal.plans["R2"].actions[0].dependencies == (
        proposal.plans["R3"].actions[0].ref,
    )
    assert proposal.plans["R3"].actions[2].dependencies == (
        proposal.plans["R1"].actions[1].ref,
        proposal.plans["R3"].actions[1].ref,
    )


def test_completed_state_replay_serialization_preserves_full_evidence() -> None:
    scenario = load_scenario(SCENARIO)
    sim = _run_to_proposal(3)
    sim.recorder = FrameRecorder(scenario)
    proposal = sim.deadlock_controller.containment.recovery_proposal
    for robot_id, plan in sim.world.plans.items():
        for action in plan.actions:
            action.transition_to(ActionStatus.RUNNING)
            action.transition_to(ActionStatus.COMPLETED)
        sim.world.robots[robot_id].position = proposal.goals[robot_id]

    completion_tick = sim.world.tick
    sim.tick()

    tick_frames = [
        frame for frame in sim.recorder.frames if frame["tick"] == completion_tick
    ]
    checkpoints = [frame["checkpoint"] for frame in tick_frames]
    assert checkpoints[:4] == [
        "tick-start",
        "after-completions",
        "after-release",
        "after-recovery-completion",
    ]
    assert checkpoints.index("after-recovery-completion") < checkpoints.index(
        "after-task-advance"
    )
    frame = next(
        frame
        for frame in tick_frames
        if frame["checkpoint"] == "after-recovery-completion"
    )
    recovery = frame["recovery"]
    assert frame["checkpoint"] == "after-recovery-completion"
    assert recovery["state"] == "completed"
    assert recovery["completion_tick"] == completion_tick
    assert recovery["incident"]["trigger_core"]
    assert recovery["incident"]["scope"]
    assert recovery["installed_plan_versions"]
    assert recovery["paths"]
    assert frame["confirmed_wait_for"] is not None
    assert sim.deadlock_controller.containment is None


def test_proposal_nested_specs_are_immutable() -> None:
    proposal = _run_to_proposal(3).deadlock_controller.containment.recovery_proposal
    with pytest.raises(FrozenInstanceError):
        proposal.plans["R1"].actions[0].end = Cell(0, 0)
    with pytest.raises(TypeError):
        proposal.plans["R4"] = proposal.plans["R1"]


def test_only_controller_recorded_proposal_can_be_installed() -> None:
    sim = _run_to_proposal(3)
    active = sim.deadlock_controller.containment
    recorded = active.recovery_proposal
    other = replace(recorded, metadata=replace(recorded.metadata, seed=99))
    active.recovery_state = RecoveryState.PROPOSAL_READY
    before = (
        sim.world.robots.copy(),
        sim.world.plans.copy(),
        sim.world.reservations.reservation_snapshot(),
        active.recovery_proposal,
    )

    failure = commit_recovery_splice(
        sim.world, sim.deadlock_controller, other, tick=sim.world.tick
    )

    assert isinstance(failure, RecoveryInstallFailure)
    assert failure.reason is RecoveryInstallFailureReason.INCIDENT_MISMATCH
    assert sim.world.robots == before[0]
    assert sim.world.plans == before[1]
    assert sim.world.reservations.reservation_snapshot() == before[2]
    assert active.recovery_proposal is before[3]


def test_last_group_participant_validation_failure_is_atomic() -> None:
    sim = _run_to_proposal(3)
    replacements = {}
    for robot_id, current in sorted(sim.world.plans.items()):
        replacements[robot_id] = Plan(
            robot_id,
            current.version + 1,
            current.task_id,
            current.phase_goal,
            (),
        )
    last = sorted(replacements)[-1]
    bad = replacements[last]
    replacements[last] = Plan(
        bad.robot_id, bad.version + 1, bad.task_id, bad.phase_goal, ()
    )
    before = (
        {key: replace(robot) for key, robot in sim.world.robots.items()},
        sim.world.plans.copy(),
        sim.world.tasks.copy(),
        sim.world.reservations.reservation_snapshot(),
    )

    with pytest.raises(WorldStateError):
        sim.world.replace_plan_group(replacements)

    assert sim.world.robots == before[0]
    assert sim.world.plans == before[1]
    assert sim.world.tasks == before[2]
    assert sim.world.reservations.reservation_snapshot() == before[3]


def test_install_failure_is_typed_and_replayed_with_detail() -> None:
    scenario = load_scenario(SCENARIO)
    sim = _run_to_proposal(3)
    active = sim.deadlock_controller.containment
    active.recovery_state = RecoveryState.PROPOSAL_READY
    failure = RecoveryInstallFailure(
        RecoveryInstallFailureReason.STALE_POSITION, "R2 moved"
    )
    sim.deadlock_controller.record_install_failure(failure, tick=19)
    recorder = FrameRecorder(scenario)
    recorder.record(
        checkpoint="after-recovery-install",
        world=sim.world,
        controller=sim.deadlock_controller,
        trace=sim.trace,
    )
    recovery = recorder.frames[-1]["recovery"]
    assert recovery["state"] == "install-failed"
    assert recovery["failure_reason"] == "stale-position"
    assert recovery["failure_detail"] == "R2 moved"
    assert recovery["failure_tick"] == 19


def test_malformed_spec_materialization_returns_typed_failure() -> None:
    sim = _run_to_proposal(3)
    active = sim.deadlock_controller.containment
    proposal = active.recovery_proposal
    plans = dict(proposal.plans)
    last = sorted(plans)[-1]
    spec = plans[last]
    plans[last] = replace(
        spec,
        actions=(replace(spec.actions[0], duration_ticks=0), *spec.actions[1:]),
    )
    object.__setattr__(proposal, "plans", MappingProxyType(plans))
    active.recovery_state = RecoveryState.PROPOSAL_READY
    before_versions = {
        robot_id: robot.plan_version for robot_id, robot in sim.world.robots.items()
    }
    before_reservations = sim.world.reservations.reservation_snapshot()

    failure = commit_recovery_splice(
        sim.world, sim.deadlock_controller, proposal, tick=sim.world.tick
    )

    assert isinstance(failure, RecoveryInstallFailure)
    assert failure.reason is RecoveryInstallFailureReason.INVALID_REPLACEMENT_PLAN
    assert {
        robot_id: robot.plan_version for robot_id, robot in sim.world.robots.items()
    } == before_versions
    assert sim.world.reservations.reservation_snapshot() == before_reservations
    assert active.recovery_state is RecoveryState.PROPOSAL_READY


@pytest.mark.parametrize(
    "invalid",
    [
        "robot-version",
        "plan-version",
        "task",
        "remaining-ticks",
        "running",
        "planned",
        "reservation",
    ],
)
def test_completion_requires_exact_installed_generation(invalid: str) -> None:
    sim = _run_to_proposal(3)
    active = sim.deadlock_controller.containment
    robot = sim.world.robots["R1"]
    plan = sim.world.plans["R1"]
    for current in sim.world.plans.values():
        for action in current.actions:
            action.transition_to(ActionStatus.RUNNING)
            action.transition_to(ActionStatus.COMPLETED)
    if invalid == "robot-version":
        robot.plan_version += 1
    elif invalid == "plan-version":
        object.__setattr__(plan, "version", plan.version + 1)
    elif invalid == "task":
        object.__setattr__(plan, "task_id", "different-task")
    elif invalid == "remaining-ticks":
        robot.remaining_ticks = 1
    elif invalid == "running":
        plan.actions[0].status = ActionStatus.RUNNING
    elif invalid == "planned":
        plan.actions[0].status = ActionStatus.PLANNED
    else:
        sim.world.reservations._commit((plan.actions[0],))

    assert not sim.deadlock_controller.complete_recovery_if_done(
        sim.world, tick=sim.world.tick
    )
    assert active.recovery_state is RecoveryState.INSTALLED
    assert not any(
        event.kind is EventKind.RECOVERY_COMPLETED for event in sim.trace.events
    )


@pytest.mark.parametrize("horizon", [3, 4, 5])
def test_recorded_proposal_specs_are_detached_from_installed_plans(
    horizon: int,
) -> None:
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
    proposal = sim.deadlock_controller.containment.recovery_proposal
    assert proposal.plans["R1"].materialize() is not world.plans["R1"]
    assert snapshot() == before
