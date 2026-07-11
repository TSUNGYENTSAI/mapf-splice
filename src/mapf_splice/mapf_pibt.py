"""Project-owned PyPIBT adapter.

This is the ONLY module allowed to touch PyPIBT and NumPy. It translates a
solver-neutral ``ScopedMapfProblem`` into the vendored solver's inputs, runs
PIBT with a fixed seed and bounded max timestep, independently verifies that
every requested goal is reached, and returns either a ``ScopedMapfSolution``
(project types only) or a typed ``RecoveryPlanningFailure``. It is read-only
with respect to ``WorldState`` (it never receives one).
"""
from __future__ import annotations

from mapf_splice.domain import Cell
from mapf_splice.recovery import (
    RecoveryFailureReason,
    RecoveryPlanningFailure,
    ScopedMapfProblem,
    ScopedMapfSolution,
)


def _import_pibt():
    """Lazily import NumPy and the vendored PIBT (kept out of module import)."""
    import numpy as np

    from mapf_splice._vendor.pypibt import PIBT

    return np, PIBT


def _to_yx(cell: Cell) -> tuple[int, int]:
    return (cell.row, cell.col)


def _to_cell(coord) -> Cell:
    return Cell(int(coord[0]), int(coord[1]))


def solve(problem: ScopedMapfProblem) -> ScopedMapfSolution | RecoveryPlanningFailure:
    try:
        np, PIBT = _import_pibt()
    except ImportError as error:
        return RecoveryPlanningFailure(
            RecoveryFailureReason.SOLVER_UNAVAILABLE,
            f"recovery solver stack unavailable: {error}",
        )

    warehouse = problem.warehouse_map
    robot_ids = tuple(sorted(problem.robot_ids))

    for robot_id in robot_ids:
        for label, cell in (
            ("start", problem.starts[robot_id]),
            ("goal", problem.goals[robot_id]),
        ):
            if not warehouse.is_traversable(cell):
                return RecoveryPlanningFailure(
                    RecoveryFailureReason.UNSUPPORTED_SCOPE,
                    f"{robot_id} {label} {cell} is not a traversable cell",
                )

    grid = np.zeros((warehouse.height, warehouse.width), dtype=bool)
    for y in range(warehouse.height):
        for x in range(warehouse.width):
            grid[y, x] = warehouse.is_traversable(Cell(y, x))

    starts = [_to_yx(problem.starts[robot_id]) for robot_id in robot_ids]
    goals = [_to_yx(problem.goals[robot_id]) for robot_id in robot_ids]

    configs = PIBT(grid, starts, goals, seed=problem.seed).run(
        max_timestep=problem.max_timestep
    )
    final = configs[-1]
    if any(final[index] != goals[index] for index in range(len(goals))):
        return RecoveryPlanningFailure(
            RecoveryFailureReason.SOLVER_NO_SOLUTION,
            f"PIBT did not reach all goals within {problem.max_timestep} timesteps",
        )

    horizon = len(configs)
    paths = {
        robot_id: tuple(_to_cell(configs[t][index]) for t in range(horizon))
        for index, robot_id in enumerate(robot_ids)
    }
    return ScopedMapfSolution(
        robot_ids=robot_ids,
        paths=paths,
        makespan=horizon - 1,
    )
