from pathlib import Path

import pytest

from mapf_splice.confirm import ConfirmedWaitForGraph
from mapf_splice.deadlock import (
    ConfirmationOutcome,
    ContainmentState,
    DeadlockController,
    classify_confirmation,
    cyclic_sccs,
)
from mapf_splice.domain import (
    ActionRef,
    Cell,
    Plan,
    Robot,
    Task,
    TaskStatus,
    VertexResource,
)
from mapf_splice.planning import compile_path
from mapf_splice.preview import (
    PreviewAnalysis,
    PreviewContention,
    ProspectiveDependency,
    analyze_preview,
)
from mapf_splice.scenario import load_scenario
from mapf_splice.simulation import DeterministicSimulator
from mapf_splice.trace import EventKind
from mapf_splice.traffic import CommittedReservationLedger
from mapf_splice.world import WorldState

ROOT = Path(__file__).parents[1]


def _dependency(waiting: str, blocking: str) -> ProspectiveDependency:
    return ProspectiveDependency(
        waiting_robot_id=waiting,
        waiting_plan_version=1,
        preview_action_ref=ActionRef(waiting, 1, 1),
        blocking_robot_id=blocking,
        blocking_plan_version=1,
        resource=VertexResource(Cell(0, 0)),
        blocking_action_refs=(ActionRef(blocking, 1, 0),),
        occupied_blocker=False,
    )


def _analysis(*edges: tuple[str, str]) -> PreviewAnalysis:
    return PreviewAnalysis(
        dependencies=tuple(_dependency(*edge) for edge in edges),
        contentions=(),
    )


def test_stability_requires_consecutive_observations() -> None:
    cycle = _analysis(("R1", "R2"), ("R2", "R1"))
    versions = {"R1": 1, "R2": 1}
    controller = DeadlockController(2)

    first = controller.observe(cycle, versions)
    second = controller.observe(cycle, versions)

    assert first.observations[0].count == 1
    assert first.stable == ()
    assert second.observations[0].count == 2
    assert second.stable == ((("R1", 1), ("R2", 1)),)
    assert controller.observe(cycle, versions).stable == ()
    assert len(controller.containments) == 1
    immediate = DeadlockController(1).observe(cycle, versions)
    assert immediate.stable == ((("R1", 1), ("R2", 1)),)


def test_disappearance_membership_and_plan_version_reset_candidates() -> None:
    two = _analysis(("R1", "R2"), ("R2", "R1"))
    three = _analysis(
        ("R1", "R2"),
        ("R2", "R3"),
        ("R3", "R1"),
    )
    controller = DeadlockController(2)
    controller.observe(two, {"R1": 1, "R2": 1})

    disappeared = controller.observe(PreviewAnalysis((), ()), {"R1": 1, "R2": 1})
    assert disappeared.expired == ((("R1", 1), ("R2", 1)),)
    assert controller.observe(two, {"R1": 1, "R2": 1}).observations[0].count == 1

    changed = controller.observe(three, {"R1": 1, "R2": 1, "R3": 1})
    assert (("R1", 1), ("R2", 1)) in changed.expired
    assert changed.observations[0].count == 1

    versioned = controller.observe(three, {"R1": 2, "R2": 1, "R3": 1})
    assert (("R1", 1), ("R2", 1), ("R3", 1)) in versioned.expired
    assert versioned.observations[0].count == 1


def test_progress_keeps_count_only_when_recomputed_cycle_remains() -> None:
    cycle = _analysis(("R1", "R2"), ("R2", "R1"))
    controller = DeadlockController(3)
    versions = {"R1": 1, "R2": 1}

    assert controller.observe(cycle, versions).observations[0].count == 1
    assert controller.observe(cycle, versions).observations[0].count == 2
    reset = controller.observe(PreviewAnalysis((), ()), versions)
    assert reset.observations == ()
    assert controller.observe(cycle, versions).observations[0].count == 1


def test_contention_is_not_an_edge_and_evidence_is_preserved() -> None:
    contention = PreviewContention(
        VertexResource(Cell(0, 0)),
        ("R1", "R2"),
        (ActionRef("R1", 1, 0), ActionRef("R2", 1, 0)),
    )
    controller = DeadlockController(1)
    assert controller.observe(PreviewAnalysis((), (contention,)), {}).observations == ()

    analysis = _analysis(
        ("R1", "R2"),
        ("R1", "R3"),
        ("R2", "R1"),
        ("R3", "R1"),
    )
    update = controller.observe(analysis, {"R1": 1, "R2": 1, "R3": 1})
    assert len(update.observations[0].evidence) == 4


