from __future__ import annotations

from collections.abc import Callable

from mapf_splice.domain import (
    ActionStatus,
    Cell,
    DomainError,
    Plan,
    Robot,
    Task,
    TaskStatus,
)
from mapf_splice.planning import compile_path
from mapf_splice.routing import NoPath, RoutePath, find_path
from mapf_splice.world import WorldState, WorldStateError


def _active_robot(world: WorldState, task: Task) -> Robot:
    robot_id = task.assigned_robot_id
    if robot_id is None or robot_id not in world.robots:
        raise WorldStateError("task has no active robot")
    robot = world.robots[robot_id]
    if robot.active_task_id != task.id:
        raise WorldStateError("task is not active on its assigned robot")
    return robot


def _require_phase_quiescent(
    world: WorldState,
    task: Task,
    robot: Robot,
    *,
    phase_label: str,
) -> None:
    if robot.active_action_ref is not None:
        raise DomainError(f"{phase_label} cannot complete during an active action")
    if world.reservations.committed_actions(robot.id, robot.plan_version):
        raise DomainError(f"{phase_label} cannot complete with committed actions")
    plan = world.plans.get(robot.id)
    if plan is None or plan.task_id != task.id:
        raise DomainError(f"{phase_label} has no current task plan")
    if any(action.status is not ActionStatus.COMPLETED for action in plan.actions):
        raise DomainError(f"{phase_label} cannot complete before its plan")


def _plan_leg(
    world: WorldState,
    task: Task,
    goal: Cell,
    *,
    is_traversable: Callable[[Cell], bool],
    base_duration_ticks: int,
) -> Plan | NoPath:
    robot = _active_robot(world, task)
    route = find_path(robot.position, goal, is_traversable=is_traversable)
    if isinstance(route, NoPath):
        return route
    assert isinstance(route, RoutePath)
    return compile_path(
        route.cells,
        robot_id=robot.id,
        plan_version=robot.plan_version + 1,
        task_id=task.id,
        base_duration_ticks=base_duration_ticks,
    )


def start_pickup_leg(
    world: WorldState,
    task_id: str,
    *,
    is_traversable: Callable[[Cell], bool],
    base_duration_ticks: int = 1,
) -> Plan | NoPath:
    task = world.tasks[task_id]
    if task.status is not TaskStatus.ASSIGNED:
        raise DomainError("pickup leg can start only from assigned status")
    plan = _plan_leg(
        world,
        task,
        task.pickup,
        is_traversable=is_traversable,
        base_duration_ticks=base_duration_ticks,
    )
    if isinstance(plan, NoPath):
        return plan
    world.install_plan(plan)
    task.transition_to(TaskStatus.TO_PICKUP)
    world.validate()
    return plan


def complete_pickup(world: WorldState, task_id: str) -> None:
    task = world.tasks[task_id]
    robot = _active_robot(world, task)
    if task.status is not TaskStatus.TO_PICKUP:
        raise DomainError("pickup can complete only from to-pickup status")
    if robot.position != task.pickup:
        raise DomainError("robot has not reached the pickup cell")
    if robot.payload_task_id is not None:
        raise DomainError("robot already carries a payload")
    _require_phase_quiescent(world, task, robot, phase_label="pickup")
    task.transition_to(TaskStatus.CARRYING)
    robot.payload_task_id = task.id
    world.validate()


def start_dropoff_leg(
    world: WorldState,
    task_id: str,
    *,
    is_traversable: Callable[[Cell], bool],
    base_duration_ticks: int = 1,
) -> Plan | NoPath:
    task = world.tasks[task_id]
    if task.status is not TaskStatus.CARRYING:
        raise DomainError("drop-off leg can start only from carrying status")
    plan = _plan_leg(
        world,
        task,
        task.dropoff,
        is_traversable=is_traversable,
        base_duration_ticks=base_duration_ticks,
    )
    if isinstance(plan, NoPath):
        return plan
    world.install_plan(plan)
    task.transition_to(TaskStatus.TO_DROPOFF)
    world.validate()
    return plan


def complete_dropoff(world: WorldState, task_id: str) -> None:
    task = world.tasks[task_id]
    robot = _active_robot(world, task)
    if task.status is not TaskStatus.TO_DROPOFF:
        raise DomainError("drop-off can complete only from to-drop-off status")
    if robot.position != task.dropoff:
        raise DomainError("robot has not reached the drop-off cell")
    if robot.payload_task_id != task.id:
        raise DomainError("robot does not carry this task payload")
    _require_phase_quiescent(world, task, robot, phase_label="drop-off")
    task.transition_to(TaskStatus.COMPLETED)
    robot.payload_task_id = None
    robot.active_task_id = None
    world.plans.pop(robot.id, None)
    world.validate()
