from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from mapf_splice.domain import ActionRef, ActionStatus, Resource
from mapf_splice.planning import next_required_action
from mapf_splice.preview import resource_label
from mapf_splice.world import WorldState


class ConfirmationError(ValueError):
    """Raised when a confirmed wait-for graph cannot be built from valid state."""


@dataclass(frozen=True, slots=True)
class ConfirmedWaitForEdge:
    waiting_robot_id: str
    waiting_plan_version: int
    waiting_action_ref: ActionRef
    resource: Resource
    blocking_robot_id: str
    blocking_plan_version: int
    committed_blocker_refs: tuple[ActionRef, ...]
    occupied_blocker: bool
    blocking_in_scope: bool


@dataclass(frozen=True, slots=True)
class ConfirmedWaitForGraph:
    scope: tuple[tuple[str, int], ...]
    epoch: int
    captured_at_tick: int
    edges: tuple[ConfirmedWaitForEdge, ...]
    cyclic_sccs: tuple[tuple[str, ...], ...]


def build_confirmed_wait_for(
    world: WorldState,
    scope: tuple[tuple[str, int], ...],
    *,
    epoch: int,
    tick: int,
) -> ConfirmedWaitForGraph:
    """Build the authoritative wait-for graph for a quiescent containment scope."""
    scope_members = set(scope)
    occupied = world.occupied_cells()
    accumulator: dict[tuple[ActionRef, str, int, Resource], dict[str, object]] = {}

    for robot_id, _version in scope:
        plan = world.plans[robot_id]
        action = next_required_action(plan)
        if action is None:
            continue
        if action.status is not ActionStatus.PLANNED:
            raise ConfirmationError(
                "quiescent plan next required action must be planned"
            )
        for conflict in world.reservations.conflicts_for(action, occupied=occupied):
            for ref in conflict.reserved_by:
                key = (action.ref, ref.robot_id, ref.plan_version, conflict.resource)
                entry = accumulator.setdefault(
                    key, {"committed": set(), "occupied": False}
                )
                entry["committed"].add(ref)
            if conflict.occupied_by is not None:
                blocker = world.robots[conflict.occupied_by]
                key = (
                    action.ref,
                    conflict.occupied_by,
                    blocker.plan_version,
                    conflict.resource,
                )
                entry = accumulator.setdefault(
                    key, {"committed": set(), "occupied": False}
                )
                entry["occupied"] = True

    edges = tuple(
        sorted(
            (
                ConfirmedWaitForEdge(
                    waiting_robot_id=action_ref.robot_id,
                    waiting_plan_version=action_ref.plan_version,
                    waiting_action_ref=action_ref,
                    resource=resource,
                    blocking_robot_id=blocking_id,
                    blocking_plan_version=blocking_version,
                    committed_blocker_refs=tuple(sorted(entry["committed"])),
                    occupied_blocker=bool(entry["occupied"]),
                    blocking_in_scope=(blocking_id, blocking_version) in scope_members,
                )
                for (action_ref, blocking_id, blocking_version, resource), entry
                in accumulator.items()
            ),
            key=lambda edge: (
                edge.waiting_robot_id,
                edge.waiting_action_ref,
                edge.blocking_robot_id,
                resource_label(edge.resource),
            ),
        )
    )
    cyclic = cyclic_components(
        (edge.waiting_robot_id, edge.blocking_robot_id) for edge in edges
    )
    return ConfirmedWaitForGraph(
        scope=scope,
        epoch=epoch,
        captured_at_tick=tick,
        edges=edges,
        cyclic_sccs=cyclic,
    )


def cyclic_components(
    edges: Iterable[tuple[str, str]],
) -> tuple[tuple[str, ...], ...]:
    """Tarjan SCCs of size >= 2 over waiting -> blocking edges, sorted."""
    graph: dict[str, set[str]] = {}
    for waiting, blocking in edges:
        graph.setdefault(waiting, set()).add(blocking)
        graph.setdefault(blocking, set())

    index = 0
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def connect(node: str) -> None:
        nonlocal index
        indexes[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in sorted(graph[node]):
            if neighbor not in indexes:
                connect(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[neighbor])
        if lowlinks[node] == indexes[node]:
            members: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                members.append(member)
                if member == node:
                    break
            component = tuple(sorted(members))
            if len(component) >= 2:
                components.append(component)

    for node in sorted(graph):
        if node not in indexes:
            connect(node)
    return tuple(sorted(components))
