from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mapf_splice.domain import Cell, TaskStatus
from mapf_splice.routing import RoutePath, find_path
from mapf_splice.world import WorldState


@dataclass(frozen=True, slots=True)
class Assignment:
    task_id: str
    robot_id: str
    pickup_distance: int


def dispatch_pending_tasks(
    world: WorldState,
    *,
    is_traversable: Callable[[Cell], bool],
) -> tuple[Assignment, ...]:
    """Assign released tasks by shortest pickup distance, then robot id."""
    idle_robot_ids = {
        robot.id for robot in world.robots.values() if robot.active_task_id is None
    }
    pending_tasks = sorted(
        (
            task
            for task in world.tasks.values()
            if task.status is TaskStatus.PENDING and task.release_tick <= world.tick
        ),
        key=lambda task: (task.release_tick, task.id),
    )
    assignments: list[Assignment] = []

    for task in pending_tasks:
        candidates: list[tuple[int, str]] = []
        for robot_id in sorted(idle_robot_ids):
            robot = world.robots[robot_id]
            route = find_path(
                robot.position,
                task.pickup,
                is_traversable=is_traversable,
            )
            if isinstance(route, RoutePath):
                candidates.append((len(route.cells) - 1, robot_id))
        if not candidates:
            continue
        pickup_distance, robot_id = min(candidates)
        world.assign_task(task.id, robot_id)
        idle_robot_ids.remove(robot_id)
        assignments.append(Assignment(task.id, robot_id, pickup_distance))

    world.validate()
    return tuple(assignments)
