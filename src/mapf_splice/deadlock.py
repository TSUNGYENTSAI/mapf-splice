from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

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
    graph: dict[str, set[str]] = {}
    for dependency in analysis.dependencies:
        graph.setdefault(dependency.waiting_robot_id, set()).add(
            dependency.blocking_robot_id
        )
        graph.setdefault(dependency.blocking_robot_id, set())

    index = 0
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def connect(node: str) -> None:
        nonlocal index
        indexes[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in sorted(graph[node]):
            if neighbor not in indexes:
                connect(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[neighbor])
        if lowlinks[node] == indexes[node]:
            members: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                members.append(member)
                if member == node:
                    break
            component = tuple(sorted(members))
            if len(component) >= 2:
                components.append(component)

    for node in sorted(graph):
        if node not in indexes:
            connect(node)
    return tuple(sorted(components))


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

    def snapshot(self, world: WorldState) -> DeadlockControllerSnapshot:
        """Expose controller state without leaking its mutable dictionaries."""
        self._refresh_containments(world)
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

    def _refresh_containments(self, world: WorldState) -> None:
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

    def is_contained(self, plan: Plan, world: WorldState) -> bool:
        self._refresh_containments(world)
        member = (plan.robot_id, plan.version)
        return any(
            containment.valid and member in containment.identity
            for containment in self._containments.values()
        )

    def newly_quiescent(self, world: WorldState) -> tuple[CandidateIdentity, ...]:
        self._refresh_containments(world)
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