def _stale_containment_world() -> tuple[WorldState, DeadlockController, Plan]:
    robots = {
        robot_id: Robot(robot_id, Cell(row, 0), active_task_id=f"T{row}")
        for row, robot_id in enumerate(("R1", "R2"))
    }
    tasks = {
        f"T{row}": Task(
            f"T{row}",
            Cell(row, 0),
            Cell(row, 1),
            0,
            TaskStatus.ASSIGNED,
            robot_id,
        )
        for row, robot_id in enumerate(("R1", "R2"))
    }
    world = WorldState(
        reservations=CommittedReservationLedger(1),
        robots=robots,
        tasks=tasks,
    )
    for row, robot_id in enumerate(("R1", "R2")):
        plan = compile_path(
            (Cell(row, 0), Cell(row, 1)),
            robot_id=robot_id,
            plan_version=1,
            task_id=f"T{row}",
        )
        world.install_plan(plan)
        tasks[f"T{row}"].transition_to(TaskStatus.TO_PICKUP)
    controller = DeadlockController(1)
    controller.observe(
        _analysis(("R1", "R2"), ("R2", "R1")),
        {"R1": 1, "R2": 1},
    )
    replacement = compile_path(
        (Cell(0, 0), Cell(1, 0)),
        robot_id="R1",
        plan_version=2,
        task_id="T0",
    )
    world.install_plan(replacement)
    return world, controller, replacement


def test_new_plan_version_is_not_captured_by_stale_containment() -> None:
    world, controller, replacement = _stale_containment_world()

    controller.refresh(world)

    assert not controller.is_contained(replacement)
    assert controller.containments[0].state is ContainmentState.INVALIDATED


def test_snapshot_is_read_only_and_refresh_is_explicit() -> None:
    world, controller, _ = _stale_containment_world()

    # snapshot() serializes existing state; it must not invalidate the now-stale
    # containment as a side effect of being observed.
    assert controller.snapshot().containments[0].state is ContainmentState.DRAINING
    assert controller.containments[0].state is ContainmentState.DRAINING

    # Invalidation happens only when the control phase explicitly refreshes.
    controller.refresh(world)
    assert controller.containments[0].state is ContainmentState.INVALIDATED
    assert (
        controller.snapshot().containments[0].state is ContainmentState.INVALIDATED
    )


@pytest.mark.parametrize(
    ("horizon", "cyclic_tick", "stable_tick", "quiescence_tick"),
    [(3, 14, 16, 18), (4, 13, 15, 18), (5, 12, 14, 18)],
)
def test_runtime_hero_first_stable_scc_contains_all_three_robots(
    horizon: int,
    cyclic_tick: int,
    stable_tick: int,
    quiescence_tick: int,
) -> None:
    scenario = load_scenario(ROOT / "scenarios/compact-three-robot/scenario.json")
    assert scenario.data["execution"]["delay_schedule"]["probability"] == 0
    simulator = DeterministicSimulator.from_scenario(
        scenario,
        committed_horizon=horizon,
    )
    expected = "R1@2,R2@2,R3@2"

    for _ in range(50):
        simulator.tick()
        stable_events = [
            event
            for event in simulator.trace.events
            if event.kind is EventKind.STABLE_SCC_DETECTED
        ]
        if stable_events:
            break
    else:
        observed = [
            dict(event.details)
            for event in simulator.trace.events
            if event.kind is EventKind.PROSPECTIVE_SCC_OBSERVED
        ]
        pytest.fail(f"K={horizon} never reached hero SCC; observed={observed[-10:]}")

    first_cyclic = next(
        event
        for event in simulator.trace.events
        if event.kind is EventKind.PROSPECTIVE_SCC_OBSERVED
    )
    assert first_cyclic.tick == cyclic_tick
    assert dict(first_cyclic.details)["members"] == "R1@2,R3@2"
    assert stable_events[0].tick == stable_tick
    assert dict(stable_events[0].details)["members"] == expected
    containment = [
        event
        for event in simulator.trace.events
        if event.kind is EventKind.CONTAINMENT_STARTED
    ]
    assert [(dict(event.details)["members"], event.tick) for event in containment] == [
        (expected, stable_tick)
    ]

    analysis = analyze_preview(simulator.world)
    assert ("R1", "R2", "R3") in cyclic_sccs(analysis)
    internal = [
        dependency
        for dependency in analysis.dependencies
        if dependency.waiting_robot_id in {"R1", "R2", "R3"}
        and dependency.blocking_robot_id in {"R1", "R2", "R3"}
    ]
    assert (
        len({(item.waiting_robot_id, item.blocking_robot_id) for item in internal}) >= 3
    )
    assert any(
        len(
            {
                item.blocking_robot_id
                for item in internal
                if item.waiting_robot_id == robot_id
            }
        )
        >= 2
        for robot_id in ("R1", "R2", "R3")
    )

    for _ in range(20):
        simulator.tick()
        quiescent = [
            event
            for event in simulator.trace.events
            if event.kind is EventKind.QUIESCENCE_REACHED
        ]
        if quiescent:
            break
    assert [(dict(event.details)["members"], event.tick) for event in quiescent] == [
        (expected, quiescence_tick)
    ]
    for robot_id in ("R1", "R2", "R3"):
        robot = simulator.world.robots[robot_id]
        assert robot.active_action_ref is None
        assert simulator.world.reservations.committed_actions(robot_id, 2) == ()
        assert any(
            action.status.value == "planned"
            for action in simulator.world.plans[robot_id].actions
        )
    assert any(
        event.kind is EventKind.ACTION_COMPLETED
        and event.robot_id in {"R1", "R2", "R3"}
        and event.tick > stable_tick
        for event in simulator.trace.events
    )
    assert not any(
        event.kind is EventKind.ADMISSION_ACCEPTED
        and event.robot_id in {"R1", "R2", "R3"}
        and event.tick > stable_tick
        for event in simulator.trace.events
    )


