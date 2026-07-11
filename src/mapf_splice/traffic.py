from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from mapf_splice.domain import (
    Action,
    ActionRef,
    ActionStatus,
    Cell,
    Plan,
    Resource,
    VertexResource,
)

if TYPE_CHECKING:
    from mapf_splice.recovery import RecoveryIncidentRef


class TrafficError(ValueError):
    """Raised when committed reservation state is used inconsistently."""


@dataclass(frozen=True, slots=True)
class ReservationConflict:
    requested_by: ActionRef
    resource: Resource
    reserved_by: tuple[ActionRef, ...] = ()
    occupied_by: str | None = None


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    accepted: bool
    requested_actions: tuple[ActionRef, ...]
    safe_prefix_length: int
    conflicts: tuple[ReservationConflict, ...] = ()


class AdmissionProfile(StrEnum):
    NORMAL_K_CRUISE = "normal-k-cruise"
    RECOVERY_ADG_BOUNDED_PREFIX = "recovery-adg-bounded-prefix"


class RecoveryBlockedReason(StrEnum):
    UNMET_CROSS_ROBOT_DEPENDENCY = "unmet-cross-robot-dependency"
    OCCUPIED_RESOURCE = "occupied-resource"
    COMMITTED_RESOURCE_CONFLICT = "committed-resource-conflict"
    STAGED_RESOURCE_CONFLICT = "staged-resource-conflict"
    NO_CAPACITY = "no-capacity"
    PLAN_COMPLETE = "plan-complete"


class RecoveryAdmissionFailureReason(StrEnum):
    NO_ACTIVE_RECOVERY = "no-active-recovery"
    RECOVERY_GENERATION_MISMATCH = "recovery-generation-mismatch"
    PARTICIPANT_COVERAGE_MISMATCH = "participant-coverage-mismatch"
    STALE_PLAN_VERSION = "stale-plan-version"
    TASK_OR_PHASE_CHANGED = "task-or-phase-changed"
    INVALID_CURRENT_FRONTIER = "invalid-current-frontier"
    INVALID_RECOVERY_PLAN = "invalid-recovery-plan"
    RESERVATION_STATE_MISMATCH = "reservation-state-mismatch"
    ATOMIC_PUBLICATION_FAILED = "atomic-publication-failed"


@dataclass(frozen=True, slots=True)
class RecoveryAdmissionFailure:
    reason: RecoveryAdmissionFailureReason
    detail: str
    tick: int


