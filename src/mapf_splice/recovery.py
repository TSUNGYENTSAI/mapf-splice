from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from mapf_splice.adg import MapfSolutionError, compile_adg
from mapf_splice.confirm import ConfirmedWaitForGraph
from mapf_splice.domain import (
    Action,
    ActionKind,
    ActionRef,
    ActionStatus,
    Cell,
    DomainError,
    Plan,
)
from mapf_splice.scenario import WarehouseMap
from mapf_splice.tasking import current_phase_goal
from mapf_splice.world import WorldState

DEFAULT_RECOVERY_SEED = 0
DEFAULT_RECOVERY_MAX_TIMESTEP = 256
PYPIBT_SOURCE_COMMIT = "a3c97f60413c6619a29a5022969896bc54877edc"


class RecoveryValidationError(ValueError):
    """Raised when a synchronized recovery solution fails project validation.

    Kept independent of ADG compilation so a later solver swap cannot bypass
    these safety checks by producing output the ADG compiler happens to accept.
    """


class RecoveryFailureReason(StrEnum):
    UNSUPPORTED_SCOPE = "unsupported-scope"
    INVALID_TASK_PHASE = "invalid-task-phase"
    DUPLICATE_GOAL = "duplicate-goal"
    SOLVER_NO_SOLUTION = "solver-did-not-find-supported-solution"
    INVALID_SOLUTION = "invalid-solution"
    ADG_REJECTED = "adg-compilation-rejected"
    SOLVER_UNAVAILABLE = "solver-unavailable"


class RecoveryState(StrEnum):
    NOT_ATTEMPTED = "not-attempted"
    PROPOSAL_READY = "proposal-ready"
    UNSUPPORTED_OR_FAILED = "unsupported-or-failed"
    INSTALL_FAILED = "install-failed"
    INSTALLED = "installed"
    EXECUTING = "executing"
    ADMISSION_FAILED = "admission-failed"
    ADMISSION_STALLED = "admission-stalled"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class RecoveryIncidentRef:
    """Deterministic identity of the one confirmed incident a proposal owns."""

    trigger_core_identity: tuple[tuple[str, int], ...]
    scope_identity: tuple[tuple[str, int], ...]
    confirmation_tick: int

    def __post_init__(self) -> None:
        core = self.trigger_core_identity
        scope = self.scope_identity
        if len(core) < 2:
            raise DomainError("recovery trigger core needs at least two members")
        if tuple(sorted(set(core))) != core:
            raise DomainError("recovery trigger core must be sorted and unique")
        if not scope or tuple(sorted(set(scope))) != scope:
            raise DomainError("recovery scope must be nonempty, sorted and unique")
        if not set(core) <= set(scope):
            raise DomainError("recovery trigger core must be a subset of its scope")
        if self.confirmation_tick < 0:
            raise DomainError("recovery confirmation tick cannot be negative")


@dataclass(frozen=True, slots=True)
class ConfirmedRecoveryIncident:
    ref: RecoveryIncidentRef
    confirmed_graph: ConfirmedWaitForGraph

    def __post_init__(self) -> None:
        if self.confirmed_graph.scope != self.ref.scope_identity:
            raise DomainError("confirmed graph scope does not match recovery incident")
        if self.confirmed_graph.captured_at_tick != self.ref.confirmation_tick:
            raise DomainError("confirmed graph tick does not match recovery incident")
        scope_ids = {robot_id for robot_id, _ in self.ref.scope_identity}
        if not self.confirmed_graph.cyclic_sccs:
            raise DomainError("recovery incident requires an internal confirmed cycle")
        for scc in self.confirmed_graph.cyclic_sccs:
            if len(scc) < 2:
                raise DomainError("confirmed cyclic SCC needs at least two members")
            if tuple(sorted(set(scc))) != scc:
                raise DomainError("confirmed cyclic SCC must be sorted and unique")
            if not set(scc) <= scope_ids:
                raise DomainError("confirmed cyclic SCC must be internal to scope")


def _require_sorted_unique(robot_ids: tuple[str, ...]) -> None:
    if not robot_ids:
        raise DomainError("scoped MAPF problem needs at least one robot")
    if list(robot_ids) != sorted(set(robot_ids)):
        raise DomainError("robot ids must be sorted and unique")


