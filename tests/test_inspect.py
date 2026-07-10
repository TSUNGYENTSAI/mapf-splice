from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path

import pytest

from mapf_splice.inspect import create_server
from mapf_splice.replay import FrameRecorder, replay_json
from mapf_splice.scenario import load_scenario
from mapf_splice.simulation import DeterministicSimulator

ROOT = Path(__file__).parents[1]
SCENARIO = ROOT / "scenarios/compact-three-robot/scenario.json"


def _artifact() -> dict:
    scenario = load_scenario(SCENARIO)
    recorder = FrameRecorder(scenario)
    simulator = DeterministicSimulator.from_scenario(scenario, committed_horizon=3)
    simulator.recorder = recorder
    simulator.tick()
    return recorder.artifact(termination_reason="tick", final_tick=1)


def test_inspector_serves_confirmed_wait_for(tmp_path: Path) -> None:
    artifact = _artifact()
    for frame in artifact["frames"]:
        assert "confirmed_wait_for" in frame
    replay_path = tmp_path / "run.json"
    replay_path.write_text(replay_json(artifact), encoding="utf-8")
    server = create_server(replay_path)  # validates against v0.2
    server.server_close()


def test_inspector_validates_and_serves_only_selected_replay(tmp_path: Path) -> None:
    artifact = _artifact()
    replay_path = tmp_path / "run.json"
    replay_path.write_text(replay_json(artifact), encoding="utf-8")
    server = create_server(replay_path)
    assert server.server_address[0] == "127.0.0.1"
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(f"{base}/run.json") as response:
            assert json.load(response)["scenario_id"] == artifact["scenario_id"]
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}/../../scenario.json")
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    invalid = deepcopy(artifact)
    del invalid["frames"]
    replay_path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(Exception):
        create_server(replay_path)
