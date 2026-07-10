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
from mapf_splice.world import WorldState

PlanMember = tuple[str, int]
CandidateIdentity = tuple[PlanMember, ...]


class ContainmentState(StrEnum):
    DRAINING = "draining"
    QUIESCENT = "quiescent"
    CONFIRMED_DEADLOCK = "confirmed-deadlock"
    EXTERNAL_BLOCKED = "external-blocked"
    CLEARED = "cleared"
    INVALIDATED = "invalidated"


class ConfirmationOutcome(StrEnum):
    CONFIRMED_DEADLOCK = "confirmed-deadlock"
    EXTERNAL_DEPENDENCY = "external-dependency"
    CLEAR = "clear"


ACTIVE_STATES = frozenset(
    {
        ContainmentState.DRAINING,
        ContainmentState.QUIESCENT,
        ContainmentState.CONFIRMED_DEADLOCK,
        ContainmentState.EXTERNAL_BLOCKED,
    }
)

_OUTCOME_STATE = {
    ConfirmationOutcome.CONFIRMED_DEADLOCK: ContainmentState.CONFIRMED_DEADLOCK,
    ConfirmationOutcome.EXTERNAL_DEPENDENCY: ContainmentState.EXTERNAL_BLOCKED,
    ConfirmationOutcome.CLEAR: ContainmentState.CLEARED,
}


def classify_confirmation(graph: ConfirmedWaitForGraph) -> ConfirmationOutcome:
    """Interpret a facts-only confirmed graph into a control outcome (policy)."""
    if graph.cyclic_sccs:
        return ConfirmationOutcome.CONFIRMED_DEADLOCK
    if any(not edge.blocking_in_scope for edge in graph.edges):
        return ConfirmationOutcome.EXTERNAL_DEPENDENCY
    return ConfirmationOutcome.CLEAR


@dataclass(frozen=True, slots=True)
class SccObservation:
    identity: CandidateIdentity
    count: int
    evidence: tuple[ProspectiveDependency, ...]
    suppressed: bool = False


@dataclass(slots=True)
class Containment:
    identity: CandidateIdentity
    epoch: int
    state: ContainmentState = ContainmentState.DRAINING
    confirmation_tick: int | None = None
    outcome: ConfirmationOutcome | None = None
    confirmed_graph: ConfirmedWaitForGraph | None = None


@dataclass(frozen=True, slots=True)
class DeadlockUpdate:
    observations: tuple[SccObservation, ...]
    stable: tuple[CandidateIdentity, ...]
    expired: tuple[CandidateIdentity, ...]


@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    identity: CandidateIdentity
    epoch: int
    graph: ConfirmedWaitForGraph
    outcome: ConfirmationOutcome
    previous_state: ContainmentState
    state: ContainmentState


@dataclass(frozen=True, slots=True)
class DeadlockCandidateSnapshot:
    identity: CandidateIdentity
    observation_count: int
    stable: bool


@dataclass(frozen=True, slots=True)
class ContainmentSnapshot:
    identity: CandidateIdentity
    epoch: int
    state: ContainmentState
    confirmation_tick: int | None
    outcome: ConfirmationOutcome | None
    confirmed_graph: ConfirmedWaitForGraph | None


@dataclass(frozen=True, slots=True)
class DeadlockControllerSnapshot:
    threshold: int
    candidates: tuple[DeadlockCandidateSnapshot, ...]
    containments: tuple[ContainmentSnapshot, ...]


def cyclic_sccs(analysis: PreviewAnalysis) -> tuple[tuple[str, ...], ...]:
    return cyclic_components(
        (dependency.waiting_robot_id, dependency.blocking_robot_id)
        for dependency in analysis.dependencies
    )


