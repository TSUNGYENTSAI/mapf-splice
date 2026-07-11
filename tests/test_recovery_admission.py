"""Explicit recovery bounded-prefix admission and executable hero recovery."""
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("numpy")

from mapf_splice.delay import DeterministicDelaySchedule  # noqa: E402
from mapf_splice.domain import (  # noqa: E402
    Action,
    ActionKind,
    ActionRef,
    ActionStatus,
    Cell,
    EdgeResource,
    Plan,
    Robot,
    Task,
    TaskStatus,
    VertexResource,
)
from mapf_splice.planning import compile_path  # noqa: E402
from mapf_splice.recovery import (  # noqa: E402
    RecoveryIncidentRef,  # noqa: E402
    RecoveryState,  # noqa: E402
)
from mapf_splice.replay import FrameRecorder, validate_replay  # noqa: E402
from mapf_splice.scenario import WarehouseMap, load_scenario  # noqa: E402
from mapf_splice.simulation import DeterministicSimulator  # noqa: E402
from mapf_splice.trace import EventKind  # noqa: E402
from mapf_splice.traffic import (  # noqa: E402
    CommittedReservationLedger,
    RecoveryAdmissionError,
    RecoveryAdmissionFailureReason,
    RecoveryAdmissionRequest,
    RecoveryBlockedReason,
    TrafficError,
)

ROOT = Path(__file__).parents[1]
SCENARIO = ROOT / "scenarios/compact-three-robot/scenario.json"
FOUR_ROBOT_SCENARIO = ROOT / "scenarios/compact-four-robot/scenario.json"


def _advance_to_installed(sim: DeterministicSimulator, limit: int = 100) -> None:
    for _ in range(limit):
        sim.tick()
        containment = sim.deadlock_controller.containment
        if containment is not None and (
            containment.recovery_state is RecoveryState.INSTALLED
        ):
            return
    pytest.fail(f"recovery was not installed within {limit} ticks")


def _plan(robot_id: str, row: int, *, dependency: ActionRef | None = None) -> Plan:
    first = Action(
        ActionRef(robot_id, 3, 0),
        ActionKind.MOVE,
        Cell(row, 0),
        Cell(row, 1),
        dependencies=() if dependency is None else (dependency,),
    )
    second = Action(
        ActionRef(robot_id, 3, 1),
        ActionKind.MOVE,
        Cell(row, 1),
        Cell(row, 2),
        dependencies=(first.ref,),
    )
    return Plan(robot_id, 3, f"T-{robot_id}", Cell(row, 2), (first, second))


def _request(ids: tuple[str, ...], horizon: int = 2) -> RecoveryAdmissionRequest:
    return RecoveryAdmissionRequest(
        RecoveryIncidentRef(
            tuple((robot_id, 2) for robot_id in ids[:2]),
            tuple((robot_id, 2) for robot_id in ids),
            18,
        ),
        tuple((robot_id, 3) for robot_id in ids),
        ids,
        horizon,
        19,
    )


def test_request_rejects_duplicate_installed_robot_id() -> None:
    with pytest.raises(TrafficError, match="robot ids must be unique"):
        RecoveryAdmissionRequest(
            RecoveryIncidentRef(
                (("A", 2), ("B", 2)), (("A", 2), ("B", 2)), 18
            ),
            (("A", 3), ("A", 4), ("B", 3)),
            ("A", "B"),
            1,
            19,
        )


def test_recovery_admission_rejects_duplicate_robot_plans() -> None:
    plan = _plan("A", 0)
    other = _plan("B", 1)
    ledger = CommittedReservationLedger(1)
    with pytest.raises(RecoveryAdmissionError) as caught:
        ledger.admit_recovery_group(
            _request(("A", "B"), 1),
            (plan, plan, other),
            occupied={Cell(0, 0): "A", Cell(1, 0): "B"},
        )
    assert (
        caught.value.reason
        is RecoveryAdmissionFailureReason.PARTICIPANT_COVERAGE_MISMATCH
    )


