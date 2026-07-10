import pytest

from mapf_splice.domain import (
    ActionRef,
    ActionStatus,
    Cell,
    EdgeResource,
    VertexResource,
)
from mapf_splice.planning import compile_path
from mapf_splice.traffic import CommittedReservationLedger, TrafficError


def _plan(
    cells: tuple[Cell, ...],
    *,
    robot_id: str = "R1",
    version: int = 1,
):
    return compile_path(
        cells,
        robot_id=robot_id,
        plan_version=version,
        task_id=f"T-{robot_id}",
    )


def _initial(
    ledger: CommittedReservationLedger,
    *plans,
    occupied,
):
    return ledger.acquire_initial_batch(plans, occupied=occupied)


def _complete(plan, action_index: int) -> None:
    action = plan.actions[action_index]
    action.transition_to(ActionStatus.RUNNING)
    action.transition_to(ActionStatus.COMPLETED)


def test_initial_horizon_rejects_atomically_and_reports_safe_prefix() -> None:
    plan = _plan(tuple(Cell(0, col) for col in range(4)))
    ledger = CommittedReservationLedger(horizon=3)

    decision = _initial(
        ledger,
        plan,
        occupied={Cell(0, 0): "R1", Cell(0, 3): "R2"},
    )["R1"]

    assert not decision.accepted
    assert decision.safe_prefix_length == 2
    assert ledger.committed_actions("R1", 1) == ()
    assert decision.conflicts[0].occupied_by == "R2"


def test_route_end_shorter_than_horizon_is_a_complete_initial_window() -> None:
    plan = _plan((Cell(0, 0), Cell(0, 1), Cell(0, 2)))
    ledger = CommittedReservationLedger(horizon=3)

    decision = _initial(ledger, plan, occupied={Cell(0, 0): "R1"})["R1"]

    assert decision.accepted
    assert decision.requested_actions == (
        ActionRef("R1", 1, 0),
        ActionRef("R1", 1, 1),
    )


def test_undirected_edge_claim_rejects_opposite_traversal() -> None:
    first = _plan((Cell(0, 0), Cell(0, 1)), robot_id="R1")
    second = _plan((Cell(0, 1), Cell(0, 0)), robot_id="R2")
    ledger = CommittedReservationLedger(horizon=1)
    decisions = _initial(ledger, second, first, occupied={})
    assert decisions["R1"].accepted
    decision = decisions["R2"]

    assert not decision.accepted
    edge_conflict = next(
        conflict
        for conflict in decision.conflicts
        if isinstance(conflict.resource, EdgeResource)
    )
    assert edge_conflict.reserved_by == (ActionRef("R1", 1, 0),)
    assert ledger.committed_actions("R2", 1) == ()


def test_one_plan_can_hold_overlapping_claims_for_move_then_wait() -> None:
    plan = _plan((Cell(0, 0), Cell(0, 1), Cell(0, 1)))
    ledger = CommittedReservationLedger(horizon=2)

    assert _initial(ledger, plan, occupied={Cell(0, 0): "R1"})[
        "R1"
    ].accepted

    assert ledger.owners(VertexResource(Cell(0, 1))) == (
        ActionRef("R1", 1, 0),
        ActionRef("R1", 1, 1),
    )


def test_completion_releases_and_replenishes_one_rolling_frontier_action() -> None:
    plan = _plan(tuple(Cell(0, col) for col in range(5)))
    ledger = CommittedReservationLedger(horizon=2)
    _initial(ledger, plan, occupied={Cell(0, 0): "R1"})
    _complete(plan, 0)
    ledger.release_completed(plan, 0)

    decision = ledger.replenish_batch(
        (plan,),
        occupied={Cell(0, 1): "R1"},
    )["R1"]

    assert decision.accepted
    assert ledger.committed_actions("R1", 1) == (
        ActionRef("R1", 1, 1),
        ActionRef("R1", 1, 2),
    )
    assert ledger.owners(EdgeResource(Cell(0, 0), Cell(0, 1))) == ()

    _complete(plan, 1)
    ledger.release_completed(plan, 1)
    blocked = ledger.replenish_batch(
        (plan,),
        occupied={Cell(0, 2): "R1", Cell(0, 4): "R2"},
    )["R1"]
    assert not blocked.accepted
    assert ledger.committed_actions("R1", 1) == (ActionRef("R1", 1, 2),)

    _complete(plan, 2)
    ledger.release_completed(plan, 2)
    retried = ledger.replenish_batch(
        (plan,),
        occupied={Cell(0, 3): "R1"},
    )["R1"]
    assert retried.accepted
    assert retried.requested_actions == (ActionRef("R1", 1, 3),)
    assert ledger.committed_actions("R1", 1) == (ActionRef("R1", 1, 3),)


def test_planned_action_cannot_be_released_as_completed() -> None:
    plan = _plan(tuple(Cell(0, col) for col in range(4)))
    ledger = CommittedReservationLedger(horizon=2)
    _initial(ledger, plan, occupied={Cell(0, 0): "R1"})

    with pytest.raises(TrafficError, match="only completed"):
        ledger.release_completed(plan, 0)

    assert ledger.committed_actions("R1", 1) == (
        ActionRef("R1", 1, 0),
        ActionRef("R1", 1, 1),
    )


def test_completed_action_must_own_a_reservation_to_release() -> None:
    plan = _plan((Cell(0, 0), Cell(0, 1)))
    ledger = CommittedReservationLedger(horizon=1)
    _initial(ledger, plan, occupied={Cell(0, 0): "R1"})
    _complete(plan, 0)
    ledger.release_completed(plan, 0)

    with pytest.raises(TrafficError, match="does not own"):
        ledger.release_completed(plan, 0)


def test_replenishment_requires_initial_batch_admission() -> None:
    plan = _plan(tuple(Cell(0, col) for col in range(4)))
    ledger = CommittedReservationLedger(horizon=2)

    with pytest.raises(TrafficError, match="initial admission"):
        ledger.replenish_batch((plan,), occupied={Cell(0, 0): "R1"})


def test_running_action_cannot_be_released_with_unexecuted_plan() -> None:
    plan = _plan(tuple(Cell(0, col) for col in range(3)))
    ledger = CommittedReservationLedger(horizon=2)
    _initial(ledger, plan, occupied={Cell(0, 0): "R1"})
    plan.actions[0].transition_to(ActionStatus.RUNNING)

    with pytest.raises(TrafficError, match="only planned actions"):
        ledger.release_unexecuted_plan(plan)


def test_batch_arbitration_is_independent_of_request_order() -> None:
    first = _plan((Cell(0, 0), Cell(0, 1)), robot_id="R1")
    second = _plan((Cell(1, 1), Cell(0, 1)), robot_id="R2")

    def run(plans):
        ledger = CommittedReservationLedger(horizon=1)
        decisions = ledger.acquire_initial_batch(plans, occupied={})
        return decisions, ledger.all_committed_actions()

    forward = run((first, second))
    reverse = run((second, first))

    assert forward == reverse
    assert forward[0]["R1"].accepted
    assert not forward[0]["R2"].accepted
    assert forward[1] == (ActionRef("R1", 1, 0),)
