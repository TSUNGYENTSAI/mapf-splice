from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from mapf_splice.domain import (
    Action,
    ActionRef,
    ActionStatus,
    Cell,
    Plan,
    Resource,
    VertexResource,
)


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
            left.robot_id == right.robot_id
            and left.plan_version == right.plan_version
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
            action.status is ActionStatus.COMPLETED
            for action in plan.actions[prefix:]
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
