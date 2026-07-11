"""Cycle core vs affected scope: group derivation, stability, containment."""
from mapf_splice.deadlock import DeadlockController, ProspectiveDeadlockGroup
from mapf_splice.domain import ActionRef, Cell, VertexResource
from mapf_splice.preview import PreviewAnalysis, ProspectiveDependency


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