def test_layered_scan_order_is_observable() -> None:
    plans = tuple(_plan(robot_id, row) for row, robot_id in enumerate(("A", "B", "C")))
    ledger = CommittedReservationLedger(2)
    result = ledger.admit_recovery_group(
        _request(("A", "B", "C")),
        plans,
        occupied={
            Cell(row, 0): robot_id
            for row, robot_id in enumerate(("A", "B", "C"))
        },
    )
    assert result.evaluation_order == (
        ActionRef("A", 3, 0),
        ActionRef("B", 3, 0),
        ActionRef("C", 3, 0),
        ActionRef("A", 3, 1),
        ActionRef("B", 3, 1),
        ActionRef("C", 3, 1),
    )
    assert all(len(robot.granted_actions) == 2 for robot in result.robots)


@pytest.mark.parametrize(
    ("status", "live_commit", "allowed"),
    [
        (ActionStatus.PLANNED, False, False),
        (ActionStatus.PLANNED, True, False),
        (ActionStatus.RUNNING, True, False),
        (ActionStatus.COMPLETED, False, True),
    ],
)
def test_cross_robot_dependency_requires_completion(
    status: ActionStatus, live_commit: bool, allowed: bool
) -> None:
    predecessor = _plan("A", 0)
    dependent = _plan("B", 1, dependency=predecessor.actions[0].ref)
    predecessor.actions[0].status = status
    ledger = CommittedReservationLedger(1)
    if live_commit:
        ledger._commit((predecessor.actions[0],))
    result = ledger.admit_recovery_group(
        _request(("A", "B"), 1),
        (predecessor, dependent),
        occupied={Cell(0, 0): "A", Cell(1, 0): "B"},
    )
    robot = next(item for item in result.robots if item.robot_id == "B")
    assert bool(robot.granted_actions) is allowed
    if not allowed:
        assert (
            robot.blocked_reason
            is RecoveryBlockedReason.UNMET_CROSS_ROBOT_DEPENDENCY
        )


def test_external_occupancy_is_typed_retryable_blocking_evidence() -> None:
    plans = (_plan("A", 0), _plan("B", 1))
    ledger = CommittedReservationLedger(1)
    result = ledger.admit_recovery_group(
        _request(("A", "B"), 1),
        plans,
        occupied={Cell(0, 0): "A", Cell(1, 0): "B", Cell(0, 1): "N"},
    )
    robot = next(item for item in result.robots if item.robot_id == "A")
    assert robot.blocked_reason is RecoveryBlockedReason.OCCUPIED_RESOURCE
    assert robot.blockers[0].robot_id == "N"
    assert robot.blockers[0].internal is False
    assert result.has_external_block is True


def test_mixed_internal_and_external_conflicts_preserve_all_blocker_evidence() -> None:
    plans = (_plan("A", 0), _plan("B", 1))
    ledger = CommittedReservationLedger(1)
    action = plans[0].actions[0]
    internal = plans[1].actions[0].ref
    external = ActionRef("N", 7, 0)
    vertex = VertexResource(action.end)
    edge = EdgeResource(action.start, action.end)
    ledger._owners_by_resource = {vertex: {internal}, edge: {external}}
    ledger._resources_by_action = {internal: {vertex}, external: {edge}}

    result = ledger.admit_recovery_group(
        _request(("A", "B"), 1),
        plans,
        occupied={Cell(0, 0): "A", Cell(1, 0): "B"},
    )

    robot = next(item for item in result.robots if item.robot_id == "A")
    assert robot.blocking_resource == vertex
    evidence = {
        (item.resource, item.robot_id, item.internal) for item in robot.blockers
    }
    assert evidence == {
        (vertex, "B", True),
        (edge, "N", False),
    }
    assert result.has_external_block is True


def test_missing_dependency_is_invalid_recovery_plan() -> None:
    plans = (_plan("A", 0, dependency=ActionRef("N", 9, 0)), _plan("B", 1))
    ledger = CommittedReservationLedger(1)
    with pytest.raises(RecoveryAdmissionError) as caught:
        ledger.admit_recovery_group(
            _request(("A", "B"), 1),
            plans,
            occupied={Cell(0, 0): "A", Cell(1, 0): "B"},
        )
    assert caught.value.reason is RecoveryAdmissionFailureReason.INVALID_RECOVERY_PLAN


