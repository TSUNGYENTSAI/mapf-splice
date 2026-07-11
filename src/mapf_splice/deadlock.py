from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from mapf_splice.confirm import (
    ConfirmedWaitForGraph,
    build_confirmed_wait_for,
    cyclic_components,
)
from mapf_splice.domain import ActionStatus, Plan
from mapf_splice.preview import PreviewAnalysis, ProspectiveDependency
from mapf_splice.recovery import (
    RecoveryPlanningFailure,
    RecoveryProposal,
    RecoveryState,
)
from mapf_splice.world import WorldState

PlanMember = tuple[str, int]
CandidateIdentity = tuple[PlanMember, ...]


class ContainmentState(StrEnum):
    DRAINING = "draining"
    QUIESCENT = "quiescent"
    CONFIRMED_DEADLOCK = "confirmed-deadlock"
    UNSUPPORTED = "unsupported"


class ConfirmationOutcome(StrEnum):
    CONFIRMED_DEADLOCK = "confirmed-deadlock"
    UNSUPPORTED_EXTERNAL = "unsupported-external"
    CLEAR = "clear"


def classify_confirmation(graph: ConfirmedWaitForGraph) -> ConfirmationOutcome:
    """Interpret a facts-only confirmed graph into a control outcome (policy).

    An internal cycle is a hard reservation deadlock. Otherwise, a blocker
    outside the containment scope is out of v0.1 scope (unsupported); a fully
    in-scope acyclic graph means a scoped robot can still make progress (clear).
    """
    if graph.cyclic_sccs:
        return ConfirmationOutcome.CONFIRMED_DEADLOCK
    if any(not edge.blocking_in_scope for edge in graph.edges):
        return ConfirmationOutcome.UNSUPPORTED_EXTERNAL
    return ConfirmationOutcome.CLEAR


@dataclass(frozen=True, slots=True)
class SccObservation:
    identity: CandidateIdentity
    count: int
    evidence: tuple[ProspectiveDependency, ...]


@dataclass(slots=True)
class Containment:
    identity: CandidateIdentity
    state: ContainmentState = ContainmentState.DRAINING
    confirmation_tick: int | None = None
    outcome: ConfirmationOutcome | None = None
    confirmed_graph: ConfirmedWaitForGraph | None = None
    recovery_state: RecoveryState = RecoveryState.NOT_ATTEMPTED
    recovery_proposal: RecoveryProposal | None = None
    recovery_failure: RecoveryPlanningFailure | None = None


@dataclass(frozen=True, slots=True)
class DeadlockUpdate:
    observations: tuple[SccObservation, ...]
    stable: tuple[CandidateIdentity, ...]
    expired: tuple[CandidateIdentity, ...]


@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    identity: CandidateIdentity
    graph: ConfirmedWaitForGraph
    outcome: ConfirmationOutcome


@dataclass(frozen=True, slots=True)
class DeadlockCandidateSnapshot:
    identity: CandidateIdentity
    observation_count: int
    stable: bool


@dataclass(frozen=True, slots=True)
class ContainmentSnapshot:
    identity: CandidateIdentity
    state: ContainmentState
    confirmation_tick: int | None
    outcome: ConfirmationOutcome | None
    confirmed_graph: ConfirmedWaitForGraph | None
    recovery_state: RecoveryState
    recovery_proposal: RecoveryProposal | None
    recovery_failure: RecoveryPlanningFailure | None


@dataclass(frozen=True, slots=True)
class DeadlockControllerSnapshot:
    threshold: int
    candidates: tuple[DeadlockCandidateSnapshot, ...]
    containment: ContainmentSnapshot | None


def cyclic_sccs(analysis: PreviewAnalysis) -> tuple[tuple[str, ...], ...]:
    return cyclic_components(
        (dependency.waiting_robot_id, dependency.blocking_robot_id)
        for dependency in analysis.dependencies
    )


