import pytest

from mapf_splice.confirm import (
    ConfirmationError,
    build_confirmed_wait_for,
    cyclic_components,
)
from mapf_splice.domain import (
    ActionStatus,
    Cell,
    Robot,
    Task,
    TaskStatus,
    VertexResource,
)
from mapf_splice.planning import compile_path
from mapf_splice.traffic import CommittedReservationLedger
from mapf_splice.world import WorldState


def _world(routes: dict[str, tuple[Cell, ...]], *, admit: bool) -> WorldState:
    starts = {robot_id: route[0] for robot_id, route in routes.items()}
    robots = {
        robot_id: Robot(robot_id, start, active_task_id=f"T-{robot_id}")
        for robot_id, start in starts.items()
    }
    tasks = {
        f"T-{robot_id}": Task(
            f"T-{robot_id}",
            start,
            routes[robot_id][-1],
            0,
            TaskStatus.ASSIGNED,
            robot_id,
        )
        for robot_id, start in starts.items()
    }
    world = WorldState(
        reservations=CommittedReservationLedger(horizon=1),
        robots=robots,
        tasks=tasks,
    )
    plans = []
    for robot_id in sorted(routes):
        plan = compile_path(
            routes[robot_id],
            robot_id=robot_id,
            plan_version=1,
            task_id=f"T-{robot_id}",
        )
        world.install_plan(plan)
        tasks[f"T-{robot_id}"].transition_to(TaskStatus.TO_PICKUP)
        plans.append(plan)
    if admit:
        world.reservations.acquire_initial_batch(
            plans, occupied=world.occupied_cells()
        )
    return world


def test_cyclic_components_finds_multi_node_cycles_only() -> None:
    assert cyclic_components([("R1", "R2"), ("R2", "R1")]) == (("R1", "R2"),)
    assert cyclic_components([("R1", "R2"), ("R2", "R3")]) == ()


def test_cyclic_components_is_deterministic_over_input_order() -> None:
    forward = cyclic_components([("R1", "R2"), ("R2", "R3"), ("R3", "R1")])
    reverse = cyclic_components([("R3", "R1"), ("R2", "R3"), ("R1", "R2")])
    assert forward == reverse == (("R1", "R2", "R3"),)


def test_confirmed_graph_records_mutual_occupancy_cycle() -> None:
    world = _world(
        {"R1": (Cell(0, 0), Cell(0, 1)), "R2": (Cell(0, 1), Cell(0, 0))},
        admit=False,
    )
    scope = (("R1", 1), ("R2", 1))
    graph = build_confirmed_wait_for(world, scope, tick=7)

    assert graph.captured_at_tick == 7
    pairs = {(e.waiting_robot_id, e.blocking_robot_id) for e in graph.edges}
    assert pairs == {("R1", "R2"), ("R2", "R1")}
    assert graph.cyclic_sccs == (("R1", "R2"),)
    r1_to_r2 = next(
        e
        for e in graph.edges
        if e.waiting_robot_id == "R1" and e.blocking_robot_id == "R2"
    )
    assert r1_to_r2.occupied_blocker is True
    assert r1_to_r2.committed_blocker_refs == ()
    assert r1_to_r2.blocking_in_scope is True
    assert r1_to_r2.resource == VertexResource(Cell(0, 1))


def test_confirmed_graph_clears_when_next_cells_are_free() -> None:
    world = _world(
        {"R1": (Cell(0, 0), Cell(0, 1)), "R2": (Cell(2, 0), Cell(2, 1))},
        admit=False,
    )
    graph = build_confirmed_wait_for(world, (("R1", 1), ("R2", 1)), tick=3)
    assert graph.edges == ()
    assert graph.cyclic_sccs == ()


def test_confirmed_graph_marks_out_of_scope_blocker() -> None:
    world = _world(
        {"R1": (Cell(0, 0), Cell(0, 1)), "R2": (Cell(0, 1), Cell(0, 2))},
        admit=False,
    )
    graph = build_confirmed_wait_for(world, (("R1", 1),), tick=4)
    edge = next(e for e in graph.edges if e.blocking_robot_id == "R2")
    assert edge.blocking_in_scope is False
    assert graph.cyclic_sccs == ()


def test_confirmed_graph_records_committed_blocker_refs() -> None:
    world = _world(
        {"R1": (Cell(0, 0), Cell(0, 1)), "R2": (Cell(0, 2), Cell(0, 1))},
        admit=False,
    )
    world.reservations.acquire_initial_batch(
        (world.plans["R2"],), occupied=world.occupied_cells()
    )
    graph = build_confirmed_wait_for(world, (("R1", 1),), tick=9)
    edge = next(
        e
        for e in graph.edges
        if e.resource == VertexResource(Cell(0, 1)) and e.blocking_robot_id == "R2"
    )
    assert edge.committed_blocker_refs != ()
    assert edge.occupied_blocker is False
    assert all(ref.plan_version >= 1 for ref in edge.committed_blocker_refs)


def test_confirmed_graph_excludes_self_ownership() -> None:
    world = _world({"R1": (Cell(0, 0), Cell(0, 0))}, admit=False)
    graph = build_confirmed_wait_for(world, (("R1", 1),), tick=1)
    assert graph.edges == ()


def test_confirmed_builder_rejects_non_planned_next_action() -> None:
    world = _world({"R1": (Cell(0, 0), Cell(0, 1))}, admit=True)
    world.plans["R1"].actions[0].transition_to(ActionStatus.RUNNING)
    with pytest.raises(ConfirmationError, match="planned"):
        build_confirmed_wait_for(world, (("R1", 1),), tick=1)


def test_confirmed_graph_allows_zero_version_idle_occupancy_blocker() -> None:
    world = _world({"R1": (Cell(0, 0), Cell(0, 1))}, admit=False)
    world.robots["R2"] = Robot("R2", Cell(0, 1))
    world.validate()
    graph = build_confirmed_wait_for(world, (("R1", 1),), tick=1)
    edge = next(e for e in graph.edges if e.blocking_robot_id == "R2")
    assert edge.blocking_plan_version == 0
    assert edge.occupied_blocker is True


def test_build_confirmed_wait_for_is_read_only() -> None:
    # The confirm path only classifies and records: it never replaces a plan or
    # mutates reservations (no MAPF, no plan replacement in v0.1).
    world = _world(
        {"R1": (Cell(0, 0), Cell(0, 1)), "R2": (Cell(0, 1), Cell(0, 0))},
        admit=False,
    )
    versions_before = {rid: r.plan_version for rid, r in world.robots.items()}
    committed_before = world.reservations.all_committed_actions()
    plans_before = dict(world.plans)

    build_confirmed_wait_for(world, (("R1", 1), ("R2", 1)), tick=1)

    assert {rid: r.plan_version for rid, r in world.robots.items()} == versions_before
    assert world.reservations.all_committed_actions() == committed_before
    assert world.plans == plans_before
