import json
from pathlib import Path

import jsonschema

from mapf_splice.scenario import load_review, load_scenario

ROOT = Path(__file__).parents[1]
SCENARIO_PATH = ROOT / "scenarios/compact-three-robot/scenario.json"
REVIEW_PATH = ROOT / "scenarios/compact-three-robot/review.json"


def _validate_json(instance_path: Path, schema_path: Path) -> None:
    instance = json.loads(instance_path.read_text())
    schema = json.loads(schema_path.read_text())
    jsonschema.Draft202012Validator(schema).validate(instance)


def test_scenario_and_review_match_json_schemas() -> None:
    _validate_json(SCENARIO_PATH, ROOT / "schemas/scenario.v0.1.schema.json")
    _validate_json(REVIEW_PATH, ROOT / "schemas/render-review.v0.1.schema.json")


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