@dataclass(slots=True)
class DeadlockController:
    stable_scc_observation_threshold: int = 2
    _counts: dict[CandidateIdentity, int] = field(default_factory=dict, init=False)
    _containments: dict[CandidateIdentity, Containment] = field(
        default_factory=dict,
        init=False,
    )
    _epoch_counter: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.stable_scc_observation_threshold < 1:
            raise ValueError("stable SCC observation threshold must be positive")

    @property
    def containments(self) -> tuple[Containment, ...]:
        return tuple(self._containments[key] for key in sorted(self._containments))

    def snapshot(self) -> DeadlockControllerSnapshot:
        """Serialize current state read-only; callers refresh() beforehand."""
        return DeadlockControllerSnapshot(
            threshold=self.stable_scc_observation_threshold,
            candidates=tuple(
                DeadlockCandidateSnapshot(
                    identity=identity,
                    observation_count=count,
                    stable=identity in self._containments,
                )
                for identity, count in sorted(self._counts.items())
            ),
            containments=tuple(
                ContainmentSnapshot(
                    identity=containment.identity,
                    epoch=containment.epoch,
                    state=containment.state,
                    confirmation_tick=containment.confirmation_tick,
                    outcome=containment.outcome,
                    confirmed_graph=containment.confirmed_graph,
                )
                for _, containment in sorted(self._containments.items())
            ),
        )

    def _active_members(
        self, exclude: CandidateIdentity | None = None
    ) -> set[PlanMember]:
        return {
            member
            for identity, containment in self._containments.items()
            if containment.state in ACTIVE_STATES and identity != exclude
            for member in identity
        }

    def observe(
        self,
        analysis: PreviewAnalysis,
        plan_versions: Mapping[str, int],
    ) -> DeadlockUpdate:
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
            if set(identity) & self._active_members(exclude=identity):
                # Suppressed by an overlapping active containment: no eligible
                # stability count accrues until the overlap disappears.
                self._counts.pop(identity, None)
                observations.append(
                    SccObservation(identity, 0, current[identity], suppressed=True)
                )
                continue
            count = self._counts.get(identity, 0) + 1
            self._counts[identity] = count
            observations.append(SccObservation(identity, count, current[identity]))
            if (
                count >= self.stable_scc_observation_threshold
                and identity not in self._containments
            ):
                self._epoch_counter += 1
                self._containments[identity] = Containment(
                    identity, self._epoch_counter
                )
                stable.append(identity)
        return DeadlockUpdate(
            tuple(observations),
            tuple(stable),
            expired,
        )

    def refresh(
        self, world: WorldState
    ) -> tuple[tuple[CandidateIdentity, int], ...]:
        """Invalidate active containments whose robots or plan versions changed.

        Mutates containment state, so the control phase must call this
        explicitly; read-only observers (snapshot) never trigger it. Returns the
        newly invalidated (identity, epoch) pairs for event emission.
        """
        invalidated: list[tuple[CandidateIdentity, int]] = []
        for identity in sorted(self._containments):
            containment = self._containments[identity]
            if containment.state not in ACTIVE_STATES:
                continue
            if any(
                robot_id not in world.robots
                or world.robots[robot_id].plan_version != version
                or robot_id not in world.plans
                or world.plans[robot_id].version != version
                for robot_id, version in containment.identity
            ):
                containment.state = ContainmentState.INVALIDATED
                invalidated.append((identity, containment.epoch))
        return tuple(invalidated)

    def is_contained(self, plan: Plan) -> bool:
        member = (plan.robot_id, plan.version)
        return any(
            containment.state in ACTIVE_STATES and member in containment.identity
            for containment in self._containments.values()
        )

    def newly_quiescent(self, world: WorldState) -> tuple[CandidateIdentity, ...]:
        results: list[CandidateIdentity] = []
        for identity in sorted(self._containments):
            containment = self._containments[identity]
            if containment.state is not ContainmentState.DRAINING:
                continue
            if self._is_quiescent(world, identity):
                containment.state = ContainmentState.QUIESCENT
                results.append(identity)
        return tuple(results)

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

    def confirm(
        self, world: WorldState, tick: int
    ) -> tuple[ConfirmationResult, ...]:
        """Confirm QUIESCENT containments and re-evaluate EXTERNAL_BLOCKED ones."""
        results: list[ConfirmationResult] = []
        for identity in sorted(self._containments):
            containment = self._containments[identity]
            if containment.state not in (
                ContainmentState.QUIESCENT,
                ContainmentState.EXTERNAL_BLOCKED,
            ):
                continue
            graph = build_confirmed_wait_for(
                world, containment.identity, epoch=containment.epoch, tick=tick
            )
            outcome = classify_confirmation(graph)
            previous = containment.state
            containment.state = _OUTCOME_STATE[outcome]
            containment.outcome = outcome
            containment.confirmation_tick = tick
            containment.confirmed_graph = graph
            results.append(
                ConfirmationResult(
                    identity,
                    containment.epoch,
                    graph,
                    outcome,
                    previous,
                    containment.state,
                )
            )
        return tuple(results)

    def prune_resolved(self) -> None:
        """Drop CLEARED/INVALIDATED containments and reset their stability counts."""
        for identity in list(self._containments):
            if self._containments[identity].state in (
                ContainmentState.CLEARED,
                ContainmentState.INVALIDATED,
            ):
                del self._containments[identity]
                self._counts.pop(identity, None)