def test_noncontiguous_completed_prefix_is_invalid_current_frontier() -> None:
    plans = (_plan("A", 0), _plan("B", 1))
    plans[0].actions[1].status = ActionStatus.COMPLETED
    ledger = CommittedReservationLedger(1)
    with pytest.raises(RecoveryAdmissionError) as caught:
        ledger.admit_recovery_group(
            _request(("A", "B"), 1),
            plans,
            occupied={Cell(0, 0): "A", Cell(1, 0): "B"},
        )
    assert (
        caught.value.reason
        is RecoveryAdmissionFailureReason.INVALID_CURRENT_FRONTIER
    )


def test_blocked_frontier_prevents_later_suffix_but_other_robot_continues() -> None:
    blocker = _plan("A", 0, dependency=ActionRef("B", 3, 0))
    other = _plan("B", 1)
    ledger = CommittedReservationLedger(2)
    result = ledger.admit_recovery_group(
        _request(("A", "B")),
        (blocker, other),
        occupied={Cell(0, 0): "A", Cell(1, 0): "B"},
    )
    robots = {robot.robot_id: robot for robot in result.robots}
    assert robots["A"].evaluated_actions == (ActionRef("A", 3, 0),)
    assert robots["A"].granted_actions == ()
    assert robots["B"].granted_actions == (
        ActionRef("B", 3, 0),
        ActionRef("B", 3, 1),
    )


def test_atomic_publication_failure_leaves_ledger_unchanged() -> None:
    class FailingLedger(CommittedReservationLedger):
        def _publish_recovery_grants(self, actions, staged_owners) -> None:
            raise TrafficError("injected final publication failure")

    plans = (_plan("A", 0), _plan("B", 1))
    ledger = FailingLedger(2)
    before = ledger.reservation_snapshot()
    with pytest.raises(RecoveryAdmissionError, match="injected") as caught:
        ledger.admit_recovery_group(
            _request(("A", "B")),
            plans,
            occupied={Cell(0, 0): "A", Cell(1, 0): "B"},
        )
    assert (
        caught.value.reason
        is RecoveryAdmissionFailureReason.ATOMIC_PUBLICATION_FAILED
    )
    assert ledger.reservation_snapshot() == before
    assert ledger.all_committed_actions() == ()


@pytest.mark.parametrize("horizon", [3, 4, 5])
def test_hero_executes_recovery_and_resumes_tasks_safely(horizon: int) -> None:
    sim = DeterministicSimulator.from_scenario(
        load_scenario(SCENARIO), committed_horizon=horizon
    )
    saw_recovery_start = False
    for _ in range(100):
        sim.tick()
        sim.world.validate()
        assert len(sim.world.occupied_cells()) == len(sim.world.robots)
        containment = sim.deadlock_controller.containment
        if containment and containment.installed_plan_versions:
            for robot_id, version in containment.installed_plan_versions.items():
                committed = sim.world.reservations.committed_actions(robot_id, version)
                assert len(committed) <= horizon
                indices = tuple(ref.action_index for ref in committed)
                if indices:
                    assert indices == tuple(
                        range(indices[0], indices[0] + len(indices))
                    )
        saw_recovery_start |= any(
            event.kind is EventKind.ACTION_STARTED
            and event.action_ref is not None
            and event.action_ref.plan_version == 3
            for event in sim.trace.events
        )
        if any(
            event.kind is EventKind.RECOVERY_COMPLETED
            for event in sim.trace.events
        ):
            break
    assert saw_recovery_start
    completed = [
        event
        for event in sim.trace.events
        if event.kind is EventKind.RECOVERY_COMPLETED
    ]
    assert completed[-1].tick == 34
    assert sim.deadlock_controller.containment is None
    assert {task.status.value for task in sim.world.tasks.values()} == {"completed"}