@dataclass(frozen=True, slots=True)
class ScopedMapfProblem:
    """A solver-neutral scoped MAPF instance (no PyPIBT/NumPy types)."""

    robot_ids: tuple[str, ...]
    starts: dict[str, Cell]
    goals: dict[str, Cell]
    warehouse_map: WarehouseMap
    max_timestep: int
    seed: int

    def __post_init__(self) -> None:
        _require_sorted_unique(self.robot_ids)
        members = set(self.robot_ids)
        if set(self.starts) != members or set(self.goals) != members:
            raise DomainError("starts and goals must cover exactly the scope robots")
        if self.max_timestep < 1:
            raise DomainError("max_timestep must be positive")


@dataclass(frozen=True, slots=True)
class ScopedMapfSolution:
    """Validated synchronized per-robot paths (makespan = number of transitions)."""

    robot_ids: tuple[str, ...]
    paths: dict[str, tuple[Cell, ...]]
    makespan: int

    def __post_init__(self) -> None:
        _require_sorted_unique(self.robot_ids)
        if set(self.paths) != set(self.robot_ids):
            raise DomainError("solution paths must cover exactly the scope robots")
        if self.makespan < 0:
            raise DomainError("makespan cannot be negative")
        expected = self.makespan + 1
        if any(len(path) != expected for path in self.paths.values()):
            raise DomainError("solution paths must be synchronized to makespan + 1")
        object.__setattr__(
            self,
            "paths",
            MappingProxyType({k: tuple(v) for k, v in self.paths.items()}),
        )


@dataclass(frozen=True, slots=True)
class RecoverySolverMetadata:
    solver: str
    seed: int
    max_timestep: int
    makespan: int
    source_commit: str


@dataclass(frozen=True, slots=True)
class RecoveryActionSpec:
    ref: ActionRef
    kind: ActionKind
    start: Cell
    end: Cell
    duration_ticks: int
    dependencies: tuple[ActionRef, ...]

    @classmethod
    def from_action(cls, action: Action) -> RecoveryActionSpec:
        return cls(action.ref, action.kind, action.start, action.end,
                   action.duration_ticks, tuple(action.dependencies))

    def materialize(self) -> Action:
        return Action(self.ref, self.kind, self.start, self.end,
                      self.duration_ticks, self.dependencies, ActionStatus.PLANNED)


@dataclass(frozen=True, slots=True)
class RecoveryPlanSpec:
    robot_id: str
    version: int
    task_id: str
    phase_goal: Cell
    actions: tuple[RecoveryActionSpec, ...]

    @classmethod
    def from_plan(cls, plan: Plan) -> RecoveryPlanSpec:
        return cls(plan.robot_id, plan.version, plan.task_id, plan.phase_goal,
                   tuple(RecoveryActionSpec.from_action(a) for a in plan.actions))

    def materialize(self) -> Plan:
        return Plan(self.robot_id, self.version, self.task_id, self.phase_goal,
                    tuple(action.materialize() for action in self.actions))


