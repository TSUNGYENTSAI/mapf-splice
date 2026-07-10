from __future__ import annotations

from collections.abc import Sequence

from mapf_splice.domain import (
    Action,
    ActionKind,
    ActionRef,
    ActionStatus,
    Cell,
    DomainError,
    Plan,
)


def compile_path(
    cells: Sequence[Cell],
    *,
    robot_id: str,
    plan_version: int,
    task_id: str,
    base_duration_ticks: int = 1,
) -> Plan:
    """Compile one spatial path into an ordered, versioned robot plan."""
    if not cells:
        raise DomainError("cannot compile an empty path")
    if base_duration_ticks < 1:
        raise DomainError("base duration must be at least one tick")

    actions: list[Action] = []
    for index, (start, end) in enumerate(zip(cells, cells[1:], strict=False)):
        distance = start.manhattan_distance(end)
        if distance not in {0, 1}:
            raise DomainError("path transitions must move one edge or wait")
        ref = ActionRef(robot_id, plan_version, index)
        dependencies = ()
        if index > 0:
            dependencies = (ActionRef(robot_id, plan_version, index - 1),)
        actions.append(
            Action(
                ref=ref,
                kind=ActionKind.WAIT if distance == 0 else ActionKind.MOVE,
                start=start,
                end=end,
                duration_ticks=base_duration_ticks,
                dependencies=dependencies,
            )
        )

    return Plan(
        robot_id=robot_id,
        version=plan_version,
        task_id=task_id,
        phase_goal=cells[-1],
        actions=tuple(actions),
    )


def completed_prefix_length(plan: Plan) -> int:
    """Count leading COMPLETED actions; the completed set must be a prefix."""
    prefix = 0
    while (
        prefix < len(plan.actions)
        and plan.actions[prefix].status is ActionStatus.COMPLETED
    ):
        prefix += 1
    if any(
        action.status is ActionStatus.COMPLETED for action in plan.actions[prefix:]
    ):
        raise DomainError("completed actions must form a sequential prefix")
    return prefix


def next_required_action(plan: Plan) -> Action | None:
    """The first action the robot has not COMPLETED, or None if the plan is done."""
    index = completed_prefix_length(plan)
    return plan.actions[index] if index < len(plan.actions) else None
