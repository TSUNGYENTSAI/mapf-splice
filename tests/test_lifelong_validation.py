from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("numpy")

from mapf_splice.domain import TaskStatus  # noqa: E402
from mapf_splice.lifelong import (  # noqa: E402
    LifelongRunConfig,
    ProgressWatchdog,
    TerminationReason,
    run_lifelong_validation,
)
from mapf_splice.scenario import build_initial_world, load_scenario  # noqa: E402
from mapf_splice.workload import SeededTaskStream, WorkloadError  # noqa: E402

ROOT = Path(__file__).parents[1]
CONFIGS = ROOT / "validation/lifelong"


def _config(name: str) -> LifelongRunConfig:
    return LifelongRunConfig.from_json(CONFIGS / name)


def _release_sequence(seed: int):
    scenario = load_scenario(ROOT / "scenarios/compact-three-robot/scenario.json")
    world = build_initial_world(scenario)
    stream = SeededTaskStream(scenario, seed, 60)
    for tick in range(61):
        world.tick = tick
        stream.release_due(world)
    return stream.released


def test_seeded_task_generation_is_repeatable_valid_and_ordered() -> None:
    first = _release_sequence(12)
    assert first == _release_sequence(12)
    assert first != _release_sequence(13)
    assert len({task.id for task in first}) == len(first)
    assert [task.release_tick for task in first] == sorted(
        task.release_tick for task in first
    )
    assert all(task.release_tick >= 0 for task in first)
    assert all(task.pickup_station_id != task.delivery_station_id for task in first)


def test_random_initial_tasks_are_seeded_unique_and_simultaneous() -> None:
    scenario = load_scenario(ROOT / "scenarios/compact-four-robot/scenario.json")
    first_world = build_initial_world(scenario)
    first_stream = SeededTaskStream(scenario, 1043, 100, True)
    first = first_stream.prepare_initial_tasks(first_world)
    second_world = build_initial_world(scenario)
    second_stream = SeededTaskStream(scenario, 1043, 100, True)
    second = second_stream.prepare_initial_tasks(second_world)
    assert first == second
    assert len(first) == len(first_world.robots) == 4
    assert {task.release_tick for task in first} == {0}
    assert len({task.pickup_station_id for task in first}) == 4


@pytest.mark.parametrize(
    "name,recoveries,final_tick",
    [
        ("random-k3-flow-seed590.json", 0, 127),
        ("random-k3-one-recovery-seed202.json", 1, 134),
        ("random-k3-two-recoveries-seed615.json", 2, 148),
        ("random-k3-three-recoveries-seed213.json", 3, 123),
        ("random-k3-four-recoveries-seed1043.json", 4, 128),
    ],
)
def test_random_review_cases_give_every_robot_three_tasks_and_drain(
    name: str, recoveries: int, final_tick: int
) -> None:
    result = run_lifelong_validation(_config(name))
    assert result.summary["termination_reason"] == "completed-and-drained"
    assert result.summary["committed_horizon"] == 3
    assert result.summary["final_tick"] == final_tick
    assert min(result.summary["tasks_completed_by_robot"].values()) >= 3
    assert result.summary["recoveries_completed"] == recoveries
    assert any(
        robot["robot_id"] == "R4" and robot["position"]["row"] < 14
        for frame in result.replay["frames"]
        for robot in frame["robots"]
    )


def test_invalid_workload_and_watchdog_configuration_are_rejected() -> None:
    scenario = load_scenario(ROOT / "scenarios/compact-three-robot/scenario.json")
    with pytest.raises(WorkloadError):
        SeededTaskStream(scenario, 1, -1)
    with pytest.raises(ValueError):
        ProgressWatchdog(0)