@dataclass(frozen=True, slots=True)
class RecoveryProposal:
    """A validated, not-yet-installed scoped MAPF recovery for a confirmed deadlock.

    ``scope_identity`` is the full affected containment scope (the MAPF
    participant set), not merely the cyclic trigger core.
    """

    incident_ref: RecoveryIncidentRef
    expected_plan_versions: Mapping[str, int]
    starts: Mapping[str, Cell]
    goals: Mapping[str, Cell]
    solution: ScopedMapfSolution
    plans: Mapping[str, RecoveryPlanSpec | Plan]
    metadata: RecoverySolverMetadata

    @property
    def scope_identity(self) -> tuple[tuple[str, int], ...]:
        """Compatibility/readability alias; incident_ref remains authoritative."""
        return self.incident_ref.scope_identity

    def __post_init__(self) -> None:
        scope = self.incident_ref.scope_identity
        ids = tuple(robot_id for robot_id, _ in scope)
        members = set(ids)
        if tuple(sorted(ids)) != ids or len(members) != len(ids):
            raise DomainError("recovery incident scope must be sorted and unique")
        if not (
            set(self.expected_plan_versions)
            == set(self.starts)
            == set(self.goals)
            == set(self.plans)
            == set(self.solution.paths)
            == members
        ):
            raise DomainError(
                "recovery proposal participant coverage does not match incident scope"
            )
        if self.solution.robot_ids != ids:
            raise DomainError(
                "recovery proposal solution robot ids do not match incident scope"
            )
        expected = dict(self.expected_plan_versions)
        if expected != dict(scope):
            raise DomainError(
                "recovery proposal expected versions do not match incident scope"
            )
        specs = {robot_id: plan if isinstance(plan, RecoveryPlanSpec)
                 else RecoveryPlanSpec.from_plan(plan)
                 for robot_id, plan in self.plans.items()}
        for robot_id in ids:
            plan = specs[robot_id]
            if plan.robot_id != robot_id or plan.version != expected[robot_id] + 1:
                raise DomainError(
                    "recovery replacement plan version or robot does not match"
                )
            if not plan.task_id:
                raise DomainError("recovery replacement task id cannot be empty")
            if plan.phase_goal != self.goals[robot_id]:
                raise DomainError("recovery replacement phase goal does not match")
            path = self.solution.paths[robot_id]
            if path[0] != self.starts[robot_id] or path[-1] != self.goals[robot_id]:
                raise DomainError("recovery solution endpoints do not match proposal")
        task_ids = {robot_id: specs[robot_id].task_id for robot_id in ids}
        versions = {robot_id: specs[robot_id].version for robot_id in ids}
        try:
            canonical = compile_adg(
                self.solution.paths,
                plan_versions=versions,
                task_ids=task_ids,
            )
        except (DomainError, MapfSolutionError, ValueError) as error:
            raise DomainError(f"invalid recovery replacement specs: {error}") from error
        canonical_specs = {
            robot_id: RecoveryPlanSpec.from_plan(plan)
            for robot_id, plan in canonical.items()
        }
        if specs != canonical_specs:
            raise DomainError(
                "recovery replacement specs differ from synchronized solution"
            )
        object.__setattr__(self, "expected_plan_versions", MappingProxyType(expected))
        object.__setattr__(self, "starts", MappingProxyType(dict(self.starts)))
        object.__setattr__(self, "goals", MappingProxyType(dict(self.goals)))
        object.__setattr__(self, "plans", MappingProxyType(specs))


@dataclass(frozen=True, slots=True)
class RecoveryPlanningFailure:
    reason: RecoveryFailureReason
    detail: str


class RecoveryInstallFailureReason(StrEnum):
    NO_ACTIVE_INCIDENT = "no-active-recovery-incident"
    INCIDENT_MISMATCH = "incident-mismatch"
    TRIGGER_CORE_MISMATCH = "trigger-core-mismatch"
    AFFECTED_SCOPE_MISMATCH = "affected-scope-mismatch"
    STALE_CONFIRMATION = "stale-confirmation"
    PROPOSAL_NOT_READY = "proposal-not-ready"
    ALREADY_INSTALLED = "already-installed"
    PARTICIPANT_COVERAGE_MISMATCH = "participant-coverage-mismatch"
    STALE_PLAN_VERSION = "stale-plan-version"
    STALE_POSITION = "stale-position"
    TASK_OR_PHASE_CHANGED = "task-or-phase-changed"
    NOT_QUIESCENT = "not-quiescent"
    INVALID_REPLACEMENT_PLAN = "invalid-replacement-plan"
    INVALID_DEPENDENCY_GRAPH = "invalid-dependency-graph"
    RESERVATION_STATE_MISMATCH = "reservation-state-mismatch"


@dataclass(frozen=True, slots=True)
class RecoveryInstallFailure:
    reason: RecoveryInstallFailureReason
    detail: str


@dataclass(frozen=True, slots=True)
class RecoveryInstallSuccess:
    installed_versions: Mapping[str, int]


def _clone_plan(plan: Plan) -> Plan:
    return Plan(
        plan.robot_id,
        plan.version,
        plan.task_id,
        plan.phase_goal,
        tuple(
            Action(
                a.ref,
                a.kind,
                a.start,
                a.end,
                a.duration_ticks,
                tuple(a.dependencies),
                a.status,
            )
            for a in plan.actions
        ),
    )


