import pytest

from mapf_splice.dispatch import Assignment, dispatch_pending_tasks
from mapf_splice.domain import (
    ActionStatus,
    Cell,
    DomainError,
    Robot,
    Task,
    TaskStatus,
)
from mapf_splice.planning import compile_path
from mapf_splice.routing import NoPath
from mapf_splice.tasking import (
    complete_dropoff,
    complete_pickup,
    start_dropoff_leg,
    start_pickup_leg,
)
from mapf_splice.traffic import CommittedReservationLedger
from mapf_splice.world import WorldState, WorldStateError


def _world(*, robots: tuple[Robot, ...], tasks: tuple[Task, ...]) -> WorldState:
    return WorldState(
        reservations=CommittedReservationLedger(horizon=3),
        robots={robot.id: robot for robot in robots},
        tasks={task.id: task for task in tasks},
    )


def test_world_rejects_duplicate_authoritative_occupancy() -> None:
    with pytest.raises(WorldStateError, match="occupy"):
        _world(
            robots=(Robot("R1", Cell(0, 0)), Robot("R2", Cell(0, 0))),
            tasks=(),
        )


def test_dispatch_is_released_task_first_nearest_idle_then_robot_id() -> None:
    task = Task("T1", Cell(0, 1), Cell(0, 3), release_tick=0)
    future = Task("T2", Cell(0, 2), Cell(0, 3), release_tick=1)
    world = _world(
        robots=(Robot("R2", Cell(0, 0)), Robot("R1", Cell(0, 2))),
        tasks=(future, task),
    )
    floor = {Cell(0, col) for col in range(4)}

    assignments = dispatch_pending_tasks(
        world,
        is_traversable=floor.__contains__,
    )

    assert assignments == (Assignment("T1", "R1", 1),)
    assert task.status is TaskStatus.ASSIGNED
    assert world.robots["R1"].active_task_id == "T1"
    assert future.status is TaskStatus.PENDING


def test_task_phase_orchestration_preserves_every_status() -> None:
    pickup = Cell(0, 0)
    dropoff = Cell(0, 2)
    robot = Robot("R1", pickup)
    task = Task("T1", pickup, dropoff, release_tick=0)
    world = _world(robots=(robot,), tasks=(task,))
    floor = {Cell(0, col) for col in range(3)}

    dispatch_pending_tasks(world, is_traversable=floor.__contains__)
    assert task.status is TaskStatus.ASSIGNED

    pickup_plan = start_pickup_leg(
        world,
        "T1",
        is_traversable=floor.__contains__,
    )
    assert task.status is TaskStatus.TO_PICKUP
    assert pickup_plan.actions == ()
    assert robot.plan_version == 1

    complete_pickup(world, "T1")
    assert task.status is TaskStatus.CARRYING
    assert robot.payload_task_id == "T1"

    dropoff_plan = start_dropoff_leg(
        world,
        "T1",
        is_traversable=floor.__contains__,
    )
    assert task.status is TaskStatus.TO_DROPOFF
    assert len(dropoff_plan.actions) == 2
    assert robot.plan_version == 2

    robot.position = dropoff
    with pytest.raises(DomainError, match="before its plan"):
        complete_dropoff(world, "T1")
    assert task.status is TaskStatus.TO_DROPOFF

    for action in dropoff_plan.actions:
        action.transition_to(ActionStatus.RUNNING)
        action.transition_to(ActionStatus.COMPLETED)
    complete_dropoff(world, "T1")
    assert task.status is TaskStatus.COMPLETED
    assert robot.active_task_id is None
    assert robot.payload_task_id is None
    assert "R1" not in world.plans

    next_task = Task("T2", dropoff, pickup, release_tick=0)
    world.tasks[next_task.id] = next_task
    assert dispatch_pending_tasks(
        world,
        is_traversable=floor.__contains__,
    ) == (Assignment("T2", "R1", 0),)
    assert robot.active_task_id == "T2"
    assert robot.plan_version == 2


