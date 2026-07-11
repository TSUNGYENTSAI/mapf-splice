from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from mapf_splice.delay import DeterministicDelaySchedule
from mapf_splice.domain import ActionStatus, EdgeResource, TaskStatus, VertexResource
from mapf_splice.replay import FrameRecorder, replay_json, validate_replay
from mapf_splice.scenario import load_scenario
from mapf_splice.simulation import DeterministicSimulator
from mapf_splice.trace import EventKind
from mapf_splice.workload import SeededTaskStream


class TerminationReason(StrEnum):
    COMPLETED_AND_DRAINED = "completed-and-drained"
    HORIZON_REACHED_SAFE = "horizon-reached-safe"
    NO_PROGRESS_TIMEOUT = "no-progress-timeout"
    SAFE_RECOVERY_FAILURE = "safe-recovery-failure"
    EXPECTED_UNSUPPORTED_BOUNDARY = "expected-unsupported-boundary"
    INVARIANT_VIOLATION = "invariant-violation"
    UNHANDLED_EXCEPTION = "unhandled-exception"


@dataclass(frozen=True, slots=True)
class LifelongRunConfig:
    scenario_path: Path
    workload_seed: int
    committed_horizon: int
    release_until_tick: int
    max_drain_ticks: int
    no_progress_threshold: int
    delay_seed: int | None = None
    delay_probability: float | None = None
    delay_minimum_extra_ticks: int | None = None
    delay_maximum_extra_ticks: int | None = None
    recovery_max_timestep: int = 256
    randomize_initial_tasks: bool = False
    expect_drain: bool = True

    def __post_init__(self) -> None:
        if self.release_until_tick < 0 or self.max_drain_ticks < 1:
            raise ValueError("release boundary must be nonnegative and drain positive")
        if self.no_progress_threshold < 1:
            raise ValueError("no-progress threshold must be positive")
        if self.recovery_max_timestep < 1:
            raise ValueError("recovery max timestep must be positive")
        if not 0 <= (self.delay_probability or 0.0) <= 1:
            raise ValueError("delay probability must be between zero and one")

    @property
    def final_max_tick(self) -> int:
        return self.release_until_tick + self.max_drain_ticks

    @classmethod
    def from_json(cls, path: Path) -> LifelongRunConfig:
        value = json.loads(path.read_text(encoding="utf-8"))
        scenario_path = Path(value["scenario_path"])
        if not scenario_path.is_absolute():
            scenario_path = (path.parent / scenario_path).resolve()
        return cls(
            scenario_path=scenario_path,
            **{k: v for k, v in value.items() if k != "scenario_path"},
        )

    def stable_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["scenario_path"] = str(self.scenario_path)
        value["final_max_tick"] = self.final_max_tick
        return value


@dataclass(frozen=True, slots=True)
class LifelongRunResult:
    config: LifelongRunConfig
    summary: dict[str, Any]
    replay: dict[str, Any]
    failure: dict[str, Any] | None


@dataclass(slots=True)
class ProgressWatchdog:
    threshold: int
    last_progress_tick: int = 0
    maximum_no_progress_interval: int = 0

    def __post_init__(self) -> None:
        if self.threshold < 1:
            raise ValueError("watchdog threshold must be positive")

    def observe(self, tick: int, *, progressed: bool, legitimate_wait: bool) -> bool:
        if tick < self.last_progress_tick:
            raise ValueError("watchdog ticks must be monotonic")
        if progressed:
            self.last_progress_tick = tick
        interval = tick - self.last_progress_tick
        self.maximum_no_progress_interval = max(
            self.maximum_no_progress_interval, interval
        )
        return interval >= self.threshold and not legitimate_wait


_PROGRESS_EVENTS = {
    EventKind.TASK_RELEASED,
    EventKind.TASK_ASSIGNED,
    EventKind.TASK_STATUS_CHANGED,
    EventKind.PLAN_INSTALLED,
    EventKind.ACTION_STARTED,
    EventKind.ACTION_COMPLETED,
    EventKind.ADMISSION_ACCEPTED,
    EventKind.STABLE_SCC_DETECTED,
    EventKind.CONTAINMENT_STARTED,
    EventKind.QUIESCENCE_REACHED,
    EventKind.RECOVERY_PROPOSAL_READY,
    EventKind.RECOVERY_INSTALL_SUCCEEDED,
    EventKind.RECOVERY_PREFIX_GRANTED,
    EventKind.RECOVERY_COMPLETED,
    EventKind.CONTAINMENT_CLEARED,
    EventKind.CONTAINMENT_INVALIDATED,
}