def test_hero_recovery_is_deterministic() -> None:
    def evidence():
        sim = DeterministicSimulator.from_scenario(
            load_scenario(SCENARIO), committed_horizon=3
        )
        for _ in range(40):
            sim.tick()
        return tuple(
            (event.tick, event.kind, event.robot_id, event.action_ref, event.details)
            for event in sim.trace.events
            if event.kind
            in {
                EventKind.RECOVERY_ADMISSION_EVALUATED,
                EventKind.RECOVERY_PREFIX_GRANTED,
                EventKind.ACTION_STARTED,
                EventKind.ACTION_COMPLETED,
                EventKind.RECOVERY_COMPLETED,
            }
        )

    assert evidence() == evidence()


def test_active_nonparticipant_executes_during_scoped_recovery() -> None:
    sim = DeterministicSimulator.from_scenario(
        load_scenario(FOUR_ROBOT_SCENARIO), committed_horizon=3
    )
    initial_r4 = sim.world.robots["R4"]
    for _ in range(100):
        sim.tick()
        containment = sim.deadlock_controller.containment
        if containment is not None:
            break
    else:
        pytest.fail("containment did not start within 100 ticks")

    r4_plan_before_splice = sim.world.plans["R4"]
    r4_plan_contract = (
        r4_plan_before_splice.version,
        r4_plan_before_splice.task_id,
        r4_plan_before_splice.phase_goal,
        tuple(action.ref for action in r4_plan_before_splice.actions),
    )
    r4_task_id = initial_r4.active_task_id
    _advance_to_installed(sim)

    containment = sim.deadlock_controller.containment
    assert containment is not None
    assert containment.scope_identity == (("R1", 2), ("R2", 2), ("R3", 2))
    assert tuple(containment.recovery_proposal.plans) == ("R1", "R2", "R3")
    assert containment.installed_plan_versions == {"R1": 3, "R2": 3, "R3": 3}
    assert sim.world.robots["R4"].active_task_id == r4_task_id
    installed_r4_plan = sim.world.plans["R4"]
    assert (
        installed_r4_plan.version,
        installed_r4_plan.task_id,
        installed_r4_plan.phase_goal,
        tuple(action.ref for action in installed_r4_plan.actions),
    ) == r4_plan_contract
    assert sim.world.robots["R4"].plan_version == 2
    installed_tick = sim.world.tick

    for _ in range(100):
        sim.tick()
        sim.world.validate()
        assert len(sim.world.occupied_cells()) == len(sim.world.robots)
        if any(
            event.kind is EventKind.RECOVERY_COMPLETED
            for event in sim.trace.events
        ):
            break

    completed_tick = next(
        event.tick
        for event in sim.trace.events
        if event.kind is EventKind.RECOVERY_COMPLETED
    )
    r4_progress = [
        event
        for event in sim.trace.events
        if event.robot_id == "R4"
        and installed_tick <= event.tick <= completed_tick
        and event.kind in {EventKind.ACTION_STARTED, EventKind.ACTION_COMPLETED}
    ]
    assert r4_progress
    assert sim.world.robots["R4"].plan_version == 2
    assert sim.deadlock_controller.containment is None