def test_progress_watchdog_respects_legitimate_wait_and_times_out() -> None:
    watchdog = ProgressWatchdog(3)
    assert not watchdog.observe(1, progressed=True, legitimate_wait=False)
    assert not watchdog.observe(4, progressed=False, legitimate_wait=True)
    assert watchdog.observe(4, progressed=False, legitimate_wait=False)
    assert watchdog.last_progress_tick == 1
    assert watchdog.maximum_no_progress_interval == 3


@pytest.mark.parametrize(
    "name,expected_tick",
    [
        ("three-robot-k3.json", 71),
        ("three-robot-k4.json", 71),
        ("three-robot-k5.json", 72),
    ],
)
def test_horizon_cases_complete_two_sequential_recoveries(
    name: str, expected_tick: int
) -> None:
    result = run_lifelong_validation(_config(name))
    assert (
        result.summary["termination_reason"] == TerminationReason.COMPLETED_AND_DRAINED
    )
    assert result.summary["final_tick"] == expected_tick
    assert result.summary["tasks_released"] == result.summary["tasks_completed"] == 5
    assert result.summary["recoveries_installed"] == 2
    assert result.summary["recoveries_completed"] == 2
    assert result.summary["invariant_violation_count"] == 0
    assert result.failure is None


def test_delayed_run_is_exactly_reproducible_and_uses_multitick_actions() -> None:
    config = _config("three-robot-delayed.json")
    first = run_lifelong_validation(config)
    second = run_lifelong_validation(config)
    assert first.summary == second.summary
    assert first.replay == second.replay
    assert first.summary["termination_reason"] == "completed-and-drained"
    assert first.summary["recoveries_completed"] == 2
    confirmation_ticks = {
        frame["recovery"]["incident"]["confirmation_tick"]
        for frame in first.replay["frames"]
        if frame["recovery"] is not None
    }
    assert len(confirmation_ticks) == 2
    starts = [
        event
        for frame in first.replay["frames"]
        for event in frame["events"]
        if event["kind"] == "action-started"
    ]
    assert any(event["details"]["extra_delay_ticks"] > 0 for event in starts)


def test_four_robot_nonparticipant_stays_outside_recovery_and_progresses() -> None:
    result = run_lifelong_validation(_config("four-robot-nonparticipant.json"))
    recoveries = [
        frame["recovery"]
        for frame in result.replay["frames"]
        if frame["recovery"] is not None
    ]
    assert recoveries
    first_recovery = [
        recovery
        for recovery in recoveries
        if recovery["incident"]["confirmation_tick"] == 18
    ]
    assert all(
        recovery["participants"] == ["R1", "R2", "R3"] for recovery in first_recovery
    )
    assert any(
        "R4" in recovery["active_nonparticipants"] for recovery in first_recovery
    )
    install_ticks = [
        event["tick"]
        for frame in result.replay["frames"]
        for event in frame["events"]
        if event["kind"] == "recovery-install-succeeded"
    ]
    completion_ticks = [
        event["tick"]
        for frame in result.replay["frames"]
        for event in frame["events"]
        if event["kind"] == "recovery-completed"
    ]
    r4_progress = [
        event["tick"]
        for frame in result.replay["frames"]
        for event in frame["events"]
        if event["robot_id"] == "R4"
        and event["kind"] in {"action-started", "action-completed"}
    ]
    assert any(install_ticks[0] <= tick <= completion_ticks[0] for tick in r4_progress)
    assert result.summary["termination_reason"] == "completed-and-drained"


def test_solver_failure_is_typed_safe_and_serializable() -> None:
    result = run_lifelong_validation(_config("safe-solver-failure.json"))
    assert result.summary["termination_reason"] == "safe-recovery-failure"
    assert result.summary["recovery_planning_failures"] == 1
    assert result.summary["recoveries_installed"] == 0
    assert result.summary["invariant_violation_count"] == 0
    assert result.failure is not None
    json.dumps(result.summary, sort_keys=True)
    json.dumps(result.failure, sort_keys=True)
    frame = result.replay["frames"][-1]
    assert all(task["status"] != TaskStatus.COMPLETED for task in frame["tasks"])
