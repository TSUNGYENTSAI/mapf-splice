from pathlib import Path

import pytest
from PIL import Image

from mapf_splice.render import render_scenario
from mapf_splice.scenario import load_review, load_scenario

ROOT = Path(__file__).parents[1]
SCENARIO_PATH = ROOT / "scenarios/compact-three-robot/scenario.json"
REVIEW_PATH = ROOT / "scenarios/compact-three-robot/review.json"


@pytest.mark.parametrize(
    "view_id",
    ["prospective-scc-k3", "prospective-scc-k4", "prospective-scc-k5"],
)
def test_renderer_writes_reproducible_png(tmp_path: Path, view_id: str) -> None:
    scenario = load_scenario(SCENARIO_PATH)
    review = load_review(REVIEW_PATH, scenario)
    first = tmp_path / f"{view_id}-first.png"
    second = tmp_path / f"{view_id}-second.png"

    render_scenario(
        scenario,
        review=review,
        view_id=view_id,
        output=first,
        cell_size=24,
    )
    render_scenario(
        scenario,
        review=review,
        view_id=view_id,
        output=second,
        cell_size=24,
    )

    assert first.read_bytes() == second.read_bytes()
    with Image.open(first) as image:
        assert image.format == "PNG"
        assert image.width > image.height
        assert image.width >= scenario.warehouse_map.width * 24