def test_hero_routes_do_not_change_with_committed_horizon() -> None:
    scenario = load_scenario(ROOT / "scenarios/compact-three-robot/scenario.json")
    paths = []
    for horizon in (3, 4, 5):
        simulator = DeterministicSimulator.from_scenario(
            scenario,
            committed_horizon=horizon,
        )
        for _ in range(13):
            simulator.tick()
        assert {
            robot_id: plan.version for robot_id, plan in simulator.world.plans.items()
        } == {"R1": 2, "R2": 2, "R3": 2}
        paths.append(
            {
                robot_id: tuple((action.start, action.end) for action in plan.actions)
                for robot_id, plan in sorted(simulator.world.plans.items())
            }
        )
    assert paths[0] == paths[1] == paths[2]


def test_containment_emits_quiescence_only_once() -> None:
    scenario = load_scenario(ROOT / "scenarios/compact-three-robot/scenario.json")
    simulator = DeterministicSimulator.from_scenario(scenario, committed_horizon=3)
    members = "R1@2,R2@2,R3@2"
    for _ in range(40):
        simulator.tick()
        quiescent = [
            event
            for event in simulator.trace.events
            if event.kind is EventKind.QUIESCENCE_REACHED
            and dict(event.details).get("members") == members
        ]
        if quiescent:
            break
    assert len(quiescent) == 1
    for _ in range(10):
        simulator.tick()
    assert (
        len(
            [
                event
                for event in simulator.trace.events
                if event.kind is EventKind.QUIESCENCE_REACHED
                and dict(event.details).get("members") == members
            ]
        )
        == 1
    )


def _quiescent_scope(routes):
    """A controller with an already-quiescent containment over `routes`' robots.

    (compile_path is already imported at the top of this module.)
    """
    starts = {robot_id: route[0] for robot_id, route in routes.items()}
    robots = {
        robot_id: Robot(robot_id, start, active_task_id=f"T-{robot_id}")
        for robot_id, start in starts.items()
    }
    tasks = {
        f"T-{robot_id}": Task(
            f"T-{robot_id}", start, routes[robot_id][-1], 0,
            TaskStatus.ASSIGNED, robot_id,
        )
        for robot_id, start in starts.items()
    }
    world = WorldState(
        reservations=CommittedReservationLedger(1), robots=robots, tasks=tasks
    )
    for robot_id in sorted(routes):
        plan = compile_path(
            routes[robot_id], robot_id=robot_id, plan_version=1,
            task_id=f"T-{robot_id}",
        )
        world.install_plan(plan)
        tasks[f"T-{robot_id}"].transition_to(TaskStatus.TO_PICKUP)
    controller = DeadlockController(1)
    scope = tuple((robot_id, 1) for robot_id in sorted(routes))
    edges = tuple((a, b) for a in sorted(routes) for b in sorted(routes) if a != b)
    controller.observe(_analysis(*edges), {robot_id: 1 for robot_id in routes})
    controller.refresh(world)
    controller.newly_quiescent(world)
    return controller, world, scope


def test_confirm_marks_hard_deadlock_on_internal_cycle() -> None:
    controller, world, scope = _quiescent_scope(
        {"R1": (Cell(0, 0), Cell(0, 1)), "R2": (Cell(0, 1), Cell(0, 0))}
    )
    results = controller.confirm(world, tick=18)
    assert len(results) == 1
    assert results[0].outcome is ConfirmationOutcome.CONFIRMED_DEADLOCK
    assert controller.containments[0].state is ContainmentState.CONFIRMED_DEADLOCK
    assert controller.containments[0].confirmation_tick == 18


