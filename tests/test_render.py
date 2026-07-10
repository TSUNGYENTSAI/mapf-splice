from pathlib import Path

from PIL import Image

from mapf_splice.render import render_scenario
from mapf_splice.scenario import load_scenario

ROOT = Path(__file__).parents[1]
SCENARIO_PATH = ROOT / "scenarios/compact-three-robot/scenario.json"


def test_renderer_writes_reproducible_png(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIO_PATH)
    first = tmp_path / "scenario-first.png"
    second = tmp_path / "scenario-second.png"

    render_scenario(
        scenario,
        output=first,
        cell_size=24,
    )
    render_scenario(
        scenario,
        output=second,
        cell_size=24,
    )

    assert first.read_bytes() == second.read_bytes()
    with Image.open(first) as image:
        assert image.format == "PNG"
        assert image.width > image.height
        assert image.width >= scenario.warehouse_map.width * 24
