"""Replay evidence for the scoped recovery proposal (schema v0.2, in place)."""
from pathlib import Path

import pytest

pytest.importorskip("numpy")

from mapf_splice.replay import FrameRecorder, validate_replay  # noqa: E402
from mapf_splice.scenario import load_scenario  # noqa: E402
from mapf_splice.simulation import DeterministicSimulator  # noqa: E402
from mapf_splice.trace import EventKind  # noqa: E402

ROOT = Path(__file__).parents[1]
SCENARIO = ROOT / "scenarios/compact-three-robot/scenario.json"


def _hero_artifact(horizon: int):
    scenario = load_scenario(SCENARIO)
    recorder = FrameRecorder(scenario)
    sim = DeterministicSimulator.from_scenario(scenario, committed_horizon=horizon)
    sim.recorder = recorder
    final = 0
    for tick in range(60):
        sim.tick()
        final = tick + 1
        if any(
            event.kind is EventKind.RECOVERY_PROPOSAL_READY
            for event in sim.trace.events
        ):
            break
    return recorder.artifact(termination_reason="tick", final_tick=final)


@pytest.mark.parametrize("horizon", [3, 4, 5])
def test_replay_carries_recovery_proposal_and_validates(horizon: int) -> None:
    artifact = _hero_artifact(horizon)
    validate_replay(artifact)  # schema v0.2 in place

    ready = [
        frame
        for frame in artifact["frames"]
        if frame["recovery"] is not None
        and frame["recovery"]["state"] == "proposal-ready"
    ]
    assert ready, "no frame carried a proposal-ready recovery block"
    recovery = ready[-1]["recovery"]
    assert recovery["participants"] == ["R1", "R2", "R3"]
    assert recovery["adg_compiled"] is True
    assert recovery["failure_reason"] is None
    assert recovery["solver"]["solver"] == "pibt"
    assert recovery["solver"]["makespan"] == 12
    assert recovery["solver"]["seed"] == 0
    goals = {item["robot_id"]: item["cell"] for item in recovery["goals"]}
    assert goals["R1"] == {"row": 12, "col": 16}
    assert goals["R2"] == {"row": 12, "col": 4}
    assert goals["R3"] == {"row": 7, "col": 2}
    for path in recovery["paths"]:
        assert path["cells"][-1] == goals[path["robot_id"]]


def test_frames_before_recovery_have_null_recovery() -> None:
    artifact = _hero_artifact(3)
    # The very first frame (tick 0) precedes any containment/recovery.
    assert artifact["frames"][0]["recovery"] is None
