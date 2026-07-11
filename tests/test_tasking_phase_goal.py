"""The canonical read-only current task-phase goal query."""
import pytest

from mapf_splice.domain import Cell, DomainError, Robot, Task, TaskStatus
from mapf_splice.planning import compile_path
from mapf_splice.tasking import current_phase_goal
from mapf_splice.traffic import CommittedReservationLedger
from mapf_splice.world import WorldState

PICKUP = Cell(0, 0)
DROPOFF = Cell(0, 3)
ROUTE = (Cell(0, 0), Cell(0, 1), Cell(0, 2), Cell(0, 3))


def _world(status: TaskStatus) -> WorldState:
    carrying = status in {TaskStatus.CARRYING, TaskStatus.TO_DROPOFF}
    version = 2 if carrying else 1
    goal = DROPOFF if carrying else PICKUP
    robot = Robot(
        "R1",
        PICKUP,
        active_task_id="T1",
        payload_task_id="T1" if carrying else None,
        plan_version=version,
    )
    task = Task("T1", PICKUP, DROPOFF, 0, status, "R1")
    plans = {}
    if status is not TaskStatus.ASSIGNED:
        cells = ROUTE if goal == DROPOFF else (PICKUP,)
        plans["R1"] = compile_path(
            cells, robot_id="R1", plan_version=version, task_id="T1"
        )
    return WorldState(
        reservations=CommittedReservationLedger(horizon=1),
        robots={"R1": robot},
        tasks={"T1": task},
        plans=plans,
    )


def test_to_pickup_phase_goal_is_pickup_cell() -> None:
    assert current_phase_goal(_world(TaskStatus.TO_PICKUP), "R1") == PICKUP


def test_carrying_phase_goal_is_dropoff_cell() -> None:
    assert current_phase_goal(_world(TaskStatus.CARRYING), "R1") == DROPOFF


def test_to_dropoff_phase_goal_is_dropoff_cell() -> None:
    assert current_phase_goal(_world(TaskStatus.TO_DROPOFF), "R1") == DROPOFF


def test_assigned_phase_has_no_recovery_goal() -> None:
    with pytest.raises(DomainError):
        current_phase_goal(_world(TaskStatus.ASSIGNED), "R1")


def test_idle_robot_has_no_phase_goal() -> None:
    world = WorldState(
        reservations=CommittedReservationLedger(horizon=1),
        robots={"R1": Robot("R1", PICKUP)},
        tasks={},
    )
    with pytest.raises(DomainError):
        current_phase_goal(world, "R1")


def test_unknown_robot_is_rejected() -> None:
    with pytest.raises(DomainError):
        current_phase_goal(_world(TaskStatus.TO_DROPOFF), "RX")


def test_query_does_not_mutate_world() -> None:
    world = _world(TaskStatus.TO_DROPOFF)
    before = (
        world.robots["R1"].position,
        world.tasks["T1"].status,
        world.robots["R1"].active_task_id,
        world.robots["R1"].plan_version,
    )
    current_phase_goal(world, "R1")
    assert (
        world.robots["R1"].position,
        world.tasks["T1"].status,
        world.robots["R1"].active_task_id,
        world.robots["R1"].plan_version,
    ) == before