class RecoveryAdmissionError(TrafficError):
    def __init__(self, reason: RecoveryAdmissionFailureReason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class RecoveryAdmissionRequest:
    incident_ref: RecoveryIncidentRef
    installed_versions: tuple[tuple[str, int], ...]
    participants: tuple[str, ...]
    horizon: int
    tick: int


@dataclass(frozen=True, slots=True)
class RecoveryRobotAdmissionResult:
    robot_id: str
    plan_version: int
    completed_prefix_length: int
    existing_committed_prefix: tuple[ActionRef, ...]
    remaining_capacity: int
    candidate_frontier_index: int | None
    evaluated_actions: tuple[ActionRef, ...]
    granted_actions: tuple[ActionRef, ...]
    first_blocked_action: ActionRef | None
    blocked_reason: RecoveryBlockedReason | None
    resulting_committed_prefix_length: int


@dataclass(frozen=True, slots=True)
class RecoveryAdmissionResult:
    profile: AdmissionProfile
    request: RecoveryAdmissionRequest
    robots: tuple[RecoveryRobotAdmissionResult, ...]
    evaluation_order: tuple[ActionRef, ...]
    staged_grants: tuple[ActionRef, ...]
    published: bool

    @property
    def any_new_authority(self) -> bool:
        return bool(self.staged_grants)


@dataclass(slots=True)
class CommittedReservationLedger:
    horizon: int
    _owners_by_resource: dict[Resource, set[ActionRef]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _resources_by_action: dict[ActionRef, set[Resource]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _initialized_plans: set[tuple[str, int]] = field(
        default_factory=set,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.horizon < 1:
            raise TrafficError("committed horizon must be positive")

    @staticmethod
    def _same_plan(left: ActionRef, right: ActionRef) -> bool:
        return (
            left.robot_id == right.robot_id and left.plan_version == right.plan_version
        )

    def _conflicts_for(
        self,
        action: Action,
        occupied: Mapping[Cell, str],
        owners_by_resource: Mapping[Resource, set[ActionRef]],
    ) -> tuple[ReservationConflict, ...]:
        conflicts: list[ReservationConflict] = []
        for resource in action.claims:
            reserved_by = tuple(
                sorted(
                    owner
                    for owner in owners_by_resource.get(resource, set())
                    if not self._same_plan(owner, action.ref)
                )
            )
            occupied_by = None
            if isinstance(resource, VertexResource):
                owner_id = occupied.get(resource.cell)
                if owner_id is not None and owner_id != action.ref.robot_id:
                    occupied_by = owner_id
            if reserved_by or occupied_by is not None:
                conflicts.append(
                    ReservationConflict(
                        requested_by=action.ref,
                        resource=resource,
                        reserved_by=reserved_by,
                        occupied_by=occupied_by,
                    )
                )
        return tuple(conflicts)

    def conflicts_for(
        self,
        action: Action,
        *,
        occupied: Mapping[Cell, str],
    ) -> tuple[ReservationConflict, ...]:
        """Report an action's conflicts against current committed state (read-only)."""
        return self._conflicts_for(action, occupied, self._owners_by_resource)

    def _evaluate(
        self,
        actions: Sequence[Action],
        occupied: Mapping[Cell, str],
        owners_by_resource: Mapping[Resource, set[ActionRef]],
    ) -> AdmissionDecision:
        conflicts: list[ReservationConflict] = []
        safe_prefix_length = 0
        prefix_clear = True
        for action in actions:
            if action.status is not ActionStatus.PLANNED:
                raise TrafficError("only planned actions can acquire reservations")
            action_conflicts = self._conflicts_for(
                action,
                occupied,
                owners_by_resource,
            )
            if action_conflicts:
                prefix_clear = False
                conflicts.extend(action_conflicts)
            elif prefix_clear:
                safe_prefix_length += 1
        return AdmissionDecision(
            accepted=not conflicts,
            requested_actions=tuple(action.ref for action in actions),
            safe_prefix_length=safe_prefix_length,
            conflicts=tuple(conflicts),
        )

    def _commit(self, actions: Sequence[Action]) -> None:
        for action in actions:
            action_resources = self._resources_by_action.setdefault(action.ref, set())
            for resource in action.claims:
                self._owners_by_resource.setdefault(resource, set()).add(action.ref)
                action_resources.add(resource)

    def _publish_recovery_grants(
        self,
        actions: Sequence[Action],
        staged_owners: dict[Resource, set[ActionRef]],
    ) -> None:
        """Publish one fully staged recovery phase as an aggregate swap."""
        staged_resources = {
            ref: set(resources) for ref, resources in self._resources_by_action.items()
        }
        for action in actions:
            staged_resources.setdefault(action.ref, set()).update(action.claims)
        self._owners_by_resource = staged_owners
        self._resources_by_action = staged_resources

    @staticmethod
    def _stage(
        actions: Sequence[Action],
        owners_by_resource: dict[Resource, set[ActionRef]],
    ) -> None:
        for action in actions:
            for resource in action.claims:
                owners_by_resource.setdefault(resource, set()).add(action.ref)

    def acquire_initial_batch(
        self,
        plans: Sequence[Plan],
        *,
        occupied: Mapping[Cell, str],
    ) -> dict[str, AdmissionDecision]:
        by_robot: dict[str, Plan] = {}
        for plan in plans:
            if plan.robot_id in by_robot:
                raise TrafficError("initial batch contains duplicate robot requests")
            key = (plan.robot_id, plan.version)
            if key in self._initialized_plans or self.committed_actions(*key):
                raise TrafficError("plan already performed initial admission")
            by_robot[plan.robot_id] = plan

        staged_owners = {
            resource: set(owners)
            for resource, owners in self._owners_by_resource.items()
        }
        decisions: dict[str, AdmissionDecision] = {}
        accepted: list[tuple[Plan, Sequence[Action]]] = []
        for robot_id in sorted(by_robot):
            plan = by_robot[robot_id]
            actions = plan.actions[: self.horizon]
            decision = self._evaluate(actions, occupied, staged_owners)
            decisions[robot_id] = decision
            if decision.accepted:
                self._stage(actions, staged_owners)
                accepted.append((plan, actions))

        for plan, actions in accepted:
            self._commit(actions)
            self._initialized_plans.add((plan.robot_id, plan.version))
        return decisions

    @staticmethod
    def _completed_prefix(plan: Plan) -> int:
        prefix = 0
        while (
            prefix < len(plan.actions)
            and plan.actions[prefix].status is ActionStatus.COMPLETED
        ):
            prefix += 1
        if any(
            action.status is ActionStatus.COMPLETED for action in plan.actions[prefix:]
        ):
            raise TrafficError("completed actions must form a sequential prefix")
        return prefix

    def release_completed(self, plan: Plan, completed_action_index: int) -> None:
        key = (plan.robot_id, plan.version)
        if key not in self._initialized_plans:
            raise TrafficError("plan has not completed initial admission")
        if not 0 <= completed_action_index < len(plan.actions):
            raise TrafficError("completed action index is outside the plan")
        action = plan.actions[completed_action_index]
        if action.status is not ActionStatus.COMPLETED:
            raise TrafficError("only completed actions may release reservations")
        committed = self.committed_actions(*key)
        if action.ref not in committed:
            raise TrafficError("completed action does not own a reservation")
        if self._completed_prefix(plan) != completed_action_index + 1:
            raise TrafficError("completed action is not the execution frontier")
        if any(ref.action_index < completed_action_index for ref in committed):
            raise TrafficError("an earlier completed action still owns reservations")
        self._release_action_unchecked(action.ref)

    def _frontier_action(self, plan: Plan) -> Action | None:
        key = (plan.robot_id, plan.version)
        if key not in self._initialized_plans:
            raise TrafficError("plan has not completed initial admission")
        prefix = self._completed_prefix(plan)
        committed = self.committed_actions(*key)
        indices = tuple(ref.action_index for ref in committed)
        if any(index < prefix for index in indices):
            raise TrafficError("completed action still owns reservations")
        if indices and indices != tuple(range(prefix, prefix + len(indices))):
            raise TrafficError("committed actions do not form a contiguous frontier")
        if len(committed) >= self.horizon:
            return None
        frontier_index = prefix + len(committed)
        if frontier_index >= len(plan.actions):
            return None
        action = plan.actions[frontier_index]
        if action.status is not ActionStatus.PLANNED:
            raise TrafficError("frontier action must be planned")
        return action

    def replenish_batch(
        self,
        plans: Sequence[Plan],
        *,
        occupied: Mapping[Cell, str],
    ) -> dict[str, AdmissionDecision]:
        by_robot: dict[str, Plan] = {}
        for plan in plans:
            if plan.robot_id in by_robot:
                raise TrafficError("replenishment batch contains duplicate robots")
            by_robot[plan.robot_id] = plan

        staged_owners = {
            resource: set(owners)
            for resource, owners in self._owners_by_resource.items()
        }
        decisions: dict[str, AdmissionDecision] = {}
        accepted: list[Action] = []
        for robot_id in sorted(by_robot):
            action = self._frontier_action(by_robot[robot_id])
            if action is None:
                decisions[robot_id] = AdmissionDecision(True, (), 0)
                continue
            decision = self._evaluate((action,), occupied, staged_owners)
            decisions[robot_id] = decision
            if decision.accepted:
                self._stage((action,), staged_owners)
                accepted.append(action)
        self._commit(accepted)
        return decisions

    def admit_batch(
        self,
        plans: Sequence[Plan],
        *,
        occupied: Mapping[Cell, str],
    ) -> dict[str, AdmissionDecision]:
        """Admit initial windows and rolling frontiers in one arbitration batch."""
        by_robot: dict[str, Plan] = {}
        requests: dict[str, tuple[Action, ...]] = {}
        initialize: set[str] = set()
        for plan in plans:
            if plan.robot_id in by_robot:
                raise TrafficError("admission batch contains duplicate robots")
            by_robot[plan.robot_id] = plan
            key = (plan.robot_id, plan.version)
            if key in self._initialized_plans:
                frontier = self._frontier_action(plan)
                requests[plan.robot_id] = () if frontier is None else (frontier,)
            else:
                if self.committed_actions(*key):
                    raise TrafficError("uninitialized plan already owns reservations")
                requests[plan.robot_id] = plan.actions[: self.horizon]
                initialize.add(plan.robot_id)

        staged_owners = {
            resource: set(owners)
            for resource, owners in self._owners_by_resource.items()
        }
        decisions: dict[str, AdmissionDecision] = {}
        accepted: list[Action] = []
        for robot_id in sorted(by_robot):
            actions = requests[robot_id]
            decision = self._evaluate(actions, occupied, staged_owners)
            decisions[robot_id] = decision
            if decision.accepted:
                self._stage(actions, staged_owners)
                accepted.extend(actions)
        self._commit(accepted)
        for robot_id in initialize:
            if decisions[robot_id].accepted:
                plan = by_robot[robot_id]
                self._initialized_plans.add((robot_id, plan.version))
        return decisions

    def admit_normal_batch(
        self,
        plans: Sequence[Plan],
        *,
        occupied: Mapping[Cell, str],
    ) -> dict[str, AdmissionDecision]:
        """Explicit NORMAL_K_CRUISE entry point; semantics match admit_batch."""
        return self.admit_batch(plans, occupied=occupied)

    def admit_recovery_group(
        self,
        request: RecoveryAdmissionRequest,
        plans: Sequence[Plan],
        *,
        occupied: Mapping[Cell, str],
    ) -> RecoveryAdmissionResult:
        """Atomically grant deterministic ADG-aware bounded prefixes."""
        if request.horizon != self.horizon or request.horizon < 1:
            raise RecoveryAdmissionError(
                RecoveryAdmissionFailureReason.RECOVERY_GENERATION_MISMATCH,
                "recovery request horizon does not match ledger",
            )
        by_robot = {plan.robot_id: plan for plan in plans}
        incident_participants = tuple(
            robot_id for robot_id, _ in request.incident_ref.scope_identity
        )
        if incident_participants != request.participants:
            raise RecoveryAdmissionError(
                RecoveryAdmissionFailureReason.RECOVERY_GENERATION_MISMATCH,
                "recovery request incident scope does not match participants",
            )
        if tuple(sorted(by_robot)) != request.participants:
            raise RecoveryAdmissionError(
                RecoveryAdmissionFailureReason.PARTICIPANT_COVERAGE_MISMATCH,
                "recovery participant coverage mismatch",
            )
        versions = dict(request.installed_versions)
        if set(versions) != set(by_robot):
            raise RecoveryAdmissionError(
                RecoveryAdmissionFailureReason.PARTICIPANT_COVERAGE_MISMATCH,
                "recovery installed-version coverage mismatch",
            )
        expected_installed = {
            robot_id: old_version + 1
            for robot_id, old_version in request.incident_ref.scope_identity
        }
        if versions != expected_installed:
            raise RecoveryAdmissionError(
                RecoveryAdmissionFailureReason.RECOVERY_GENERATION_MISMATCH,
                "installed versions do not match incident generations",
            )

        staged_owners = {
            resource: set(owners)
            for resource, owners in self._owners_by_resource.items()
        }
        all_actions = {
            action.ref: action for plan in plans for action in plan.actions
        }
        state: dict[str, dict[str, object]] = {}
        for robot_id in request.participants:
            plan = by_robot[robot_id]
            if plan.version != versions[robot_id]:
                raise RecoveryAdmissionError(
                    RecoveryAdmissionFailureReason.STALE_PLAN_VERSION,
                    f"stale recovery plan version for {robot_id}",
                )
            completed = self._completed_prefix(plan)
            committed = self.committed_actions(robot_id, plan.version)
            indices = tuple(ref.action_index for ref in committed)
            if any(index < completed for index in indices) or (
                indices
                and indices != tuple(range(completed, completed + len(indices)))
            ):
                raise RecoveryAdmissionError(
                    RecoveryAdmissionFailureReason.INVALID_CURRENT_FRONTIER,
                    f"invalid recovery committed frontier for {robot_id}",
                )
            if len(committed) > self.horizon:
                raise RecoveryAdmissionError(
                    RecoveryAdmissionFailureReason.INVALID_CURRENT_FRONTIER,
                    f"recovery committed prefix exceeds horizon for {robot_id}",
                )
            state[robot_id] = {
                "completed": completed,
                "existing": committed,
                "capacity": self.horizon - len(committed),
                "evaluated": [],
                "granted": [],
                "blocked": False,
                "blocked_action": None,
                "blocked_reason": None,
            }

        evaluation_order: list[ActionRef] = []
        staged: list[Action] = []
        staged_refs: set[ActionRef] = set()
        for _layer in range(self.horizon):
            for robot_id in request.participants:
                data = state[robot_id]
                if data["blocked"] or int(data["capacity"]) <= len(data["granted"]):
                    continue
                plan = by_robot[robot_id]
                index = (
                    int(data["completed"])
                    + len(data["existing"])
                    + len(data["granted"])
                )
                if index >= len(plan.actions):
                    data["blocked"] = True
                    data["blocked_reason"] = RecoveryBlockedReason.PLAN_COMPLETE
                    continue
                action = plan.actions[index]
                evaluation_order.append(action.ref)
                data["evaluated"].append(action.ref)
                unmet_cross = next(
                    (
                        dependency
                        for dependency in action.dependencies
                        if dependency.robot_id != robot_id
                        and (
                            dependency not in all_actions
                            or all_actions[dependency].status
                            is not ActionStatus.COMPLETED
                        )
                    ),
                    None,
                )
                if unmet_cross is not None:
                    data["blocked"] = True
                    data["blocked_action"] = action.ref
                    data["blocked_reason"] = (
                        RecoveryBlockedReason.UNMET_CROSS_ROBOT_DEPENDENCY
                    )
                    continue
                for dependency in action.dependencies:
                    if dependency.robot_id == robot_id and not (
                        all_actions[dependency].status is ActionStatus.COMPLETED
                        or dependency in data["existing"]
                        or dependency in staged_refs
                    ):
                        raise RecoveryAdmissionError(
                            RecoveryAdmissionFailureReason.INVALID_RECOVERY_PLAN,
                            "invalid same-robot recovery dependency",
                        )
                conflicts = self._conflicts_for(action, occupied, staged_owners)
                if conflicts:
                    data["blocked"] = True
                    data["blocked_action"] = action.ref
                    live_conflict = any(
                        conflict.reserved_by
                        and any(
                            owner in self._resources_by_action
                            for owner in conflict.reserved_by
                        )
                        for conflict in conflicts
                    )
                    if any(c.occupied_by is not None for c in conflicts):
                        reason = RecoveryBlockedReason.OCCUPIED_RESOURCE
                    elif live_conflict:
                        reason = RecoveryBlockedReason.COMMITTED_RESOURCE_CONFLICT
                    else:
                        reason = RecoveryBlockedReason.STAGED_RESOURCE_CONFLICT
                    data["blocked_reason"] = reason
                    continue
                self._stage((action,), staged_owners)
                staged.append(action)
                staged_refs.add(action.ref)
                data["granted"].append(action.ref)

        self._publish_recovery_grants(staged, staged_owners)
        for robot_id in request.participants:
            if state[robot_id]["granted"]:
                self._initialized_plans.add((robot_id, versions[robot_id]))
        robot_results = []
        for robot_id in request.participants:
            data = state[robot_id]
            completed = int(data["completed"])
            existing = tuple(data["existing"])
            granted = tuple(data["granted"])
            frontier = completed + len(existing)
            robot_results.append(
                RecoveryRobotAdmissionResult(
                    robot_id,
                    versions[robot_id],
                    completed,
                    existing,
                    int(data["capacity"]),
                    frontier if frontier < len(by_robot[robot_id].actions) else None,
                    tuple(data["evaluated"]),
                    granted,
                    data["blocked_action"],
                    data["blocked_reason"],
                    len(existing) + len(granted),
                )
            )
        return RecoveryAdmissionResult(
            AdmissionProfile.RECOVERY_ADG_BOUNDED_PREFIX,
            request,
            tuple(robot_results),
            tuple(evaluation_order),
            tuple(action.ref for action in staged),
            True,
        )

    def _release_action_unchecked(self, action_ref: ActionRef) -> None:
        resources = self._resources_by_action.pop(action_ref, set())
        for resource in resources:
            owners = self._owners_by_resource[resource]
            owners.discard(action_ref)
            if not owners:
                del self._owners_by_resource[resource]

    def release_unexecuted_plan(self, plan: Plan) -> None:
        key = (plan.robot_id, plan.version)
        refs = self.committed_actions(*key)
        for ref in refs:
            if plan.actions[ref.action_index].status is not ActionStatus.PLANNED:
                raise TrafficError(
                    "only planned actions may be released during plan replacement"
                )
        for ref in refs:
            self._release_action_unchecked(ref)
        self._initialized_plans.discard(key)

    def retire_completed_plan(self, plan: Plan) -> None:
        if any(action.status is not ActionStatus.COMPLETED for action in plan.actions):
            raise TrafficError("only a completed plan can be retired")
        key = (plan.robot_id, plan.version)
        if self.committed_actions(*key):
            raise TrafficError("completed plan still owns reservations")
        self._initialized_plans.discard(key)

    def plan_initialized(self, plan: Plan) -> bool:
        return (plan.robot_id, plan.version) in self._initialized_plans

    def committed_actions(
        self,
        robot_id: str,
        plan_version: int,
    ) -> tuple[ActionRef, ...]:
        return tuple(
            sorted(
                ref
                for ref in self._resources_by_action
                if ref.robot_id == robot_id and ref.plan_version == plan_version
            )
        )

    def owners(self, resource: Resource) -> tuple[ActionRef, ...]:
        return tuple(sorted(self._owners_by_resource.get(resource, set())))

    def all_committed_actions(self) -> tuple[ActionRef, ...]:
        return tuple(sorted(self._resources_by_action))

    def reservation_snapshot(
        self,
    ) -> tuple[tuple[Resource, tuple[ActionRef, ...]], ...]:
        """Return immutable, deterministically ordered committed ownership."""
        return tuple(
            (resource, tuple(sorted(owners)))
            for resource, owners in sorted(
                self._owners_by_resource.items(), key=lambda item: repr(item[0])
            )
        )
