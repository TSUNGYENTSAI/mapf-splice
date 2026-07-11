"""Explicit recovery bounded-prefix admission and executable hero recovery."""
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
    Plan,
)
from mapf_splice.recovery import (
    RecoveryIncidentRef,  # noqa: E402
    RecoveryState,  # noqa: E402
)
from mapf_splice.scenario import load_scenario  # noqa: E402
from mapf_splice.simulation import DeterministicSimulator  # noqa: E402
from mapf_splice.trace import EventKind  # noqa: E402
from mapf_splice.traffic import (  # noqa: E402
    CommittedReservationLedger,
    RecoveryAdmissionRequest,
    RecoveryBlockedReason,
    TrafficError,
)

ROOT = Path(__file__).parents[1]
SCENARIO = ROOT / "scenarios/compact-three-robot/scenario.json"


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
    with pytest.raises(TrafficError, match="injected"):
        ledger.admit_recovery_group(
            _request(("A", "B")),
            plans,
            occupied={Cell(0, 0): "A", Cell(1, 0): "B"},
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


def test_zero_authority_zero_grant_stalls_without_retry() -> None:
    sim = DeterministicSimulator.from_scenario(
        load_scenario(SCENARIO), committed_horizon=3
    )
    while sim.deadlock_controller.containment is None or (
        sim.deadlock_controller.containment.recovery_state
        is not RecoveryState.INSTALLED
    ):
        sim.tick()
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
    while sim.deadlock_controller.containment is None or (
        sim.deadlock_controller.containment.recovery_state
        is not RecoveryState.INSTALLED
    ):
        sim.tick()
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
