"""Cycle core vs affected scope: group derivation, stability, containment."""
import pytest

from mapf_splice.deadlock import (
    ConfirmationOutcome,
    ContainmentState,
    DeadlockController,
    ProspectiveDeadlockGroup,
)
from mapf_splice.domain import (
    ActionRef,
    Cell,
    DomainError,
    Robot,
    Task,
    TaskStatus,
    VertexResource,
)
from mapf_splice.planning import compile_path
from mapf_splice.preview import PreviewAnalysis, ProspectiveDependency
from mapf_splice.recovery import (
    RecoveryProposal,
    RecoverySolverMetadata,
    RecoveryState,
    ScopedMapfSolution,
)
from mapf_splice.traffic import CommittedReservationLedger
from mapf_splice.world import WorldState


def _dependency(waiting, blocking, *, resource=None):
    return ProspectiveDependency(
        waiting_robot_id=waiting,
        waiting_plan_version=1,
        preview_action_ref=ActionRef(waiting, 1, 1),
        blocking_robot_id=blocking,
        blocking_plan_version=1,
        resource=resource or VertexResource(Cell(0, 0)),
        blocking_action_refs=(ActionRef(blocking, 1, 0),),
        occupied_blocker=False,
    )


def _analysis(*edges, resource=None):
    return PreviewAnalysis(
        dependencies=tuple(_dependency(*e, resource=resource) for e in edges),
        contentions=(),
    )


# Core R1<->R2 with R3 waiting into it (upstream, not in the SCC).
BLOCKED = _analysis(("R1", "R2"), ("R2", "R1"), ("R3", "R1"))


def test_observation_separates_trigger_core_from_affected_scope() -> None:
    controller = DeadlockController(2)
    update = controller.observe(BLOCKED, {"R1": 1, "R2": 1, "R3": 1})
    group = update.observations[0].group
    assert isinstance(group, ProspectiveDeadlockGroup)
    assert group.trigger_core_identity == (("R1", 1), ("R2", 1))
    assert group.scope_identity == (("R1", 1), ("R2", 1), ("R3", 1))


def test_unchanged_core_and_scope_increment_count() -> None:
    controller = DeadlockController(3)
    versions = {"R1": 1, "R2": 1, "R3": 1}
    assert controller.observe(BLOCKED, versions).observations[0].count == 1
    assert controller.observe(BLOCKED, versions).observations[0].count == 2


def test_upstream_scope_member_version_change_resets_candidate() -> None:
    controller = DeadlockController(3)
    first = controller.observe(BLOCKED, {"R1": 1, "R2": 1, "R3": 1})
    assert first.observations[0].count == 1
    # Only R3 (an upstream scope member, not in the cycle core) changes version.
    second = controller.observe(BLOCKED, {"R1": 1, "R2": 1, "R3": 2})
    assert second.observations[0].count == 1
    assert first.observations[0].group in second.expired


def test_scope_membership_change_resets_candidate() -> None:
    controller = DeadlockController(3)
    versions = {"R1": 1, "R2": 1, "R3": 1}
    with_r3 = controller.observe(BLOCKED, versions)
    assert with_r3.observations[0].count == 1
    # R3 drops out of the graph: same core, smaller scope -> new candidate.
    without_r3 = controller.observe(
        _analysis(("R1", "R2"), ("R2", "R1")), {"R1": 1, "R2": 1}
    )
    assert without_r3.observations[0].count == 1
    assert with_r3.observations[0].group in without_r3.expired


def test_evidence_only_change_does_not_reset_count() -> None:
    controller = DeadlockController(3)
    versions = {"R1": 1, "R2": 1, "R3": 1}
    first = controller.observe(BLOCKED, versions)
    # Same robots and plan versions, different concrete resource evidence.
    other = _analysis(
        ("R1", "R2"), ("R2", "R1"), ("R3", "R1"), resource=VertexResource(Cell(9, 9))
    )
    second = controller.observe(other, versions)
    assert first.observations[0].count == 1
    assert second.observations[0].count == 2
    assert second.expired == ()


def _plan(robot_id: str, version: int):
    return compile_path(
        (Cell(0, 0),), robot_id=robot_id, plan_version=version, task_id="T"
    )


def _installed_world(routes: dict[str, tuple[Cell, ...]]) -> WorldState:
    robots = {
        r: Robot(r, route[0], active_task_id=f"T-{r}") for r, route in routes.items()
    }
    tasks = {
        f"T-{r}": Task(f"T-{r}", route[0], route[-1], 0, TaskStatus.ASSIGNED, r)
        for r, route in routes.items()
    }
    world = WorldState(
        reservations=CommittedReservationLedger(horizon=1),
        robots=robots,
        tasks=tasks,
    )
    for r in sorted(routes):
        world.install_plan(
            compile_path(routes[r], robot_id=r, plan_version=1, task_id=f"T-{r}")
        )
        tasks[f"T-{r}"].transition_to(TaskStatus.TO_PICKUP)
    return world


def _contained_over_blocked(world: WorldState) -> DeadlockController:
    controller = DeadlockController(1)
    controller.observe(BLOCKED, {"R1": 1, "R2": 1, "R3": 1})
    controller.refresh(world)
    return controller