def test_external_nonparticipant_releases_frontier_then_recovery_retries() -> None:
    scenario = load_scenario(SCENARIO)
    rows = list(scenario.warehouse_map.rows)
    for row_index in (12, 13):
        row = list(rows[row_index])
        row[9] = "."
        rows[row_index] = "".join(row)
    scenario = replace(scenario, warehouse_map=WarehouseMap(tuple(rows)))
    sim = DeterministicSimulator.from_scenario(
        scenario, committed_horizon=3
    )
    sim.recorder = FrameRecorder(scenario)
    _advance_to_installed(sim)

    # A short side spur lets R4's ordinary admitted prefix naturally claim the
    # recovery frontier at (11, 9), enter it, then leave it again.
    path = (
        Cell(13, 9),
        Cell(12, 9),
        Cell(11, 9),
        Cell(12, 9),
        Cell(13, 9),
    )
    task = Task(
        "T-R4-BLOCK",
        path[0],
        path[-1],
        0,
        TaskStatus.TO_DROPOFF,
        "R4",
    )
    sim.world.robots["R4"] = Robot(
        "R4",
        path[0],
        active_task_id=task.id,
        payload_task_id=task.id,
        plan_version=2,
    )
    sim.world.tasks[task.id] = task
    sim.world.plans["R4"] = compile_path(
        path, robot_id="R4", plan_version=2, task_id=task.id
    )
    sim.world.validate()

    for _ in range(100):
        sim.tick()
        sim.world.validate()
        assert len(sim.world.occupied_cells()) == len(sim.world.robots)
        if any(
            event.kind is EventKind.RECOVERY_COMPLETED
            for event in sim.trace.events
        ):
            break

    external_wait = next(
        event
        for event in sim.trace.events
        if event.kind is EventKind.RECOVERY_ADMISSION_EXTERNAL_WAIT
        and "R4" in dict(event.details)["blocking_robots"].split(",")
    )
    r4_release = next(
        event
        for event in sim.trace.events
        if event.kind is EventKind.ACTION_COMPLETED and event.robot_id == "R4"
    )
    recovery_completed = next(
        event
        for event in sim.trace.events
        if event.kind is EventKind.RECOVERY_COMPLETED
    )
    assert external_wait.tick <= r4_release.tick < recovery_completed.tick
    assert not any(
        event.kind is EventKind.RECOVERY_ADMISSION_STALLED
        for event in sim.trace.events
    )
    artifact = sim.recorder.artifact(
        termination_reason="quiescence", final_tick=sim.world.tick
    )
    validate_replay(artifact)
    external_blockers = [
        blocker
        for frame in artifact["frames"]
        if frame["recovery"] and frame["recovery"]["admission"]
        for robot in frame["recovery"]["admission"]["robots"]
        for blocker in robot["blockers"]
        if blocker["robot_id"] == "R4" and blocker["internal"] is False
    ]
    assert external_blockers
    assert {item["resource"]["type"] for item in external_blockers} >= {"vertex"}


def test_zero_authority_zero_grant_stalls_without_retry() -> None:
    sim = DeterministicSimulator.from_scenario(
        load_scenario(SCENARIO), committed_horizon=3
    )
    _advance_to_installed(sim)
    plans = sim.world.plans
    plans["R3"].actions[0].dependencies = (plans["R1"].actions[-1].ref,)
    sim.tick()
    assert (
        sim.deadlock_controller.containment.recovery_state
        is RecoveryState.ADMISSION_STALLED
    )
    count = sum(
        event.kind is EventKind.RECOVERY_ADMISSION_EVALUATED
        for event in sim.trace.events
    )
    sim.tick()
    assert sum(
        event.kind is EventKind.RECOVERY_ADMISSION_EVALUATED
        for event in sim.trace.events
    ) == count


def test_delayed_recovery_completes_with_dependency_order_preserved() -> None:
    sim = DeterministicSimulator.from_scenario(
        load_scenario(SCENARIO), committed_horizon=3
    )
    _advance_to_installed(sim)
    dependencies = {
        action.ref: action.dependencies
        for plan in sim.deadlock_controller.containment.recovery_proposal.plans.values()
        for action in plan.actions
    }
    sim.delay_schedule = DeterministicDelaySchedule(7, 1.0, 2, 2)
    for _ in range(100):
        sim.tick()
        sim.world.validate()
        if any(
            event.kind is EventKind.RECOVERY_COMPLETED
            for event in sim.trace.events
        ):
            break
    events = sim.trace.events
    completed_at = {
        event.action_ref: event.tick
        for event in events
        if event.kind is EventKind.ACTION_COMPLETED and event.action_ref is not None
    }
    for event in events:
        if (
            event.kind is EventKind.ACTION_STARTED
            and event.action_ref is not None
            and event.action_ref.plan_version == 3
        ):
            assert all(
                completed_at[dependency] <= event.tick
                for dependency in dependencies[event.action_ref]
            )
    assert any(event.kind is EventKind.RECOVERY_COMPLETED for event in events)
