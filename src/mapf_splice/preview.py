from __future__ import annotations

from dataclasses import dataclass

from mapf_splice.domain import (
    Action,
    ActionRef,
    ActionStatus,
    Plan,
    Resource,
    VertexResource,
)
from mapf_splice.world import WorldState


@dataclass(frozen=True, slots=True)
class ProspectiveDependency:
    waiting_robot_id: str
    waiting_plan_version: int
    preview_action_ref: ActionRef
    blocking_robot_id: str
    blocking_plan_version: int
    resource: Resource
    blocking_action_refs: tuple[ActionRef, ...]
    occupied_blocker: bool


@dataclass(frozen=True, slots=True)
class PreviewContention:
    resource: Resource
    robot_ids: tuple[str, ...]
    action_refs: tuple[ActionRef, ...]


@dataclass(frozen=True, slots=True)
class PreviewAnalysis:
    dependencies: tuple[ProspectiveDependency, ...]
    contentions: tuple[PreviewContention, ...]


def _resource_key(resource: Resource) -> tuple:
    if isinstance(resource, VertexResource):
        return ("vertex", resource.cell.row, resource.cell.col)
    return (
        "edge",
        resource.first.row,
        resource.first.col,
        resource.second.row,
        resource.second.col,
    )


def resource_label(resource: Resource) -> str:
    key = _resource_key(resource)
    return ":".join(str(part) for part in key)


def preview_actions(world: WorldState, plan: Plan) -> tuple[Action, ...]:
    if not world.reservations.plan_initialized(plan):
        return ()
    completed = 0
    while (
        completed < len(plan.actions)
        and plan.actions[completed].status is ActionStatus.COMPLETED
    ):
        completed += 1
    committed = world.reservations.committed_actions(plan.robot_id, plan.version)
    start = completed
    if committed:
        start = committed[-1].action_index + 1
    return plan.actions[start : start + world.reservations.horizon]


def analyze_preview(world: WorldState) -> PreviewAnalysis:
    preview_by_robot = {
        robot_id: preview_actions(world, plan)
        for robot_id, plan in sorted(world.plans.items())
    }
    occupied = world.occupied_cells()
    evidence: dict[tuple, tuple[set[ActionRef], bool]] = {}
    preview_claims: dict[Resource, list[tuple[str, ActionRef]]] = {}

    for waiting_id, actions in preview_by_robot.items():
        plan = world.plans[waiting_id]
        for action in actions:
            for resource in action.claims:
                preview_claims.setdefault(resource, []).append((waiting_id, action.ref))
                blockers: dict[tuple[str, int], set[ActionRef]] = {}
                for owner in world.reservations.owners(resource):
                    if owner.robot_id == waiting_id:
                        continue
                    blockers.setdefault(
                        (owner.robot_id, owner.plan_version),
                        set(),
                    ).add(owner)
                occupied_id = None
                if isinstance(resource, VertexResource):
                    occupied_id = occupied.get(resource.cell)
                    if occupied_id == waiting_id:
                        occupied_id = None
                    if occupied_id is not None:
                        blocker = world.robots[occupied_id]
                        blockers.setdefault((occupied_id, blocker.plan_version), set())
                for (blocking_id, blocking_version), refs in blockers.items():
                    key = (
                        waiting_id,
                        plan.version,
                        action.ref,
                        blocking_id,
                        blocking_version,
                        resource,
                    )
                    evidence[key] = (refs, occupied_id == blocking_id)

    dependencies = [
        ProspectiveDependency(
            waiting_robot_id=key[0],
            waiting_plan_version=key[1],
            preview_action_ref=key[2],
            blocking_robot_id=key[3],
            blocking_plan_version=key[4],
            resource=key[5],
            blocking_action_refs=tuple(sorted(value[0])),
            occupied_blocker=value[1],
        )
        for key, value in evidence.items()
    ]
    dependencies.sort(
        key=lambda item: (
            item.waiting_robot_id,
            item.preview_action_ref,
            item.blocking_robot_id,
            _resource_key(item.resource),
        )
    )

    contentions: list[PreviewContention] = []
    for resource, claims in preview_claims.items():
        robot_ids = tuple(sorted({robot_id for robot_id, _ in claims}))
        if len(robot_ids) > 1:
            contentions.append(
                PreviewContention(
                    resource=resource,
                    robot_ids=robot_ids,
                    action_refs=tuple(sorted(ref for _, ref in claims)),
                )
            )
    contentions.sort(key=lambda item: _resource_key(item.resource))
    return PreviewAnalysis(tuple(dependencies), tuple(contentions))