def validate_synchronized_solution(
    solution: ScopedMapfSolution,
    *,
    problem: ScopedMapfProblem,
) -> None:
    """Validate a synchronized solution against its problem (solver-independent).

    Raises RecoveryValidationError on the first violation. Checks exact
    participant coverage and deterministic ordering, that starts equal the
    authoritative quiescent positions and goals equal the current task-phase
    goals, synchronized length, in-bounds traversable cells, adjacent-or-wait
    transitions, no vertex collision, and no opposite-edge swap.
    """
    if solution.robot_ids != problem.robot_ids:
        raise RecoveryValidationError(
            f"solution coverage {solution.robot_ids} != scope {problem.robot_ids}"
        )
    if set(solution.paths) != set(problem.robot_ids):
        raise RecoveryValidationError("solution path coverage does not match scope")

    lengths = {len(path) for path in solution.paths.values()}
    if len(lengths) != 1:
        raise RecoveryValidationError("solution paths are not synchronized in length")
    horizon = lengths.pop()
    if horizon != solution.makespan + 1:
        raise RecoveryValidationError("solution length does not match makespan")

    warehouse = problem.warehouse_map
    ids = problem.robot_ids
    for robot_id in ids:
        path = solution.paths[robot_id]
        if path[0] != problem.starts[robot_id]:
            raise RecoveryValidationError(
                f"{robot_id} path start {path[0]} != authoritative position"
            )
        if path[-1] != problem.goals[robot_id]:
            raise RecoveryValidationError(
                f"{robot_id} path goal {path[-1]} != current task-phase goal"
            )
        for cell in path:
            if not warehouse.is_traversable(cell):
                raise RecoveryValidationError(
                    f"{robot_id} path visits non-traversable cell {cell}"
                )
        for start, end in zip(path, path[1:], strict=False):
            if start.manhattan_distance(end) not in (0, 1):
                raise RecoveryValidationError(
                    f"{robot_id} path has a non-adjacent transition {start}->{end}"
                )

    for time_index in range(horizon):
        positions = [solution.paths[robot_id][time_index] for robot_id in ids]
        if len(set(positions)) != len(positions):
            raise RecoveryValidationError(f"vertex collision at timestep {time_index}")

    for time_index in range(horizon - 1):
        for offset, first_id in enumerate(ids):
            for second_id in ids[offset + 1 :]:
                if (
                    solution.paths[first_id][time_index]
                    == solution.paths[second_id][time_index + 1]
                    and solution.paths[first_id][time_index + 1]
                    == solution.paths[second_id][time_index]
                ):
                    raise RecoveryValidationError(
                        f"opposite-edge swap at transition {time_index}"
                    )


def build_recovery_proposal(
    world: WorldState,
    incident: ConfirmedRecoveryIncident,
    warehouse_map: WarehouseMap,
    *,
    seed: int = DEFAULT_RECOVERY_SEED,
    max_timestep: int = DEFAULT_RECOVERY_MAX_TIMESTEP,
) -> RecoveryProposal | RecoveryPlanningFailure:
    """Produce and validate a scoped MAPF recovery proposal (read-only).

    ``scope_identity`` is the full affected containment scope; the MAPF
    participant set equals it. This function does not rediscover or expand the
    scope. It never mutates world state, installs plans, or changes plan
    versions or reservations, and returns a validated, not-yet-installed
    RecoveryProposal or a typed RecoveryPlanningFailure. The PyPIBT adapter is
    imported lazily here to keep the module import cycle-free and NumPy-free.
    """
    from mapf_splice.mapf_pibt import solve

    scope_identity = incident.ref.scope_identity
    scope_ids = tuple(sorted(robot_id for robot_id, _ in scope_identity))
    expected_versions = {robot_id: version for robot_id, version in scope_identity}

    # The confirmed incident owns the frozen participant set. Validate those
    # members only; unrelated active robots are intentionally absent from MAPF.
    for robot_id, version in scope_identity:
        robot = world.robots.get(robot_id)
        plan = world.plans.get(robot_id)
        if (
            robot is None
            or robot.plan_version != version
            or plan is None
            or plan.version != version
            or robot.active_task_id is None
            or plan.task_id != robot.active_task_id
        ):
            return RecoveryPlanningFailure(
                RecoveryFailureReason.UNSUPPORTED_SCOPE,
                f"{robot_id} is not a current planned scope member at v{version}",
            )
        if (
            robot.active_action_ref is not None
            or robot.remaining_ticks != 0
            or any(action.status is ActionStatus.RUNNING for action in plan.actions)
            or world.reservations.committed_actions(robot_id, version)
        ):
            return RecoveryPlanningFailure(
                RecoveryFailureReason.UNSUPPORTED_SCOPE,
                f"{robot_id} is not quiescent at v{version}",
            )

    starts = {robot_id: world.robots[robot_id].position for robot_id in scope_ids}
    goals: dict[str, Cell] = {}
    for robot_id in scope_ids:
        try:
            goals[robot_id] = current_phase_goal(world, robot_id)
        except DomainError as error:
            return RecoveryPlanningFailure(
                RecoveryFailureReason.INVALID_TASK_PHASE,
                f"{robot_id}: {error}",
            )
    if len(set(goals.values())) != len(goals):
        return RecoveryPlanningFailure(
            RecoveryFailureReason.DUPLICATE_GOAL,
            "participants share a current task-phase goal",
        )

    problem = ScopedMapfProblem(
        robot_ids=scope_ids,
        starts=starts,
        goals=goals,
        warehouse_map=warehouse_map,
        max_timestep=max_timestep,
        seed=seed,
    )
    solution = solve(problem)
    if isinstance(solution, RecoveryPlanningFailure):
        return solution

    try:
        validate_synchronized_solution(solution, problem=problem)
    except RecoveryValidationError as error:
        return RecoveryPlanningFailure(
            RecoveryFailureReason.INVALID_SOLUTION, str(error)
        )

    task_ids = {
        robot_id: world.robots[robot_id].active_task_id for robot_id in scope_ids
    }
    new_versions = {robot_id: expected_versions[robot_id] + 1 for robot_id in scope_ids}
    try:
        plans = compile_adg(
            solution.paths,
            plan_versions=new_versions,
            task_ids=task_ids,
        )
    except MapfSolutionError as error:
        return RecoveryPlanningFailure(RecoveryFailureReason.ADG_REJECTED, str(error))

    return RecoveryProposal(
        incident_ref=incident.ref,
        expected_plan_versions=expected_versions,
        starts=starts,
        goals=goals,
        solution=solution,
        plans=plans,
        metadata=RecoverySolverMetadata(
            solver="pibt",
            seed=seed,
            max_timestep=max_timestep,
            makespan=solution.makespan,
            source_commit=PYPIBT_SOURCE_COMMIT,
        ),
    )


