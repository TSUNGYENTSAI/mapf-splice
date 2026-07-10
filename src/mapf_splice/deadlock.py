from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from mapf_splice.confirm import cyclic_components
from mapf_splice.domain import ActionStatus, Plan
from mapf_splice.preview import PreviewAnalysis, ProspectiveDependency
from mapf_splice.world import WorldState

PlanMember = tuple[str, int]
CandidateIdentity = tuple[PlanMember, ...]


@dataclass(frozen=True, slots=True)
class SccObservation:
    identity: CandidateIdentity
    count: int
    evidence: tuple[ProspectiveDependency, ...]


@dataclass(slots=True)
class Containment:
    identity: CandidateIdentity
    quiescence_emitted: bool = False
    valid: bool = True


@dataclass(frozen=True, slots=True)
class DeadlockUpdate:
    observations: tuple[SccObservation, ...]
    stable: tuple[CandidateIdentity, ...]
    expired: tuple[CandidateIdentity, ...]


@dataclass(frozen=True, slots=True)
class DeadlockCandidateSnapshot:
    identity: CandidateIdentity
    observation_count: int
    stable: bool


@dataclass(frozen=True, slots=True)
class ContainmentSnapshot:
    identity: CandidateIdentity
    valid: bool
    quiescence_emitted: bool


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
                    identity=identity,
                    valid=containment.valid,
                    quiescence_emitted=containment.quiescence_emitted,
                )
                for identity, containment in sorted(self._containments.items())
            ),
        )

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
            count = self._counts.get(identity, 0) + 1
            self._counts[identity] = count
            observations.append(SccObservation(identity, count, current[identity]))
            if (
                count >= self.stable_scc_observation_threshold
                and identity not in self._containments
            ):
                self._containments[identity] = Containment(identity)
                stable.append(identity)
        return DeadlockUpdate(
            tuple(observations),
            tuple(stable),
            expired,
        )

    def refresh(self, world: WorldState) -> None:
        """Invalidate containments whose scoped robots or plan versions changed.

        Mutates containment validity, so the control phase must call this
        explicitly; read-only observers (snapshot) never trigger it.
        """
        for containment in self._containments.values():
            if not containment.valid:
                continue
            if any(
                robot_id not in world.robots
                or world.robots[robot_id].plan_version != version
                or robot_id not in world.plans
                or world.plans[robot_id].version != version
                for robot_id, version in containment.identity
            ):
                containment.valid = False

    def is_contained(self, plan: Plan) -> bool:
        member = (plan.robot_id, plan.version)
        return any(
            containment.valid and member in containment.identity
            for containment in self._containments.values()
        )

    def newly_quiescent(self, world: WorldState) -> tuple[CandidateIdentity, ...]:
        results: list[CandidateIdentity] = []
        for identity, containment in sorted(self._containments.items()):
            if not containment.valid or containment.quiescence_emitted:
                continue
            quiescent = True
            for robot_id, version in identity:
                robot = world.robots[robot_id]
                plan = world.plans.get(robot_id)
                if (
                    robot.plan_version != version
                    or robot.active_action_ref is not None
                    or world.reservations.committed_actions(robot_id, version)
                    or plan is None
                    or any(
                        action.status is ActionStatus.RUNNING
                        for action in plan.actions
                    )
                ):
                    quiescent = False
                    break
            if quiescent:
                containment.quiescence_emitted = True
                results.append(identity)
        return tuple(results)