@dataclass(slots=True)
class DeadlockController:
    """Single-incident reference controller.

    v0.1 supports at most one active containment incident globally. While an
    incident is active, no second incident forms and no candidate accrues an
    eligible stability count. Recovery-group expansion and multi-incident
    orchestration are deliberately out of scope.
    """

    stable_scc_observation_threshold: int = 2
    _counts: dict[CandidateIdentity, int] = field(default_factory=dict, init=False)
    _active: Containment | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.stable_scc_observation_threshold < 1:
            raise ValueError("stable SCC observation threshold must be positive")

    @property
    def containment(self) -> Containment | None:
        return self._active

    def snapshot(self) -> DeadlockControllerSnapshot:
        """Serialize current state read-only; callers refresh() beforehand."""
        active = self._active
        return DeadlockControllerSnapshot(
            threshold=self.stable_scc_observation_threshold,
            candidates=tuple(
                DeadlockCandidateSnapshot(
                    identity=identity,
                    observation_count=count,
                    stable=active is not None and identity == active.identity,
                )
                for identity, count in sorted(self._counts.items())
            ),
            containment=(
                None
                if active is None
                else ContainmentSnapshot(
                    identity=active.identity,
                    state=active.state,
                    confirmation_tick=active.confirmation_tick,
                    outcome=active.outcome,
                    confirmed_graph=active.confirmed_graph,
                    recovery_state=active.recovery_state,
                    recovery_proposal=active.recovery_proposal,
                    recovery_failure=active.recovery_failure,
                )
            ),
        )

    def observe(
        self,
        analysis: PreviewAnalysis,
        plan_versions: Mapping[str, int],
    ) -> DeadlockUpdate:
        # While an incident is active, candidate accumulation is frozen: no new
        # incident forms and no count changes. Prospective evidence still lives
        # in the preview graph itself.
        if self._active is not None:
            return DeadlockUpdate((), (), ())

        current: dict[CandidateIdentity, tuple[ProspectiveDependency, ...]] = {}
        for members in cyclic_sccs(analysis):
            identity = tuple(
                (robot_id, plan_versions[robot_id]) for robot_id in members
            )
            evidence = tuple(
                dependency
                for dependency in analysis.dependencies
                if dependency.waiting_robot_id in members
                and dependency.blocking_robot_id in members
            )
            current[identity] = evidence

        expired = tuple(sorted(set(self._counts) - set(current)))
        for identity in expired:
            del self._counts[identity]

        stable: list[CandidateIdentity] = []
        observations: list[SccObservation] = []
        for identity in sorted(current):
            count = self._counts.get(identity, 0) + 1
            self._counts[identity] = count
            observations.append(SccObservation(identity, count, current[identity]))
            if (
                self._active is None
                and count >= self.stable_scc_observation_threshold
            ):
                self._active = Containment(identity)
                stable.append(identity)
        return DeadlockUpdate(tuple(observations), tuple(stable), expired)

    def refresh(self, world: WorldState) -> CandidateIdentity | None:
        """Drop the active incident if its scoped robots or plan versions changed.

        Mutates controller state, so the control phase must call this explicitly;
        read-only observers (snapshot) never trigger it. Returns the invalidated
        identity for event emission, or None.
        """
        active = self._active
        if active is None:
            return None
        if any(
            robot_id not in world.robots
            or world.robots[robot_id].plan_version != version
            or robot_id not in world.plans
            or world.plans[robot_id].version != version
            for robot_id, version in active.identity
        ):
            self._release()
            return active.identity
        return None

    def is_contained(self, plan: Plan) -> bool:
        active = self._active
        return active is not None and (plan.robot_id, plan.version) in active.identity

    def newly_quiescent(self, world: WorldState) -> tuple[CandidateIdentity, ...]:
        active = self._active
        if active is None or active.state is not ContainmentState.DRAINING:
            return ()
        if self._is_quiescent(world, active.identity):
            active.state = ContainmentState.QUIESCENT
            return (active.identity,)
        return ()

    @staticmethod
    def _is_quiescent(world: WorldState, identity: CandidateIdentity) -> bool:
        for robot_id, version in identity:
            robot = world.robots[robot_id]
            plan = world.plans.get(robot_id)
            if (
                robot.plan_version != version
                or robot.active_action_ref is not None
                or world.reservations.committed_actions(robot_id, version)
                or plan is None
                or any(
                    action.status is ActionStatus.RUNNING for action in plan.actions
                )
            ):
                return False
        return True

    def confirm(self, world: WorldState, tick: int) -> ConfirmationResult | None:
        """Confirm a quiescent incident exactly once.

        An internal cycle holds the incident as a confirmed deadlock; an
        external blocker holds it as unsupported; an acyclic in-scope graph
        clears and releases it. No automatic re-evaluation.
        """
        active = self._active
        if active is None or active.state is not ContainmentState.QUIESCENT:
            return None
        graph = build_confirmed_wait_for(world, active.identity, tick=tick)
        outcome = classify_confirmation(graph)
        active.confirmation_tick = tick
        active.outcome = outcome
        active.confirmed_graph = graph
        result = ConfirmationResult(active.identity, graph, outcome)
        if outcome is ConfirmationOutcome.CONFIRMED_DEADLOCK:
            active.state = ContainmentState.CONFIRMED_DEADLOCK
        elif outcome is ConfirmationOutcome.UNSUPPORTED_EXTERNAL:
            active.state = ContainmentState.UNSUPPORTED
        else:  # CLEAR: false positive, release and resume admission next tick.
            self._release()
        return result

    def record_recovery(
        self,
        result: RecoveryProposal | RecoveryPlanningFailure,
    ) -> None:
        """Attach a recovery proposal or typed failure to the incident once.

        Only a confirmed-deadlock incident that has not yet attempted recovery
        records a result; further calls are ignored. This milestone attempts
        recovery once with no retry, cancellation, or multi-attempt history.
        """
        active = self._active
        if (
            active is None
            or active.state is not ContainmentState.CONFIRMED_DEADLOCK
            or active.recovery_state is not RecoveryState.NOT_ATTEMPTED
        ):
            return
        if isinstance(result, RecoveryProposal):
            active.recovery_proposal = result
            active.recovery_state = RecoveryState.PROPOSAL_READY
        else:
            active.recovery_failure = result
            active.recovery_state = RecoveryState.UNSUPPORTED_OR_FAILED

    def _release(self) -> None:
        self._active = None
        self._counts.clear()
