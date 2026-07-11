from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path

import pytest

from mapf_splice.inspect import create_case_server, create_server
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
        with urllib.request.urlopen(f"{base}/capture.css") as response:
            capture_css = response.read()
            assert b"story-shell" in capture_css
            assert b"evidence-drawer" in capture_css
        with urllib.request.urlopen(f"{base}/story-visuals.css") as response:
            story_css = response.read()
            assert b"splice-visual" in story_css
            assert b"adg-visual" in story_css
            assert b"grid-template-columns:225px 36px 62px 36px 110px 94px 104px" in story_css
        with urllib.request.urlopen(f"{base}/app.js") as response:
            app = response.read()
            assert b"dependencyGraph" in app
            assert b"affected scope" in app
            assert b"confirmed wait-for graph" in app.lower()
            assert b"findFrame" not in app
            assert b"stageIndex" not in app
            assert b"playTimer" not in app
        with urllib.request.urlopen(f"{base}/playback.js") as response:
            playback = response.read()
            assert b"buildStorySequence" in playback
            assert b"createPlaybackController" in playback
            assert b"four-robot-nonparticipant" in playback
            assert b"three-robot-delayed" in playback
            assert b"random-k3-two-recoveries-seed615" in playback
        with urllib.request.urlopen(base) as response:
            html = response.read()
            assert b"Physical view" in html
            assert b"Logical view" in html
            assert b"Lifecycle view" in html
            assert b"Capture Mode" not in html
            assert b"Inspect mode" not in html
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


def test_inspector_serves_a_case_catalog_and_selected_runs() -> None:
    first = _artifact()
    second = deepcopy(first)
    second["committed_horizon"] = 4
    server = create_case_server({"hero-k3": first, "hero-k4": second})
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(f"{base}/cases.json") as response:
            catalog = json.load(response)
        assert [item["id"] for item in catalog] == ["hero-k3", "hero-k4"]
        assert [item["committed_horizon"] for item in catalog] == [3, 4]
        with urllib.request.urlopen(f"{base}/runs/hero-k4.json") as response:
            assert json.load(response)["committed_horizon"] == 4
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}/runs/missing.json")
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