def test_failed_phase_route_does_not_change_status_or_plan_version() -> None:
    robot = Robot("R1", Cell(0, 0), active_task_id="T1")
    task = Task(
        "T1",
        Cell(0, 2),
        Cell(0, 3),
        release_tick=0,
        status=TaskStatus.ASSIGNED,
        assigned_robot_id="R1",
    )
    world = _world(robots=(robot,), tasks=(task,))
    disconnected = {Cell(0, 0), Cell(0, 2), Cell(0, 3)}

    result = start_pickup_leg(
        world,
        "T1",
        is_traversable=disconnected.__contains__,
    )

    assert isinstance(result, NoPath)
    assert task.status is TaskStatus.ASSIGNED
    assert robot.plan_version == 0
    assert world.plans == {}


def test_new_plan_cannot_install_while_old_plan_keeps_reservations() -> None:
    robot = Robot("R1", Cell(0, 0), active_task_id="T1")
    task = Task(
        "T1",
        Cell(0, 0),
        Cell(0, 2),
        release_tick=0,
        status=TaskStatus.ASSIGNED,
        assigned_robot_id="R1",
    )
    world = _world(robots=(robot,), tasks=(task,))
    first = compile_path(
        (Cell(0, 0), Cell(0, 1)),
        robot_id="R1",
        plan_version=1,
        task_id="T1",
    )
    world.install_plan(first)
    world.reservations.acquire_initial_batch(
        (first,),
        occupied=world.occupied_cells(),
    )
    replacement = compile_path(
        (Cell(0, 0), Cell(1, 0)),
        robot_id="R1",
        plan_version=2,
        task_id="T1",
    )

    with pytest.raises(WorldStateError, match="still owns committed"):
        world.install_plan(replacement)

    assert robot.plan_version == 1
    assert world.plans["R1"] is first


def test_idle_robot_cannot_retain_a_stale_plan() -> None:
    task = Task(
        "T1",
        Cell(0, 0),
        Cell(0, 1),
        release_tick=0,
        status=TaskStatus.COMPLETED,
        assigned_robot_id="R1",
    )
    plan = compile_path(
        (Cell(0, 0), Cell(0, 1)),
        robot_id="R1",
        plan_version=1,
        task_id="T1",
    )

    with pytest.raises(WorldStateError, match="idle robot"):
        WorldState(
            reservations=CommittedReservationLedger(horizon=2),
            robots={"R1": Robot("R1", Cell(0, 1), plan_version=1)},
            tasks={"T1": task},
            plans={"R1": plan},
        )


def test_failed_world_assignment_does_not_partially_mutate_task() -> None:
    active = Task(
        "T0",
        Cell(0, 0),
        Cell(0, 1),
        release_tick=0,
        status=TaskStatus.ASSIGNED,
        assigned_robot_id="R1",
    )
    pending = Task("T1", Cell(0, 1), Cell(0, 2), release_tick=0)
    robot = Robot("R1", Cell(0, 0), active_task_id="T0")
    world = _world(robots=(robot,), tasks=(active, pending))

    with pytest.raises(WorldStateError, match="not idle"):
        world.assign_task("T1", "R1")

    assert pending.status is TaskStatus.PENDING
    assert pending.assigned_robot_id is None
    assert robot.active_task_id == "T0"


def test_pickup_requires_quiescent_completed_phase_plan() -> None:
    pickup = Cell(0, 0)
    robot = Robot("R1", pickup, active_task_id="T1")
    task = Task(
        "T1",
        pickup,
        Cell(0, 1),
        release_tick=0,
        status=TaskStatus.ASSIGNED,
        assigned_robot_id="R1",
    )
    world = _world(robots=(robot,), tasks=(task,))
    plan = compile_path(
        (pickup, pickup),
        robot_id="R1",
        plan_version=1,
        task_id="T1",
    )
    world.install_plan(plan)
    task.transition_to(TaskStatus.TO_PICKUP)
    world.reservations.acquire_initial_batch((plan,), occupied=world.occupied_cells())
    plan.actions[0].transition_to(ActionStatus.RUNNING)
    robot.active_action_ref = plan.actions[0].ref
    robot.remaining_ticks = 1

    with pytest.raises(DomainError, match="active action"):
        complete_pickup(world, "T1")

    assert task.status is TaskStatus.TO_PICKUP
    assert robot.payload_task_id is None
