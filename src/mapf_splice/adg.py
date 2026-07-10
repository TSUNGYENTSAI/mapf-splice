from __future__ import annotations

from collections.abc import Mapping, Sequence

from mapf_splice.domain import Action, ActionKind, ActionRef, Cell, Plan


class MapfSolutionError(ValueError):
    """Raised when a MAPF solution cannot be safely compiled for execution."""


def _validate_solution(paths: Mapping[str, Sequence[Cell]]) -> int:
    if not paths:
        raise MapfSolutionError("MAPF solution cannot be empty")
    lengths = {len(path) for path in paths.values()}
    if 0 in lengths:
        raise MapfSolutionError("MAPF paths cannot be empty")
    if len(lengths) != 1:
        raise MapfSolutionError("MAPF paths must have equal synchronized lengths")
    configuration_count = lengths.pop()

    for robot_id, path in paths.items():
        goal = path[-1]
        first_goal_index = path.index(goal)
        if any(cell != goal for cell in path[first_goal_index:]):
            raise MapfSolutionError(
                f"path for {robot_id} does not use stay-at-goal semantics"
            )
        for start, end in zip(path, path[1:], strict=False):
            if start.manhattan_distance(end) not in {0, 1}:
                raise MapfSolutionError(
                    f"path for {robot_id} contains a non-adjacent transition"
                )

    robot_ids = sorted(paths)
    for time_index in range(configuration_count):
        positions = [paths[robot_id][time_index] for robot_id in robot_ids]
        if len(set(positions)) != len(positions):
            raise MapfSolutionError(
                f"vertex collision in MAPF configuration {time_index}"
            )

    for time_index in range(configuration_count - 1):
        for offset, robot_id in enumerate(robot_ids):
            for other_id in robot_ids[offset + 1 :]:
                if (
                    paths[robot_id][time_index]
                    == paths[other_id][time_index + 1]
                    and paths[robot_id][time_index + 1]
                    == paths[other_id][time_index]
                ):
                    raise MapfSolutionError(
                        f"opposite-edge swap at MAPF transition {time_index}"
                    )
    return configuration_count


def _last_action_count(path: Sequence[Cell]) -> int:
    for index in range(len(path) - 2, -1, -1):
        if path[index] != path[index + 1]:
            return index + 1
    return 0


def _assert_acyclic(actions: Mapping[ActionRef, Action]) -> None:
    visiting: set[ActionRef] = set()
    visited: set[ActionRef] = set()

    def visit(ref: ActionRef) -> None:
        if ref in visiting:
            raise MapfSolutionError("compiled action dependency graph contains a cycle")
        if ref in visited:
            return
        action = actions.get(ref)
        if action is None:
            raise MapfSolutionError(f"dependency refers to missing action {ref}")
        visiting.add(ref)
        for dependency in action.dependencies:
            visit(dependency)
        visiting.remove(ref)
        visited.add(ref)

    for action_ref in sorted(actions):
        visit(action_ref)


def compile_adg(
    paths: Mapping[str, Sequence[Cell]],
    *,
    plan_versions: Mapping[str, int],
    task_ids: Mapping[str, str],
    base_duration_ticks: int = 1,
) -> dict[str, Plan]:
    """Validate synchronized MAPF paths and compile executable ADG plans."""
    if base_duration_ticks < 1:
        raise MapfSolutionError("base duration must be at least one tick")
    robot_ids = set(paths)
    if set(plan_versions) != robot_ids or set(task_ids) != robot_ids:
        raise MapfSolutionError(
            "paths, plan versions, and task ids must cover the same robots"
        )
    configuration_count = _validate_solution(paths)
    action_counts = {
        robot_id: _last_action_count(path) for robot_id, path in paths.items()
    }
    dependency_sets: dict[ActionRef, set[ActionRef]] = {}

    for robot_id in sorted(paths):
        version = plan_versions[robot_id]
        for action_index in range(action_counts[robot_id]):
            ref = ActionRef(robot_id, version, action_index)
            dependencies: set[ActionRef] = set()
            if action_index > 0:
                dependencies.add(ActionRef(robot_id, version, action_index - 1))
            dependency_sets[ref] = dependencies

    # A move into a vertex follows the most recent visit to that vertex. The
    # later entry must wait for the earlier occupant's departure to complete.
    for robot_id in sorted(paths):
        path = paths[robot_id]
        for action_index in range(action_counts[robot_id]):
            if path[action_index] == path[action_index + 1]:
                continue
            target = path[action_index + 1]
            previous_owner: tuple[str, int] | None = None
            for time_index in range(action_index, -1, -1):
                owner = next(
                    (
                        other_id
                        for other_id in sorted(paths)
                        if paths[other_id][time_index] == target
                    ),
                    None,
                )
                if owner is not None:
                    previous_owner = (owner, time_index)
                    break
            if previous_owner is None or previous_owner[0] == robot_id:
                continue
            owner_id, departure_index = previous_owner
            if departure_index >= action_counts[owner_id]:
                raise MapfSolutionError(
                    f"robot {robot_id} enters a vertex never vacated by {owner_id}"
                )
            ref = ActionRef(robot_id, plan_versions[robot_id], action_index)
            dependency_sets[ref].add(
                ActionRef(owner_id, plan_versions[owner_id], departure_index)
            )

    plans: dict[str, Plan] = {}
    all_actions: dict[ActionRef, Action] = {}
    for robot_id in sorted(paths):
        path = paths[robot_id]
        actions: list[Action] = []
        for action_index in range(action_counts[robot_id]):
            ref = ActionRef(robot_id, plan_versions[robot_id], action_index)
            start = path[action_index]
            end = path[action_index + 1]
            action = Action(
                ref=ref,
                kind=ActionKind.WAIT if start == end else ActionKind.MOVE,
                start=start,
                end=end,
                duration_ticks=base_duration_ticks,
                dependencies=tuple(sorted(dependency_sets[ref])),
            )
            actions.append(action)
            all_actions[ref] = action
        plans[robot_id] = Plan(
            robot_id=robot_id,
            version=plan_versions[robot_id],
            task_id=task_ids[robot_id],
            phase_goal=path[configuration_count - 1],
            actions=tuple(actions),
        )

    _assert_acyclic(all_actions)
    return plans
