import json
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest

from mapf_splice.dispatch import Assignment, dispatch_pending_tasks
from mapf_splice.scenario import (
    ScenarioError,
    build_initial_world,
    load_review,
    load_scenario,
)

ROOT = Path(__file__).parents[1]
SCENARIO_PATH = ROOT / "scenarios/compact-three-robot/scenario.json"
REVIEW_PATH = ROOT / "scenarios/compact-three-robot/review.json"
SCENARIO_SCHEMA = ROOT / "schemas/scenario.v0.1.schema.json"
REVIEW_SCHEMA = ROOT / "schemas/render-review.v0.1.schema.json"


def _validate_json(instance_path: Path, schema_path: Path) -> None:
    instance = json.loads(instance_path.read_text())
    schema = json.loads(schema_path.read_text())
    jsonschema.Draft202012Validator(schema).validate(instance)


def _use_schema(data: dict, schema_path: Path) -> None:
    data["$schema"] = str(schema_path.resolve())


def test_scenario_and_review_match_json_schemas() -> None:
    _validate_json(SCENARIO_PATH, SCENARIO_SCHEMA)
    _validate_json(REVIEW_PATH, REVIEW_SCHEMA)


def test_lifelong_scenario_cross_file_invariants() -> None:
    scenario = load_scenario(SCENARIO_PATH)

    assert scenario.warehouse_map.height == 15
    assert scenario.warehouse_map.width == 21
    assert scenario.data["task_stream"]["mode"] == "lifelong"
    assert len(scenario.data["fleet"]["robots"]) == 3
    assert len(scenario.data["task_stream"]["initial_tasks"]) == 3
    assert scenario.data["traffic"]["committed_horizon"] == {
        "default": 3,
        "minimum": 3,
        "maximum": 5,
    }


def test_scenario_builds_authoritative_initial_world() -> None:
    scenario = load_scenario(SCENARIO_PATH)
    world = build_initial_world(scenario, committed_horizon=4)

    assert world.tick == 0
    assert world.reservations.horizon == 4
    assert world.occupied_cells() == {
        scenario.stations["P1"]: "R1",
        scenario.stations["P2"]: "R2",
        scenario.stations["P3"]: "R3",
    }
    assert set(world.tasks) == {"T1", "T2", "T3"}

    assert dispatch_pending_tasks(
        world,
        is_traversable=scenario.warehouse_map.is_traversable,
    ) == (
        Assignment("T1", "R1", 0),
        Assignment("T2", "R2", 0),
        Assignment("T3", "R3", 0),
    )


def test_review_views_derive_three_robot_prospective_sccs() -> None:
    scenario = load_scenario(SCENARIO_PATH)
    review = load_review(REVIEW_PATH, scenario)

    assert [view["committed_horizon"] for view in review["views"]] == [3, 4, 5]
    for view in review["views"]:
        edges = {
            (edge["waiting_robot_id"], edge["blocking_robot_id"])
            for edge in view["prospective_dependencies"]
        }
        assert {("R1", "R2"), ("R2", "R3"), ("R3", "R1")} <= edges


def test_review_rejects_duplicate_robot_routes(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIO_PATH)
    review = json.loads(REVIEW_PATH.read_text())
    _use_schema(review, REVIEW_SCHEMA)
    duplicate = deepcopy(review["routes"][0])
    duplicate["cells"][0] = {"row": 1, "col": 4}
    review["routes"].append(duplicate)
    path = tmp_path / "duplicate-route.json"
    path.write_text(json.dumps(review))

    with pytest.raises(ScenarioError, match="duplicate review route"):
        load_review(path, scenario)


def test_review_route_must_equal_runtime_astar_route(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIO_PATH)
    review = json.loads(REVIEW_PATH.read_text())
    _use_schema(review, REVIEW_SCHEMA)
    alternative = (
        (14, 10),
        (13, 10),
        (12, 10),
        (11, 10),
        (10, 10),
        (10, 9),
        (10, 8),
        (10, 7),
        (9, 7),
        (8, 7),
        (7, 7),
        (7, 6),
        (7, 5),
        (7, 4),
        (7, 3),
        (7, 2),
    )
    review["routes"][2]["cells"] = [
        {"row": row, "col": col} for row, col in alternative
    ]
    path = tmp_path / "alternative-shortest-route.json"
    path.write_text(json.dumps(review))

    with pytest.raises(ScenarioError, match=r"does not match deterministic A\*"):
        load_review(path, scenario)


def test_bootstrap_tasks_cannot_share_a_pickup_station(tmp_path: Path) -> None:
    data = json.loads(SCENARIO_PATH.read_text())
    _use_schema(data, SCENARIO_SCHEMA)
    data["map"]["path"] = str(SCENARIO_PATH.parent / "map.txt")
    duplicate_pickup = deepcopy(data["task_stream"]["initial_tasks"][0])
    duplicate_pickup["id"] = "T4"
    data["task_stream"]["initial_tasks"].append(duplicate_pickup)
    path = tmp_path / "duplicate-pickup.json"
    path.write_text(json.dumps(data))

    with pytest.raises(ScenarioError, match="cannot share a pickup"):
        load_scenario(path)


def test_runtime_loader_rejects_unknown_scenario_property(tmp_path: Path) -> None:
    data = json.loads(SCENARIO_PATH.read_text())
    _use_schema(data, SCENARIO_SCHEMA)
    data["unexpected"] = 123
    path = tmp_path / "unexpected-property.json"
    path.write_text(json.dumps(data))

    with pytest.raises(ScenarioError, match="schema validation failed"):
        load_scenario(path)


def test_schema_rejects_unsupported_carrying_bootstrap(tmp_path: Path) -> None:
    data = json.loads(SCENARIO_PATH.read_text())
    _use_schema(data, SCENARIO_SCHEMA)
    data["fleet"]["robots"][0]["initial_payload"] = "carrying"
    path = tmp_path / "carrying-bootstrap.json"
    path.write_text(json.dumps(data))

    with pytest.raises(ScenarioError, match="schema validation failed"):
        load_scenario(path)


def test_release_interval_must_be_positive(tmp_path: Path) -> None:
    data = json.loads(SCENARIO_PATH.read_text())
    _use_schema(data, SCENARIO_SCHEMA)
    data["task_stream"]["generator"]["release_interval_ticks"]["minimum"] = 0
    path = tmp_path / "zero-release-interval.json"
    path.write_text(json.dumps(data))

    with pytest.raises(ScenarioError, match="schema validation failed"):
        load_scenario(path)