def test_confirm_clears_when_graph_is_acyclic_and_local() -> None:
    controller, world, scope = _quiescent_scope(
        {"R1": (Cell(0, 0), Cell(0, 1)), "R2": (Cell(2, 0), Cell(2, 1))}
    )
    results = controller.confirm(world, tick=12)
    assert results[0].outcome is ConfirmationOutcome.CLEAR
    assert controller.containments[0].state is ContainmentState.CLEARED


def test_cleared_containment_is_pruned_and_counts_reset() -> None:
    controller, world, scope = _quiescent_scope(
        {"R1": (Cell(0, 0), Cell(0, 1)), "R2": (Cell(2, 0), Cell(2, 1))}
    )
    controller.confirm(world, tick=12)
    controller.prune_resolved()
    assert controller.containments == ()
    update = controller.observe(
        _analysis(("R1", "R2"), ("R2", "R1")), {"R1": 1, "R2": 1}
    )
    assert update.observations[0].count == 1


def test_external_blocked_holds_then_reevaluates_to_clear() -> None:
    # A 2-robot scope (so a cyclic SCC can form the containment), but the
    # confirmed graph has no internal cycle -- only R1 blocked by external R3.
    controller, world, scope = _quiescent_scope(
        {"R1": (Cell(0, 0), Cell(0, 1)), "R2": (Cell(5, 0), Cell(5, 1))}
    )
    world.robots["R3"] = Robot("R3", Cell(0, 1))  # external blocker on R1's target
    world.validate()
    first = controller.confirm(world, tick=5)
    assert first[0].outcome is ConfirmationOutcome.EXTERNAL_DEPENDENCY
    assert controller.containments[0].state is ContainmentState.EXTERNAL_BLOCKED
    # external robot leaves; re-evaluation clears
    world.robots["R3"].position = Cell(9, 9)
    world.validate()
    second = controller.confirm(world, tick=6)
    assert second[0].outcome is ConfirmationOutcome.CLEAR
    assert controller.containments[0].state is ContainmentState.CLEARED


def test_overlapping_scc_does_not_accumulate_while_suppressed() -> None:
    controller = DeadlockController(2)
    controller.observe(_analysis(("R1", "R2"), ("R2", "R1")), {"R1": 1, "R2": 1})
    controller.observe(_analysis(("R1", "R2"), ("R2", "R1")), {"R1": 1, "R2": 1})
    assert controller.containments[0].state is ContainmentState.DRAINING
    superset = _analysis(("R1", "R2"), ("R2", "R3"), ("R3", "R1"))
    versions = {"R1": 1, "R2": 1, "R3": 1}
    controller.observe(superset, versions)
    second = controller.observe(superset, versions)
    superset_obs = next(
        o
        for o in second.observations
        if o.identity == (("R1", 1), ("R2", 1), ("R3", 1))
    )
    assert superset_obs.suppressed is True
    assert superset_obs.count == 0


def test_classify_confirmation_prefers_internal_cycle() -> None:
    graph = ConfirmedWaitForGraph(
        scope=(("R1", 1), ("R2", 1)), epoch=1, captured_at_tick=0, edges=(),
        cyclic_sccs=(("R1", "R2"),),
    )
    assert classify_confirmation(graph) is ConfirmationOutcome.CONFIRMED_DEADLOCK


@pytest.mark.parametrize("horizon", [3, 4, 5])
def test_hero_reaches_quiescence_then_confirms(horizon: int) -> None:
    scenario = load_scenario(ROOT / "scenarios/compact-three-robot/scenario.json")
    simulator = DeterministicSimulator.from_scenario(
        scenario, committed_horizon=horizon
    )
    for _ in range(60):
        simulator.tick()
        built = [
            event
            for event in simulator.trace.events
            if event.kind is EventKind.CONFIRMED_WAIT_FOR_BUILT
        ]
        if built:
            break
    assert built, f"K={horizon} never ran confirmation"
    outcome = dict(built[0].details)["outcome"]
    members = dict(built[0].details)["members"]
    assert members == "R1@2,R2@2,R3@2"
    # Empirical outcome of the confirmation algorithm on the hero scenario:
    # the three robots form a real internal-cycle hard reservation deadlock.
    assert outcome == "confirmed-deadlock"
    hard = [
        event
        for event in simulator.trace.events
        if event.kind is EventKind.HARD_DEADLOCK_CONFIRMED
    ]
    assert [dict(event.details)["members"] for event in hard] == ["R1@2,R2@2,R3@2"]
    assert (
        simulator.deadlock_controller.containments[0].state
        is ContainmentState.CONFIRMED_DEADLOCK
    )
