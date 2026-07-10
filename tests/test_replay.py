from __future__ import annotations

from pathlib import Path

import pytest

from mapf_splice.replay import CHECKPOINTS, FrameRecorder, replay_json, validate_replay
from mapf_splice.run import export_run
from mapf_splice.scenario import load_scenario
from mapf_splice.simulation import DeterministicSimulator

ROOT = Path(__file__).parents[1]
SCENARIO = ROOT / "scenarios/compact-three-robot/scenario.json"


def _recorded(ticks: int = 9):
    scenario = load_scenario(SCENARIO)
    recorder = FrameRecorder(scenario)
    simulator = DeterministicSimulator.from_scenario(scenario, committed_horizon=3)
    simulator.recorder = recorder
    for _ in range(ticks):
        simulator.tick()
    return simulator, recorder.artifact(termination_reason="tick", final_tick=ticks)


def test_recording_is_read_only_and_deterministic() -> None:
    scenario = load_scenario(SCENARIO)
    plain = DeterministicSimulator.from_scenario(scenario, committed_horizon=3)
    recorded, left = _recorded()
    _, right = _recorded()
    for _ in range(9):
        plain.tick()

    assert recorded.world == plain.world
    assert recorded.trace.events == plain.trace.events
    assert left == right
    assert replay_json(left) == replay_json(right)
    validate_replay(left)


def test_replay_contains_topology_and_ordered_full_snapshots() -> None:
    _, artifact = _recorded(2)

    assert artifact["map_rows"]
    assert {station["kind"] for station in artifact["stations"]} == {
        "handoff",
        "delivery",
    }
    assert [frame["checkpoint"] for frame in artifact["frames"][:7]] == list(
        CHECKPOINTS
    )
    order = {checkpoint: index for index, checkpoint in enumerate(CHECKPOINTS)}
    keys = [(frame["tick"], order[frame["checkpoint"]]) for frame in artifact["frames"]]
    assert keys == sorted(keys)
    for frame in artifact["frames"]:
        for plan in frame["plans"]:
            indices = [action["action_index"] for action in plan["actions"]]
            assert indices == sorted(indices)


def test_runtime_evidence_and_containment_are_recorded_at_source() -> None:
    _, artifact = _recorded(19)
    preview_frames = [
        frame for frame in artifact["frames"] if frame["checkpoint"] == "after-preview"
    ]
    stable = next(
        frame for frame in preview_frames if frame["deadlock"]["newly_stable"]
    )
    assert stable["preview"]["dependencies"]
    assert stable["deadlock"]["candidates"][0]["observation_count"] >= 2
    assert stable["deadlock"]["containments"][0]["valid"]
    assert all(
        set(item) >= {"waiting_robot_id", "blocking_robot_id", "resource"}
        for item in stable["preview"]["dependencies"]
    )
    assert isinstance(stable["preview"]["contentions"], list)


def test_export_termination_and_max_tick_failure() -> None:
    data = export_run(
        SCENARIO,
        committed_horizon=3,
        until="tick",
        max_ticks=3,
        stop_tick=2,
    )
    assert data["termination_reason"] == "tick"
    assert data["final_tick"] == 2
    with pytest.raises(RuntimeError, match="max ticks"):
        export_run(
            SCENARIO,
            committed_horizon=3,
            until="quiescence",
            max_ticks=1,
            stop_tick=None,
        )
