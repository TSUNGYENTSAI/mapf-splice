from pathlib import Path

from mapf_splice.domain import Cell
from mapf_splice.routing import NoPath, RoutePath, find_path
from mapf_splice.scenario import load_review, load_scenario

ROOT = Path(__file__).parents[1]
SCENARIO_PATH = ROOT / "scenarios/compact-three-robot/scenario.json"
REVIEW_PATH = ROOT / "scenarios/compact-three-robot/review.json"


def test_astar_reproduces_the_three_hero_routes() -> None:
    scenario = load_scenario(SCENARIO_PATH)
    review = load_review(REVIEW_PATH, scenario)

    for expected_cells in review["_routes"].values():
        result = find_path(
            expected_cells[0],
            expected_cells[-1],
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