_SAFE_FAILURE_EVENTS = {
    EventKind.RECOVERY_PLANNING_FAILED,
    EventKind.RECOVERY_INSTALL_FAILED,
    EventKind.RECOVERY_ADMISSION_FAILED,
    EventKind.RECOVERY_ADMISSION_STALLED,
}


def _drained(simulator: DeterministicSimulator) -> bool:
    world = simulator.world
    return (
        all(task.status is TaskStatus.COMPLETED for task in world.tasks.values())
        and all(robot.active_task_id is None for robot in world.robots.values())
        and not world.plans
        and not world.reservations.all_committed_actions()
        and simulator.deadlock_controller.containment is None
    )


def _validate_aggregate(simulator: DeterministicSimulator) -> None:
    world = simulator.world
    world.validate()
    for task in world.tasks.values():
        if task.status is TaskStatus.COMPLETED and task.assigned_robot_id is None:
            raise ValueError("completed task lost its historical assignee")
    committed = set(world.reservations.all_committed_actions())
    for plan in world.plans.values():
        for action in plan.actions:
            if action.status is ActionStatus.COMPLETED and action.ref in committed:
                raise ValueError("completed action retains a reservation")
    containment = simulator.deadlock_controller.containment
    if containment is not None and containment.recovery_proposal is not None:
        participants = tuple(sorted(containment.recovery_proposal.plans))
        expected = tuple(robot_id for robot_id, _ in containment.scope_identity)
        if participants != expected:
            raise ValueError("recovery participants differ from frozen scope")


def _failure_snapshot(
    simulator: DeterministicSimulator,
    reason: TerminationReason,
    last_progress_tick: int,
    error: BaseException | None,
) -> dict[str, Any]:
    def resource_data(resource):
        if resource is None:
            return None
        if isinstance(resource, VertexResource):
            return {
                "type": "vertex",
                "cell": {"row": resource.cell.row, "col": resource.cell.col},
            }
        assert isinstance(resource, EdgeResource)
        return {
            "type": "edge",
            "first": {"row": resource.first.row, "col": resource.first.col},
            "second": {"row": resource.second.row, "col": resource.second.col},
        }

    world = simulator.world
    containment = simulator.deadlock_controller.containment
    admission = None if containment is None else containment.recovery_admission
    blocker_evidence = []
    if admission is not None:
        for robot in admission.robots:
            for blocker in robot.blockers:
                blocker_evidence.append(
                    {
                        "waiting_robot_id": robot.robot_id,
                        "blocking_robot_id": blocker.robot_id,
                        "blocking_action": (
                            None
                            if blocker.action_ref is None
                            else {
                                "robot_id": blocker.action_ref.robot_id,
                                "plan_version": blocker.action_ref.plan_version,
                                "action_index": blocker.action_ref.action_index,
                            }
                        ),
                        "internal": blocker.internal,
                        "resource": resource_data(blocker.resource),
                    }
                )
    return {
        "termination_reason": reason.value,
        "tick": world.tick,
        "last_progress_tick": last_progress_tick,
        "active_robots": [
            {
                "robot_id": robot.id,
                "task_id": robot.active_task_id,
                "position": {"row": robot.position.row, "col": robot.position.col},
                "plan_version": robot.plan_version,
            }
            for robot in sorted(world.robots.values(), key=lambda item: item.id)
            if robot.active_task_id is not None
        ],
        "active_tasks": [
            {
                "task_id": task.id,
                "status": task.status.value,
                "robot_id": task.assigned_robot_id,
            }
            for task in sorted(world.tasks.values(), key=lambda item: item.id)
            if task.status is not TaskStatus.COMPLETED
        ],
        "running_actions": [
            {
                "robot_id": robot.id,
                "plan_version": robot.active_action_ref.plan_version,
                "action_index": robot.active_action_ref.action_index,
                "remaining_ticks": robot.remaining_ticks,
            }
            for robot in sorted(world.robots.values(), key=lambda item: item.id)
            if robot.active_action_ref is not None
        ],
        "committed_reservations": [
            {
                "robot_id": ref.robot_id,
                "plan_version": ref.plan_version,
                "action_index": ref.action_index,
            }
            for ref in world.reservations.all_committed_actions()
        ],
        "containment": None
        if containment is None
        else {
            "scope": [
                {"robot_id": robot_id, "plan_version": version}
                for robot_id, version in containment.scope_identity
            ],
            "state": containment.state.value,
            "recovery_state": containment.recovery_state.value,
        },
        "blocker_evidence": blocker_evidence,
        "exception": None
        if error is None
        else {"type": type(error).__name__, "message": str(error)},
    }


