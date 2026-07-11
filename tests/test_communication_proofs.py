from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

pytest.importorskip("numpy")

from mapf_splice.communication import analyze_communication_proofs  # noqa: E402
from mapf_splice.lifelong import (  # noqa: E402
    LifelongRunConfig,
    run_lifelong_validation,
)

ROOT = Path(__file__).parents[1]
CASES = ROOT / "validation/lifelong"


def _report(name: str):
    config = LifelongRunConfig.from_json(CASES / name)
    replay = run_lifelong_validation(config).replay
    before = deepcopy(replay)
    report = analyze_communication_proofs(replay, case_id=name.removesuffix(".json"))
    assert replay == before
    return report


def test_four_robot_fixture_has_local_scope_proof_with_one_lifecycle_gap() -> None:
    report = _report("four-robot-nonparticipant.json")
    candidate = report["local_scope"][0]
    assert candidate["affected_scope"] == ["R1", "R2", "R3"]
    assert candidate["active_nonparticipants"] == ["R4"]
    assert candidate["versions_before"]["R4"] == 2
    assert candidate["versions_after"]["R4"] == 2
    assert candidate["nonparticipant_move_completions"] == 5
    assert candidate["unrelated_stable_cycles"] == 0
    assert candidate["checks"]["post_recovery_task_progress"] is False
    assert sum(not value for value in candidate["checks"].values()) == 1
    assert candidate["strict_pass"] is False
    splice = report["transactional_splice"][0]
    assert splice["participants"] == ["R1", "R2", "R3"]
    assert splice["nonparticipants"] == ["R4"]
    assert splice["strict_pass"] is True


def test_delayed_fixture_already_has_clean_cross_robot_unlocks() -> None:
    report = _report("three-robot-delayed.json")
    passing = [item for item in report["delayed_handoffs"] if item["strict_pass"]]
    assert len(passing) >= 3
    assert any(
        item["predecessor"] == "R2@3:2"
        and item["successor"] == "R1@3:2"
        and item["delay_ticks"] == 2
        and item["unlock_delta_ticks"] == 0
        for item in passing
    )


def test_three_robot_hero_proves_detect_contain_confirm_sequence() -> None:
    report = _report("three-robot-k3.json")
    candidate = report["detect_contain_confirm"][0]
    assert candidate["start_tick"] == 14
    assert candidate["stable_scc_tick"] == 16
    assert candidate["containment_tick"] == 16
    assert candidate["quiescence_tick"] == 18
    assert candidate["confirmation_tick"] == 18
    assert candidate["cycle_core"] == ["R1", "R2", "R3"]
    assert candidate["affected_scope"] == ["R1", "R2", "R3"]
    assert candidate["confirmed_cyclic_sccs"] == [["R1", "R2"]]
    assert candidate["strict_pass"] is True


def test_seed_615_proves_two_recovery_lifelong_continuation() -> None:
    report = _report("random-k3-two-recoveries-seed615.json")
    assert report["lifelong_continuation"] == {
        "recovery_completion_ticks": [26, 95],
        "strict_pass": True,
    }


def test_seed_1043_has_external_wait_then_resume_evidence() -> None:
    report = _report("random-k3-four-recoveries-seed1043.json")
    waits = report["external_waits"]
    assert len(waits) == 7
    assert all(item["strict_pass"] for item in waits)
    assert waits[-1] == {
        "wait_tick": 89,
        "resume_tick": 90,
        "resume_event": "recovery-prefix-granted",
        "strict_pass": True,
    }
