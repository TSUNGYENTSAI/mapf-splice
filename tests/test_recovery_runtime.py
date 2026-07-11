"""Runtime wiring: a confirmed deadlock produces a recovery proposal once."""
from pathlib import Path

import pytest

pytest.importorskip("numpy")

from mapf_splice.deadlock import ContainmentState  # noqa: E402
from mapf_splice.recovery import (  # noqa: E402
    RecoveryFailureReason,
    RecoveryPlanningFailure,
    RecoveryProposal,
    RecoveryState,
)
from mapf_splice.scenario import load_scenario  # noqa: E402
from mapf_splice.simulation import DeterministicSimulator  # noqa: E402
from mapf_splice.trace import EventKind  # noqa: E402

ROOT = Path(__file__).parents[1]


def _run_to_recovery(horizon: int) -> DeterministicSimulator:
    scenario = load_scenario(ROOT / "scenarios/compact-three-robot/scenario.json")
    sim = DeterministicSimulator.from_scenario(scenario, committed_horizon=horizon)
    for _ in range(60):
        sim.tick()
        if any(
            event.kind is EventKind.RECOVERY_PROPOSAL_READY
            for event in sim.trace.events
        ):
            return sim
    pytest.fail(f"K={horizon} never produced a recovery proposal")


@pytest.mark.parametrize("horizon", [3, 4, 5])
def test_confirmed_deadlock_produces_recovery_proposal(horizon: int) -> None:
    sim = _run_to_recovery(horizon)
    containment = sim.deadlock_controller.containment
    assert containment.state is ContainmentState.CONFIRMED_DEADLOCK
    assert containment.recovery_state is RecoveryState.PROPOSAL_READY
    assert containment.recovery_failure is None
    proposal = containment.recovery_proposal
    assert isinstance(proposal, RecoveryProposal)
    assert set(proposal.plans) == {"R1", "R2", "R3"}
    for robot_id in ("R1", "R2", "R3"):
        assert proposal.plans[robot_id].phase_goal == proposal.goals[robot_id]


@pytest.mark.parametrize("horizon", [3, 4, 5])
def test_recovery_is_attempted_exactly_once(horizon: int) -> None:
    sim = _run_to_recovery(horizon)
    for _ in range(10):
        sim.tick()
    ready = [
        event
        for event in sim.trace.events
        if event.kind is EventKind.RECOVERY_PROPOSAL_READY
    ]
    assert len(ready) == 1


def test_record_recovery_does_not_overwrite_existing_result() -> None:
    sim = _run_to_recovery(3)
    controller = sim.deadlock_controller
    original = controller.containment.recovery_proposal
    controller.record_recovery(
        RecoveryPlanningFailure(RecoveryFailureReason.SOLVER_NO_SOLUTION, "ignored")
    )
    assert controller.containment.recovery_proposal is original
    assert controller.containment.recovery_state is RecoveryState.PROPOSAL_READY