def _summary(
    simulator: DeterministicSimulator,
    config: LifelongRunConfig,
    scenario_hash: str,
    reason: TerminationReason,
    last_progress_tick: int,
    max_no_progress: int,
    invariant_violations: int,
) -> dict[str, Any]:
    events = simulator.trace.events

    def count(kind: EventKind) -> int:
        return sum(event.kind is kind for event in events)

    assigned = {
        event.task_id for event in events if event.kind is EventKind.TASK_ASSIGNED
    }
    assigned_by_robot = {
        robot_id: len(
            {
                event.task_id
                for event in events
                if event.kind is EventKind.TASK_ASSIGNED and event.robot_id == robot_id
            }
        )
        for robot_id in sorted(simulator.world.robots)
    }
    completed_by_robot = {
        robot_id: sum(
            task.status is TaskStatus.COMPLETED and task.assigned_robot_id == robot_id
            for task in simulator.world.tasks.values()
        )
        for robot_id in sorted(simulator.world.robots)
    }
    return {
        "scenario_id": simulator.recorder.scenario.data["id"],
        "scenario_path": str(config.scenario_path),
        "scenario_content_hash": scenario_hash,
        "workload_seed": config.workload_seed,
        "delay_seed": simulator.delay_schedule.seed,
        "delay_probability": simulator.delay_schedule.probability,
        "delay_minimum_extra_ticks": simulator.delay_schedule.minimum_extra_ticks,
        "delay_maximum_extra_ticks": simulator.delay_schedule.maximum_extra_ticks,
        "committed_horizon": config.committed_horizon,
        "release_until_tick": config.release_until_tick,
        "final_tick": simulator.world.tick,
        "termination_reason": reason.value,
        "tasks_released": sum(
            task.release_tick <= simulator.world.tick
            for task in simulator.world.tasks.values()
        ),
        "tasks_assigned": len(assigned),
        "tasks_assigned_by_robot": assigned_by_robot,
        "tasks_completed": sum(
            task.status is TaskStatus.COMPLETED
            for task in simulator.world.tasks.values()
        ),
        "tasks_completed_by_robot": completed_by_robot,
        "actions_started": count(EventKind.ACTION_STARTED),
        "actions_completed": count(EventKind.ACTION_COMPLETED),
        "stable_sccs_detected": count(EventKind.STABLE_SCC_DETECTED),
        "hard_deadlocks_confirmed": count(EventKind.HARD_DEADLOCK_CONFIRMED),
        "recovery_proposals_created": count(EventKind.RECOVERY_PROPOSAL_READY),
        "recoveries_installed": count(EventKind.RECOVERY_INSTALL_SUCCEEDED),
        "recoveries_completed": count(EventKind.RECOVERY_COMPLETED),
        "recovery_planning_failures": count(EventKind.RECOVERY_PLANNING_FAILED),
        "recovery_installation_failures": count(EventKind.RECOVERY_INSTALL_FAILED),
        "recovery_admission_failures": count(EventKind.RECOVERY_ADMISSION_FAILED),
        "recovery_stalls": count(EventKind.RECOVERY_ADMISSION_STALLED),
        "external_recovery_waits": count(EventKind.RECOVERY_ADMISSION_EXTERNAL_WAIT),
        "maximum_no_progress_interval": max_no_progress,
        "last_progress_tick": last_progress_tick,
        "invariant_violation_count": invariant_violations,
    }


