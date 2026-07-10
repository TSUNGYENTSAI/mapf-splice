from pathlib import Path

from mapf_splice.domain import Cell
from mapf_splice.routing import NoPath, RoutePath, find_path
from mapf_splice.scenario import load_scenario

ROOT = Path(__file__).parents[1]
SCENARIO_PATH = ROOT / "scenarios/compact-three-robot/scenario.json"

EXPECTED_HERO_ROUTES = {
    "R1": (
        tuple(Cell(row, 4) for row in range(11))
        + tuple(Cell(10, col) for col in range(5, 9))
        + tuple(Cell(11, col) for col in range(8, 17))
        + (Cell(12, 16),)
    ),
    "R2": (
        tuple(Cell(row, 16) for row in range(11))
        + tuple(Cell(10, col) for col in range(15, 5, -1))
        + (Cell(11, 6), Cell(11, 5), Cell(11, 4), Cell(12, 4))
    ),
    "R3": (
        (
            Cell(14, 10),
            Cell(13, 10),
            Cell(12, 10),
            Cell(11, 10),
            Cell(11, 9),
            Cell(11, 8),
        )
        + tuple(Cell(10, col) for col in range(8, 3, -1))
        + tuple(Cell(row, 4) for row in range(9, 6, -1))
        + (Cell(7, 3), Cell(7, 2))
    ),
}


def test_astar_reproduces_the_three_hero_routes() -> None:
    scenario = load_scenario(SCENARIO_PATH)
    tasks_by_pickup = {
        task["pickup_station_id"]: task
        for task in scenario.data["task_stream"]["initial_tasks"]
    }
    for robot in scenario.data["fleet"]["robots"]:
        expected_cells = EXPECTED_HERO_ROUTES[robot["id"]]
        task = tasks_by_pickup[robot["start_station_id"]]
        result = find_path(
            scenario.stations[robot["start_station_id"]],
            scenario.stations[task["delivery_station_id"]],
            is_traversable=scenario.warehouse_map.is_traversable,
        )
        assert isinstance(result, RoutePath)
        assert result.cells == expected_cells


def test_astar_returns_typed_no_path() -> None:
    traversable = {Cell(0, 0), Cell(0, 2)}

    result = find_path(
        Cell(0, 0),
        Cell(0, 2),
        is_traversable=traversable.__contains__,
    )

    assert result == NoPath(start=Cell(0, 0), goal=Cell(0, 2))
