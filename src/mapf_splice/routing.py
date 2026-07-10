from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass
from heapq import heappop, heappush

from mapf_splice.domain import Cell


@dataclass(frozen=True, slots=True)
class RoutePath:
    cells: tuple[Cell, ...]


@dataclass(frozen=True, slots=True)
class NoPath:
    start: Cell
    goal: Cell


RouteResult = RoutePath | NoPath

# The order is part of seeded reproducibility and reproduces the hero routes.
NEIGHBOR_OFFSETS = ((1, 0), (0, -1), (0, 1), (-1, 0))


def find_path(
    start: Cell,
    goal: Cell,
    *,
    is_traversable: Callable[[Cell], bool],
) -> RouteResult:
    """Find a deterministic shortest spatial path over static traversability."""
    if not is_traversable(start) or not is_traversable(goal):
        return NoPath(start=start, goal=goal)
    if start == goal:
        return RoutePath(cells=(start,))

    serial = itertools.count()
    frontier: list[tuple[int, int, int, Cell]] = []
    heappush(frontier, (start.manhattan_distance(goal), 0, next(serial), start))
    distance = {start: 0}
    previous: dict[Cell, Cell] = {}

    while frontier:
        _, current_distance, _, current = heappop(frontier)
        if current_distance != distance[current]:
            continue
        if current == goal:
            cells = [goal]
            while cells[-1] != start:
                cells.append(previous[cells[-1]])
            cells.reverse()
            return RoutePath(cells=tuple(cells))

        for row_offset, col_offset in NEIGHBOR_OFFSETS:
            neighbor = Cell(
                current.row + row_offset,
                current.col + col_offset,
            )
            if not is_traversable(neighbor):
                continue
            tentative_distance = current_distance + 1
            if tentative_distance >= distance.get(neighbor, 2**63 - 1):
                continue
            distance[neighbor] = tentative_distance
            previous[neighbor] = current
            priority = tentative_distance + neighbor.manhattan_distance(goal)
            heappush(
                frontier,
                (priority, tentative_distance, next(serial), neighbor),
            )

    return NoPath(start=start, goal=goal)