def run_lifelong_validation(config: LifelongRunConfig) -> LifelongRunResult:
    scenario = load_scenario(config.scenario_path)
    recorder = FrameRecorder(scenario)
    simulator = DeterministicSimulator.from_scenario(
        scenario, committed_horizon=config.committed_horizon
    )
    simulator.recorder = recorder
    simulator.recovery_max_timestep = config.recovery_max_timestep
    simulator.task_stream = SeededTaskStream(
        scenario,
        config.workload_seed,
        config.release_until_tick,
        config.randomize_initial_tasks,
    )
    simulator.task_stream.prepare_initial_tasks(simulator.world)
    delay = scenario.data["execution"]["delay_schedule"]
    simulator.delay_schedule = DeterministicDelaySchedule(
        seed=config.delay_seed if config.delay_seed is not None else delay["seed"],
        probability=config.delay_probability
        if config.delay_probability is not None
        else delay["probability"],
        minimum_extra_ticks=config.delay_minimum_extra_ticks
        if config.delay_minimum_extra_ticks is not None
        else delay["extra_wait_ticks"]["minimum"],
        maximum_extra_ticks=config.delay_maximum_extra_ticks
        if config.delay_maximum_extra_ticks is not None
        else delay["extra_wait_ticks"]["maximum"],
    )
    watchdog = ProgressWatchdog(config.no_progress_threshold)
    invariant_violations = 0
    reason: TerminationReason | None = None
    error: BaseException | None = None
    event_index = 0
    while simulator.world.tick < config.final_max_tick:
        try:
            simulator.tick()
            _validate_aggregate(simulator)
        except (AssertionError, ValueError) as caught:
            invariant_violations += 1
            reason, error = TerminationReason.INVARIANT_VIOLATION, caught
            break
        except Exception as caught:  # failure artifact must preserve unexpected errors
            reason, error = TerminationReason.UNHANDLED_EXCEPTION, caught
            break
        new_events = simulator.trace.events[event_index:]
        event_index = len(simulator.trace.events)
        progressed = any(event.kind in _PROGRESS_EVENTS for event in new_events)
        watchdog.observe(
            simulator.world.tick, progressed=progressed, legitimate_wait=True
        )
        if any(event.kind in _SAFE_FAILURE_EVENTS for event in new_events):
            reason = TerminationReason.SAFE_RECOVERY_FAILURE
            break
        if any(
            event.kind is EventKind.CONFIRMATION_UNSUPPORTED for event in new_events
        ):
            reason = TerminationReason.EXPECTED_UNSUPPORTED_BOUNDARY
            break
        if simulator.world.tick > config.release_until_tick and _drained(simulator):
            reason = TerminationReason.COMPLETED_AND_DRAINED
            break
        running = any(
            robot.active_action_ref is not None
            for robot in simulator.world.robots.values()
        )
        future_release = (
            simulator.task_stream.next_release_tick <= config.release_until_tick
        )
        if watchdog.observe(
            simulator.world.tick,
            progressed=progressed,
            legitimate_wait=running or future_release,
        ):
            reason = TerminationReason.NO_PROGRESS_TIMEOUT
            break
    if reason is None:
        reason = TerminationReason.HORIZON_REACHED_SAFE
    replay = recorder.artifact(
        termination_reason=reason.value, final_tick=simulator.world.tick
    )
    validate_replay(replay)
    scenario_hash = hashlib.sha256(config.scenario_path.read_bytes()).hexdigest()
    summary = _summary(
        simulator,
        config,
        scenario_hash,
        reason,
        watchdog.last_progress_tick,
        watchdog.maximum_no_progress_interval,
        invariant_violations,
    )
    expected_success = reason is TerminationReason.COMPLETED_AND_DRAINED or (
        reason is TerminationReason.HORIZON_REACHED_SAFE and not config.expect_drain
    )
    failure = (
        None
        if expected_success
        else _failure_snapshot(simulator, reason, watchdog.last_progress_tick, error)
    )
    return LifelongRunResult(config, summary, replay, failure)


def write_lifelong_artifacts(result: LifelongRunResult, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    documents = {
        "config.json": result.config.stable_dict(),
        "summary.json": result.summary,
        "replay.json": result.replay,
    }
    if result.failure is not None:
        documents["failure.json"] = result.failure
    for name, value in documents.items():
        text = (
            replay_json(value)
            if name == "replay.json"
            else json.dumps(value, indent=2, sort_keys=True) + "\n"
        )
        (output / name).write_text(text, encoding="utf-8")