# F: full-scope containment
def test_full_scope_containment_includes_upstream_waiter() -> None:
    controller = DeadlockController(1)
    controller.observe(BLOCKED, {"R1": 1, "R2": 1, "R3": 1})
    c = controller.containment
    assert c.trigger_core_identity == (("R1", 1), ("R2", 1))
    assert c.scope_identity == (("R1", 1), ("R2", 1), ("R3", 1))
    # R3 is only an upstream waiter, not in the cycle core, yet it is contained.
    for robot_id in ("R1", "R2", "R3"):
        assert controller.is_contained(_plan(robot_id, 1)) is True
    assert controller.is_contained(_plan("R4", 1)) is False
    # a stale-version scope member is not contained
    assert controller.is_contained(_plan("R3", 2)) is False


# G: full-scope quiescence
def test_quiescence_waits_for_every_scope_member() -> None:
    world = _installed_world(
        {
            "R1": (Cell(0, 0), Cell(0, 1)),
            "R2": (Cell(2, 0), Cell(2, 1)),
            "R3": (Cell(4, 0), Cell(4, 1)),
        }
    )
    # Only the upstream waiter R3 still holds committed motion authority.
    world.reservations.acquire_initial_batch(
        [world.plans["R3"]], occupied=world.occupied_cells()
    )
    controller = _contained_over_blocked(world)

    assert controller.newly_quiescent(world) == ()
    assert controller.containment.state is ContainmentState.DRAINING

    world.reservations.release_unexecuted_plan(world.plans["R3"])
    assert controller.newly_quiescent(world) == ((("R1", 1), ("R2", 1), ("R3", 1)),)
    assert controller.containment.state is ContainmentState.QUIESCENT


# H: confirmation scope larger than the confirmed cycle
def test_confirmation_scope_larger_than_confirmed_cycle() -> None:
    world = _installed_world(
        {
            "R1": (Cell(0, 0), Cell(0, 1)),
            "R2": (Cell(0, 1), Cell(0, 0)),
            "R3": (Cell(1, 0), Cell(0, 0)),
        }
    )
    controller = _contained_over_blocked(world)
    controller.newly_quiescent(world)

    result = controller.confirm(world, tick=18)
    assert result.outcome is ConfirmationOutcome.CONFIRMED_DEADLOCK
    assert result.trigger_core_identity == (("R1", 1), ("R2", 1))
    assert result.scope_identity == (("R1", 1), ("R2", 1), ("R3", 1))
    assert {member[0] for member in result.graph.scope} == {"R1", "R2", "R3"}
    assert result.graph.cyclic_sccs == (("R1", "R2"),)
    assert controller.containment.state is ContainmentState.CONFIRMED_DEADLOCK


def _confirmed_scope3_controller() -> DeadlockController:
    world = _installed_world(
        {
            "R1": (Cell(0, 0), Cell(0, 1)),
            "R2": (Cell(0, 1), Cell(0, 0)),
            "R3": (Cell(1, 0), Cell(0, 0)),
        }
    )
    controller = _contained_over_blocked(world)
    controller.newly_quiescent(world)
    result = controller.confirm(world, tick=18)
    assert result.outcome is ConfirmationOutcome.CONFIRMED_DEADLOCK
    return controller


def _recovery_proposal(scope: tuple[tuple[str, int], ...]) -> RecoveryProposal:
    robot_ids = tuple(robot_id for robot_id, _ in scope)
    solution = ScopedMapfSolution(
        robot_ids=robot_ids,
        paths={robot_id: (Cell(0, 0),) for robot_id in robot_ids},
        makespan=0,
    )
    return RecoveryProposal(
        scope_identity=scope,
        expected_plan_versions={robot_id: version for robot_id, version in scope},
        starts={robot_id: Cell(0, 0) for robot_id in robot_ids},
        goals={robot_id: Cell(0, 0) for robot_id in robot_ids},
        solution=solution,
        plans={},
        metadata=RecoverySolverMetadata(
            solver="pibt", seed=0, max_timestep=1, makespan=0, source_commit="x"
        ),
    )


# record_recovery must enforce that a proposal covers the active scope exactly.
def test_record_recovery_rejects_proposal_scoped_to_core_only() -> None:
    controller = _confirmed_scope3_controller()
    proposal = _recovery_proposal((("R1", 1), ("R2", 1)))  # missing upstream R3
    with pytest.raises(DomainError):
        controller.record_recovery(proposal)
    assert controller.containment.recovery_state is RecoveryState.NOT_ATTEMPTED
    assert controller.containment.recovery_proposal is None


def test_record_recovery_rejects_proposal_with_wrong_plan_versions() -> None:
    controller = _confirmed_scope3_controller()
    proposal = _recovery_proposal((("R1", 2), ("R2", 2), ("R3", 2)))  # wrong versions
    with pytest.raises(DomainError):
        controller.record_recovery(proposal)
    assert controller.containment.recovery_state is RecoveryState.NOT_ATTEMPTED
    assert controller.containment.recovery_proposal is None


def test_record_recovery_accepts_proposal_matching_full_scope() -> None:
    controller = _confirmed_scope3_controller()
    proposal = _recovery_proposal((("R1", 1), ("R2", 1), ("R3", 1)))
    controller.record_recovery(proposal)
    assert controller.containment.recovery_state is RecoveryState.PROPOSAL_READY
    assert controller.containment.recovery_proposal is proposal
