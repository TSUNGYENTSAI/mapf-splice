"""Multi-Agent Path Finding (MAPF) utility functions.

Vendored subset of pypibt.mapf_utils. Only the grid/coordinate types and the
two grid helpers the PIBT algorithm depends on (``is_valid_coord`` and
``get_neighbors``) are retained; the upstream benchmark loaders
(``get_grid``/``get_scenario``), the visualizer export, and the solution
validators are intentionally omitted because MAPF Splice never uses them and
they are not part of the algorithm. The retained function bodies are verbatim.
See PROVENANCE.md.
"""
from typing import TypeAlias

import numpy as np

# y, x
Grid: TypeAlias = np.ndarray
Coord: TypeAlias = tuple[int, int]
Config: TypeAlias = list[Coord]
Configs: TypeAlias = list[Config]


def is_valid_coord(grid: Grid, coord: Coord) -> bool:
    """Check if a coordinate is valid and free on the grid.

    Args:
        grid: 2D boolean array representing the map.
        coord: Position (y, x) to check.

    Returns:
        True if coordinate is within bounds and not an obstacle, False otherwise.
    """
    y, x = coord
    if y < 0 or y >= grid.shape[0] or x < 0 or x >= grid.shape[1] or not grid[coord]:
        return False
    return True


def get_neighbors(grid: Grid, coord: Coord) -> list[Coord]:
    """Get valid neighboring coordinates (4-connected grid).

    Args:
        grid: 2D boolean array representing the map.
        coord: Center position (y, x).

    Returns:
        List of valid neighboring coordinates in 4 directions (left, right,
        up, down). Empty list if coord is invalid.
    """
    # coord: y, x
    neigh: list[Coord] = []

    # check valid input
    if not is_valid_coord(grid, coord):
        return neigh

    y, x = coord

    if x > 0 and grid[y, x - 1]:
        neigh.append((y, x - 1))

    if x < grid.shape[1] - 1 and grid[y, x + 1]:
        neigh.append((y, x + 1))

    if y > 0 and grid[y - 1, x]:
        neigh.append((y - 1, x))

    if y < grid.shape[0] - 1 and grid[y + 1, x]:
        neigh.append((y + 1, x))

    return neigh