def _install_failure(
    reason: RecoveryInstallFailureReason, detail: str
) -> RecoveryInstallFailure:
    return RecoveryInstallFailure(reason, detail)


def commit_recovery_splice(
    world: WorldState, controller, proposal: RecoveryProposal, *, tick: int
) -> RecoveryInstallSuccess | RecoveryInstallFailure:
    """Validate a staged replacement group then publish it atomically.

    This deliberately owns no solver behavior. Every mutable Action is cloned
    before validation and WorldState publishes the complete group only once.
    """
    active = controller.containment
    if active is None:
        return _install_failure(
            RecoveryInstallFailureReason.NO_ACTIVE_INCIDENT, "no active containment"
        )
    if active.recovery_state is not RecoveryState.PROPOSAL_READY:
        return _install_failure(
            RecoveryInstallFailureReason.PROPOSAL_NOT_READY, active.recovery_state.value
        )
    if proposal is not active.recovery_proposal:
        return _install_failure(
            RecoveryInstallFailureReason.INCIDENT_MISMATCH,
            "proposal is not the proposal recorded by the active incident",
        )
    if active.confirmation_tick is None:
        return _install_failure(
            RecoveryInstallFailureReason.INCIDENT_MISMATCH,
            "active incident was not confirmed",
        )
    expected_ref = RecoveryIncidentRef(
        active.trigger_core_identity, active.scope_identity, active.confirmation_tick
    )
    if (
        proposal.incident_ref.trigger_core_identity
        != expected_ref.trigger_core_identity
    ):
        return _install_failure(
            RecoveryInstallFailureReason.TRIGGER_CORE_MISMATCH,
            "proposal trigger core does not match active incident",
        )
    if proposal.incident_ref.scope_identity != expected_ref.scope_identity:
        return _install_failure(
            RecoveryInstallFailureReason.AFFECTED_SCOPE_MISMATCH,
            "proposal scope does not match active incident",
        )
    if proposal.incident_ref.confirmation_tick != expected_ref.confirmation_tick:
        return _install_failure(
            RecoveryInstallFailureReason.STALE_CONFIRMATION,
            "proposal confirmation tick does not match active incident",
        )
    if proposal.incident_ref != expected_ref:
        return _install_failure(
            RecoveryInstallFailureReason.INCIDENT_MISMATCH,
            "proposal incident does not match active incident",
        )
    ids = tuple(robot_id for robot_id, _ in active.scope_identity)
    if not (
        set(ids)
        == set(proposal.expected_plan_versions)
        == set(proposal.starts)
        == set(proposal.goals)
        == set(proposal.plans)
        == set(proposal.solution.paths)
        and proposal.solution.robot_ids == ids
    ):
        return _install_failure(
            RecoveryInstallFailureReason.PARTICIPANT_COVERAGE_MISMATCH,
            "proposal members differ from active scope",
        )
    try:
        staged = {
            robot_id: proposal.plans[robot_id].materialize() for robot_id in ids
        }
    except (AttributeError, DomainError, TypeError, ValueError, IndexError) as error:
        return _install_failure(
            RecoveryInstallFailureReason.INVALID_REPLACEMENT_PLAN, str(error)
        )
    all_actions = {
        action.ref: action for plan in staged.values() for action in plan.actions
    }
    try:
        for robot_id, version in active.scope_identity:
            robot = world.robots.get(robot_id)
            plan = world.plans.get(robot_id)
            if (
                robot is None
                or plan is None
                or robot.plan_version != version
                or plan.version != version
            ):
                return _install_failure(
                    RecoveryInstallFailureReason.STALE_PLAN_VERSION, robot_id
                )
            if proposal.expected_plan_versions[robot_id] != version:
                return _install_failure(
                    RecoveryInstallFailureReason.STALE_PLAN_VERSION, robot_id
                )
            if robot.position != proposal.starts[robot_id]:
                return _install_failure(
                    RecoveryInstallFailureReason.STALE_POSITION, robot_id
                )
            if (
                robot.active_action_ref is not None
                or robot.remaining_ticks != 0
                or any(a.status is ActionStatus.RUNNING for a in plan.actions)
            ):
                return _install_failure(
                    RecoveryInstallFailureReason.NOT_QUIESCENT, robot_id
                )
            if world.reservations.committed_actions(robot_id, version):
                return _install_failure(
                    RecoveryInstallFailureReason.RESERVATION_STATE_MISMATCH, robot_id
                )
            if (
                robot.active_task_id is None
                or plan.task_id != robot.active_task_id
                or staged[robot_id].task_id != robot.active_task_id
                or current_phase_goal(world, robot_id) != proposal.goals[robot_id]
            ):
                return _install_failure(
                    RecoveryInstallFailureReason.TASK_OR_PHASE_CHANGED, robot_id
                )
            new = staged[robot_id]
            if (
                new.version != version + 1
                or new.phase_goal != proposal.goals[robot_id]
                or (new.actions and new.actions[0].start != robot.position)
                or any(a.status is not ActionStatus.PLANNED for a in new.actions)
            ):
                return _install_failure(
                    RecoveryInstallFailureReason.INVALID_REPLACEMENT_PLAN, robot_id
                )
        for action in all_actions.values():
            if any(dep not in all_actions for dep in action.dependencies):
                return _install_failure(
                    RecoveryInstallFailureReason.INVALID_DEPENDENCY_GRAPH,
                    str(action.ref),
                )
        # Plan constructors validate refs, chains and own-plan dependencies;
        # cross-plan DFS detects cycles before the aggregate is touched.
        visiting: set[ActionRef] = set()
        visited: set[ActionRef] = set()

        def visit(ref: ActionRef) -> None:
            if ref in visiting:
                raise DomainError("dependency cycle")
            if ref not in visited:
                visiting.add(ref)
                for dep in all_actions[ref].dependencies:
                    visit(dep)
                visiting.remove(ref)
                visited.add(ref)

        for ref in sorted(all_actions):
            visit(ref)
    except DomainError as error:
        return _install_failure(
            RecoveryInstallFailureReason.INVALID_DEPENDENCY_GRAPH, str(error)
        )
    try:
        world.replace_plan_group(staged)
    except (DomainError, ValueError) as error:
        return _install_failure(
            RecoveryInstallFailureReason.INVALID_REPLACEMENT_PLAN, str(error)
        )
    controller.record_install_success(
        {robot_id: staged[robot_id].version for robot_id in ids}, tick=tick
    )
    return RecoveryInstallSuccess(
        MappingProxyType({robot_id: staged[robot_id].version for robot_id in ids})
    )
