from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from mapf_splice.deadlock import (
    CandidateIdentity,
    ConfirmationOutcome,
    DeadlockController,
    DeadlockUpdate,
)
from mapf_splice.delay import DeterministicDelaySchedule
from mapf_splice.dispatch import dispatch_pending_tasks
from mapf_splice.domain import Action, ActionStatus, Cell, TaskStatus
from mapf_splice.planning import next_required_action
from mapf_splice.preview import PreviewAnalysis, analyze_preview, resource_label
from mapf_splice.recovery import (
    DEFAULT_RECOVERY_MAX_TIMESTEP,
    ConfirmedRecoveryIncident,
    RecoveryIncidentRef,
    RecoveryInstallFailure,
    RecoveryProposal,
    RecoveryState,
    build_recovery_proposal,
    commit_recovery_splice,
)
from mapf_splice.replay import FrameRecorder
from mapf_splice.routing import NoPath
from mapf_splice.scenario import ScenarioBundle, WarehouseMap, build_initial_world
from mapf_splice.tasking import (
    complete_dropoff,
    complete_pickup,
    start_dropoff_leg,
    start_pickup_leg,
)
from mapf_splice.trace import EventKind, EventTrace, TickPhase
from mapf_splice.traffic import (
    RecoveryAdmissionError,
    RecoveryAdmissionFailure,
    RecoveryAdmissionFailureReason,
    RecoveryAdmissionRequest,
    TrafficError,
)
from mapf_splice.workload import SeededTaskStream
from mapf_splice.world import WorldState, WorldStateError


@dataclass(slots=True)
class DeterministicSimulator:
    world: WorldState
    is_traversable: Callable[[Cell], bool]
    delay_schedule: DeterministicDelaySchedule
    base_action_duration_ticks: int = 1
    trace: EventTrace = field(default_factory=EventTrace)
    deadlock_controller: DeadlockController = field(default_factory=DeadlockController)
    recorder: FrameRecorder | None = None
    warehouse_map: WarehouseMap | None = None
    task_stream: SeededTaskStream | None = None
    recovery_max_timestep: int = DEFAULT_RECOVERY_MAX_TIMESTEP

    def __post_init__(self) -> None:
        if self.base_action_duration_ticks < 1:
            raise ValueError("base action duration must be positive")

    @classmethod
    def from_scenario(
        cls,
        scenario: ScenarioBundle,
        *,
        committed_horizon: int | None = None,
    ) -> DeterministicSimulator:
        execution = scenario.data["execution"]
        delay = execution["delay_schedule"]
        return cls(
            world=build_initial_world(
                scenario,
                committed_horizon=committed_horizon,
            ),
            is_traversable=scenario.warehouse_map.is_traversable,
            delay_schedule=DeterministicDelaySchedule(
                seed=delay["seed"],
                probability=delay["probability"],
                minimum_extra_ticks=delay["extra_wait_ticks"]["minimum"],
                maximum_extra_ticks=delay["extra_wait_ticks"]["maximum"],
            ),
            base_action_duration_ticks=execution["base_move_duration_ticks"],
            deadlock_controller=DeadlockController(
                scenario.data["deadlock_analysis"]["stable_scc_observation_threshold"]
            ),
            warehouse_map=scenario.warehouse_map,
        )

    def _running_actions(self) -> tuple[tuple[str, Action], ...]:
        self.world.validate()
        return tuple(
            (robot_id, self.world.action(robot.active_action_ref))
            for robot_id, robot in sorted(self.world.robots.items())
            if robot.active_action_ref is not None
        )

    def _complete_due_actions(self) -> tuple[tuple[str, Action], ...]:
        running = self._running_actions()
        due = tuple(
            (robot_id, action)
            for robot_id, action in running
            if self.world.robots[robot_id].remaining_ticks == 1
        )
        occupied = self.world.occupied_cells()
        targets: dict[Cell, str] = {}
        for robot_id, action in due:
            occupant = occupied.get(action.end)
            if occupant is not None and occupant != robot_id:
                raise WorldStateError("due completion target is still occupied")
            other = targets.get(action.end)
            if other is not None and other != robot_id:
                raise WorldStateError("due completions share a target vertex")
            targets[action.end] = robot_id

        due_ids = {robot_id for robot_id, _ in due}
        for robot_id, action in running:
            robot = self.world.robots[robot_id]
            if robot_id in due_ids:
                action.transition_to(ActionStatus.COMPLETED)
                robot.position = action.end
                robot.active_action_ref = None
                robot.remaining_ticks = 0
                self.trace.append(
                    tick=self.world.tick,
                    phase=TickPhase.APPLY_COMPLETIONS,
                    kind=EventKind.ACTION_COMPLETED,
                    robot_id=robot_id,
                    task_id=self.world.plans[robot_id].task_id,
                    action_ref=action.ref,
                )
            else:
                robot.remaining_ticks -= 1
        self.world.validate()
        return due

    def _release_completed(self, due: tuple[tuple[str, Action], ...]) -> None:
        for robot_id, action in due:
            plan = self.world.plans[robot_id]
            self.world.reservations.release_completed(plan, action.ref.action_index)
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.RELEASE_RESERVATIONS,
                kind=EventKind.RESERVATION_RELEASED,
                robot_id=robot_id,
                task_id=plan.task_id,
                action_ref=action.ref,
            )

    @staticmethod
    def _plan_complete(world: WorldState, robot_id: str) -> bool:
        plan = world.plans[robot_id]
        return (
            world.robots[robot_id].active_action_ref is None
            and not world.reservations.committed_actions(robot_id, plan.version)
            and all(action.status is ActionStatus.COMPLETED for action in plan.actions)
        )

    def _record_plan(self, task_id: str, old_status: TaskStatus, plan) -> None:
        self.trace.append(
            tick=self.world.tick,
            phase=TickPhase.ADVANCE_TASKS,
            kind=EventKind.PLAN_INSTALLED,
            robot_id=plan.robot_id,
            task_id=task_id,
            details=(("plan_version", plan.version),),
        )
        self.trace.append(
            tick=self.world.tick,
            phase=TickPhase.ADVANCE_TASKS,
            kind=EventKind.TASK_STATUS_CHANGED,
            robot_id=plan.robot_id,
            task_id=task_id,
            details=(
                ("from", old_status.value),
                ("to", self.world.tasks[task_id].status.value),
            ),
        )

    def _advance_tasks(self) -> None:
        assignments = dispatch_pending_tasks(
            self.world,
            is_traversable=self.is_traversable,
        )
        for assignment in assignments:
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.ADVANCE_TASKS,
                kind=EventKind.TASK_ASSIGNED,
                robot_id=assignment.robot_id,
                task_id=assignment.task_id,
                details=(("pickup_distance", assignment.pickup_distance),),
            )

        for task_id in sorted(self.world.tasks):
            task = self.world.tasks[task_id]
            if task.status is not TaskStatus.ASSIGNED:
                continue
            plan = start_pickup_leg(
                self.world,
                task_id,
                is_traversable=self.is_traversable,
                base_duration_ticks=self.base_action_duration_ticks,
            )
            if isinstance(plan, NoPath):
                self.trace.append(
                    tick=self.world.tick,
                    phase=TickPhase.ADVANCE_TASKS,
                    kind=EventKind.PLANNING_FAILED,
                    robot_id=task.assigned_robot_id,
                    task_id=task_id,
                    details=(("leg", "pickup"),),
                )
            else:
                self._record_plan(task_id, TaskStatus.ASSIGNED, plan)

        failed_dropoff_plans: set[str] = set()
        progress = True
        while progress:
            progress = False
            for task_id in sorted(self.world.tasks):
                task = self.world.tasks[task_id]
                robot_id = task.assigned_robot_id
                if robot_id is None:
                    continue
                robot = self.world.robots[robot_id]
                if (
                    task.status is TaskStatus.TO_PICKUP
                    and robot.position == task.pickup
                    and self._plan_complete(self.world, robot_id)
                    and not self.deadlock_controller.is_executing_recovery_plan(
                        self.world.plans[robot_id]
                    )
                ):
                    plan = self.world.plans[robot_id]
                    self.world.reservations.retire_completed_plan(plan)
                    complete_pickup(self.world, task_id)
                    self.trace.append(
                        tick=self.world.tick,
                        phase=TickPhase.ADVANCE_TASKS,
                        kind=EventKind.TASK_STATUS_CHANGED,
                        robot_id=robot_id,
                        task_id=task_id,
                        details=(("from", "to-pickup"), ("to", "carrying")),
                    )
                    progress = True
                if (
                    task.status is TaskStatus.CARRYING
                    and task_id not in failed_dropoff_plans
                ):
                    plan = start_dropoff_leg(
                        self.world,
                        task_id,
                        is_traversable=self.is_traversable,
                        base_duration_ticks=self.base_action_duration_ticks,
                    )
                    if isinstance(plan, NoPath):
                        failed_dropoff_plans.add(task_id)
                        self.trace.append(
                            tick=self.world.tick,
                            phase=TickPhase.ADVANCE_TASKS,
                            kind=EventKind.PLANNING_FAILED,
                            robot_id=robot_id,
                            task_id=task_id,
                            details=(("leg", "dropoff"),),
                        )
                    else:
                        self._record_plan(task_id, TaskStatus.CARRYING, plan)
                        progress = True
                if (
                    task.status is TaskStatus.TO_DROPOFF
                    and robot.position == task.dropoff
                    and self._plan_complete(self.world, robot_id)
                    and not self.deadlock_controller.is_executing_recovery_plan(
                        self.world.plans[robot_id]
                    )
                ):
                    plan = self.world.plans[robot_id]
                    self.world.reservations.retire_completed_plan(plan)
                    complete_dropoff(self.world, task_id)
                    self.trace.append(
                        tick=self.world.tick,
                        phase=TickPhase.ADVANCE_TASKS,
                        kind=EventKind.TASK_STATUS_CHANGED,
                        robot_id=robot_id,
                        task_id=task_id,
                        details=(("from", "to-drop-off"), ("to", "completed")),
                    )
                    progress = True

    def _admit(self) -> None:
        plans_list = []
        for _, plan in sorted(self.world.plans.items()):
            if all(action.status is ActionStatus.COMPLETED for action in plan.actions):
                continue
            if self.deadlock_controller.is_contained(plan):
                if not self.world.reservations.plan_initialized(plan):
                    raise WorldStateError("contained plan was not initially admitted")
                continue
            if self.deadlock_controller.is_executing_recovery_plan(plan):
                continue
            plans_list.append(plan)
        plans = tuple(plans_list)
        modes = {
            plan.robot_id: (
                "replenish"
                if self.world.reservations.plan_initialized(plan)
                else "initial"
            )
            for plan in plans
        }
        decisions = self.world.reservations.admit_normal_batch(
            plans,
            occupied=self.world.occupied_cells(),
        )
        for robot_id in sorted(decisions):
            decision = decisions[robot_id]
            if not decision.requested_actions and decision.accepted:
                continue
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.APPLY_ADMISSION,
                kind=(
                    EventKind.ADMISSION_ACCEPTED
                    if decision.accepted
                    else EventKind.ADMISSION_REJECTED
                ),
                robot_id=robot_id,
                task_id=self.world.plans[robot_id].task_id,
                details=(
                    ("mode", modes[robot_id]),
                    ("safe_prefix_length", decision.safe_prefix_length),
                ),
            )
        self._admit_recovery()

    def _admit_recovery(self) -> None:
        containment = self.deadlock_controller.containment
        if (
            containment is None
            or containment.recovery_state
            not in {RecoveryState.INSTALLED, RecoveryState.EXECUTING}
            or containment.installed_plan_versions is None
        ):
            return
        participants = tuple(robot_id for robot_id, _ in containment.scope_identity)
        request = RecoveryAdmissionRequest(
            containment.recovery_proposal.incident_ref,
            tuple(sorted(containment.installed_plan_versions.items())),
            participants,
            self.world.reservations.horizon,
            self.world.tick,
        )
        proposal = containment.recovery_proposal
        failure = None
        try:
            plans = tuple(self.world.plans[robot_id] for robot_id in participants)
        except KeyError as error:
            plans = ()
            failure = RecoveryAdmissionFailure(
                RecoveryAdmissionFailureReason.PARTICIPANT_COVERAGE_MISMATCH,
                f"missing recovery participant {error.args[0]}",
                self.world.tick,
            )
        if failure is None:
            for plan in plans:
                robot = self.world.robots.get(plan.robot_id)
                version = containment.installed_plan_versions[plan.robot_id]
                spec = proposal.plans[plan.robot_id]
                if (
                    robot is None
                    or robot.plan_version != version
                    or plan.version != version
                ):
                    failure = RecoveryAdmissionFailure(
                        RecoveryAdmissionFailureReason.STALE_PLAN_VERSION,
                        f"stale recovery generation for {plan.robot_id}",
                        self.world.tick,
                    )
                    break
                if plan.task_id != spec.task_id or plan.phase_goal != spec.phase_goal:
                    failure = RecoveryAdmissionFailure(
                        RecoveryAdmissionFailureReason.TASK_OR_PHASE_CHANGED,
                        f"recovery task or phase changed for {plan.robot_id}",
                        self.world.tick,
                    )
                    break
        if failure is not None:
            self.deadlock_controller.fail_recovery_admission(failure)
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.APPLY_ADMISSION,
                kind=EventKind.RECOVERY_ADMISSION_FAILED,
                details=(("reason", failure.reason.value), ("detail", failure.detail)),
            )
            return
        try:
            result = self.world.reservations.admit_recovery_group(
                request, plans, occupied=self.world.occupied_cells()
            )
        except RecoveryAdmissionError as error:
            failure = RecoveryAdmissionFailure(
                error.reason,
                error.detail,
                self.world.tick,
            )
            self.deadlock_controller.fail_recovery_admission(failure)
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.APPLY_ADMISSION,
                kind=EventKind.RECOVERY_ADMISSION_FAILED,
                details=(("reason", failure.reason.value), ("detail", failure.detail)),
            )
            return
        except TrafficError as error:
            failure = RecoveryAdmissionFailure(
                RecoveryAdmissionFailureReason.RESERVATION_STATE_MISMATCH,
                str(error),
                self.world.tick,
            )
            self.deadlock_controller.fail_recovery_admission(failure)
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.APPLY_ADMISSION,
                kind=EventKind.RECOVERY_ADMISSION_FAILED,
                details=(("reason", failure.reason.value), ("detail", failure.detail)),
            )
            return
        self.deadlock_controller.record_recovery_admission(result)
        self.trace.append(
            tick=self.world.tick,
            phase=TickPhase.APPLY_ADMISSION,
            kind=EventKind.RECOVERY_ADMISSION_EVALUATED,
            details=(
                ("profile", result.profile.value),
                (
                    "evaluation_order",
                    ",".join(
                        f"{ref.robot_id}@{ref.plan_version}:{ref.action_index}"
                        for ref in result.evaluation_order
                    ),
                ),
            ),
        )
        for robot in result.robots:
            if robot.granted_actions:
                self.trace.append(
                    tick=self.world.tick,
                    phase=TickPhase.APPLY_ADMISSION,
                    kind=EventKind.RECOVERY_PREFIX_GRANTED,
                    robot_id=robot.robot_id,
                    details=(
                        ("granted", len(robot.granted_actions)),
                        (
                            "blocked_reason",
                            robot.blocked_reason.value if robot.blocked_reason else "",
                        ),
                    ),
                )
        if result.has_external_block:
            blockers = sorted(
                {
                    blocker.robot_id
                    for robot in result.robots
                    for blocker in robot.blockers
                    if not blocker.internal
                }
            )
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.APPLY_ADMISSION,
                kind=EventKind.RECOVERY_ADMISSION_EXTERNAL_WAIT,
                details=(("blocking_robots", ",".join(blockers)),),
            )
        running = any(
            self.world.robots[robot_id].active_action_ref is not None
            for robot_id in participants
        )
        committed = any(
            self.world.reservations.committed_actions(
                robot_id, containment.installed_plan_versions[robot_id]
            )
            for robot_id in participants
        )
        unfinished = any(
            any(action.status is not ActionStatus.COMPLETED for action in plan.actions)
            for plan in plans
        )
        if (
            unfinished
            and not result.any_new_authority
            and not running
            and not committed
            and not result.has_external_block
        ):
            self.deadlock_controller.stall_recovery_admission()
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.APPLY_ADMISSION,
                kind=EventKind.RECOVERY_ADMISSION_STALLED,
            )

    def _start_actions(self) -> None:
        eligible: list[tuple[str, Action]] = []
        for robot_id, robot in sorted(self.world.robots.items()):
            if robot.active_action_ref is not None or robot_id not in self.world.plans:
                continue
            plan = self.world.plans[robot_id]
            action = next_required_action(plan)
            if action is None or action.status is not ActionStatus.PLANNED:
                continue
            committed = self.world.reservations.committed_actions(
                robot_id,
                plan.version,
            )
            if action.ref not in committed:
                continue
            if any(
                self.world.action(dependency).status is not ActionStatus.COMPLETED
                for dependency in action.dependencies
            ):
                continue
            if action.start != robot.position:
                raise WorldStateError(
                    "eligible action does not start at robot position"
                )
            if any(
                action.ref not in self.world.reservations.owners(resource)
                for resource in action.claims
            ):
                raise WorldStateError("eligible action lacks committed resources")
            eligible.append((robot_id, action))

        for robot_id, action in eligible:
            robot = self.world.robots[robot_id]
            extra_ticks = self.delay_schedule.extra_ticks(action.ref)
            action.transition_to(ActionStatus.RUNNING)
            robot.active_action_ref = action.ref
            robot.remaining_ticks = action.duration_ticks + extra_ticks
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.START_ACTIONS,
                kind=EventKind.ACTION_STARTED,
                robot_id=robot_id,
                task_id=self.world.plans[robot_id].task_id,
                action_ref=action.ref,
                details=(("extra_delay_ticks", extra_ticks),),
            )
            self.deadlock_controller.record_recovery_action_started(action.ref)
        self.world.validate()

    def _preview(self) -> tuple[PreviewAnalysis, DeadlockUpdate]:
        analysis = analyze_preview(self.world)
        for dependency in analysis.dependencies:
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.PREVIEW,
                kind=EventKind.PROSPECTIVE_DEPENDENCY,
                robot_id=dependency.waiting_robot_id,
                action_ref=dependency.preview_action_ref,
                details=(
                    ("blocking_robot_id", dependency.blocking_robot_id),
                    ("blocking_plan_version", dependency.blocking_plan_version),
                    ("resource", resource_label(dependency.resource)),
                    ("occupied_blocker", dependency.occupied_blocker),
                ),
            )
        for contention in analysis.contentions:
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.PREVIEW,
                kind=EventKind.PREVIEW_CONTENTION,
                details=(
                    ("resource", resource_label(contention.resource)),
                    ("robot_ids", ",".join(contention.robot_ids)),
                ),
            )
        versions = {
            robot_id: robot.plan_version
            for robot_id, robot in self.world.robots.items()
        }
        update = self.deadlock_controller.observe(analysis, versions)
        for observation in update.observations:
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.PREVIEW,
                kind=EventKind.PROSPECTIVE_SCC_OBSERVED,
                details=(
                    (
                        "members",
                        self._identity_label(observation.group.trigger_core_identity),
                    ),
                    ("scope", self._identity_label(observation.group.scope_identity)),
                    ("observation_count", observation.count),
                ),
            )
        for group in update.stable:
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.PREVIEW,
                kind=EventKind.STABLE_SCC_DETECTED,
                details=(
                    ("members", self._identity_label(group.trigger_core_identity)),
                    ("scope", self._identity_label(group.scope_identity)),
                ),
            )
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.PREVIEW,
                kind=EventKind.CONTAINMENT_STARTED,
                details=(
                    ("members", self._identity_label(group.scope_identity)),
                    ("cycle_core", self._identity_label(group.trigger_core_identity)),
                ),
            )
        for group in update.expired:
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.PREVIEW,
                kind=EventKind.CANDIDATE_EXPIRED,
                details=(
                    ("members", self._identity_label(group.trigger_core_identity)),
                    ("scope", self._identity_label(group.scope_identity)),
                ),
            )
        for identity in self.deadlock_controller.newly_quiescent(self.world):
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.PREVIEW,
                kind=EventKind.QUIESCENCE_REACHED,
                details=(("members", self._identity_label(identity)),),
            )
        return analysis, update

    @staticmethod
    def _identity_label(identity: CandidateIdentity) -> str:
        return ",".join(
            f"{robot_id}@{plan_version}" for robot_id, plan_version in identity
        )

    def tick(self) -> None:
        if self.task_stream is not None:
            for task in self.task_stream.release_due(self.world):
                self.trace.append(
                    tick=self.world.tick,
                    phase=TickPhase.ADVANCE_TASKS,
                    kind=EventKind.TASK_RELEASED,
                    task_id=task.id,
                    details=(
                        ("pickup_station_id", task.pickup_station_id),
                        ("delivery_station_id", task.delivery_station_id),
                    ),
                )
        self._record("tick-start")
        due = self._complete_due_actions()
        self._record("after-completions")
        self._release_completed(due)
        self._record("after-release")
        self._complete_recovery_if_ready()
        self._advance_tasks()
        self._emit_invalidation(self.deadlock_controller.refresh(self.world))
        self._record("after-task-advance")
        self._admit()
        self._record("after-admission")
        self._start_actions()
        self._record("after-action-start")
        analysis, update = self._preview()
        self._record(
            "after-preview",
            preview_analysis=analysis,
            deadlock_update=update,
        )
        self._confirm()
        self._record("after-confirmation")
        self._install_recovery_if_ready()
        self._record("after-recovery-install")
        self.trace.append(
            tick=self.world.tick,
            phase=TickPhase.ADVANCE_TICK,
            kind=EventKind.TICK_ADVANCED,
            details=(("next_tick", self.world.tick + 1),),
        )
        self.world.tick += 1

    def _emit_invalidation(self, invalidated: CandidateIdentity | None) -> None:
        if invalidated is None:
            return
        self.trace.append(
            tick=self.world.tick,
            phase=TickPhase.ADVANCE_TASKS,
            kind=EventKind.CONTAINMENT_INVALIDATED,
            details=(("members", self._identity_label(invalidated)),),
        )

    _OUTCOME_EVENTS = {
        ConfirmationOutcome.CONFIRMED_DEADLOCK: EventKind.HARD_DEADLOCK_CONFIRMED,
        ConfirmationOutcome.UNSUPPORTED_EXTERNAL: EventKind.CONFIRMATION_UNSUPPORTED,
        ConfirmationOutcome.CLEAR: EventKind.CONTAINMENT_CLEARED,
    }

    def _confirm(self) -> None:
        result = self.deadlock_controller.confirm(self.world, self.world.tick)
        if result is None:
            return
        self.trace.append(
            tick=self.world.tick,
            phase=TickPhase.CONFIRM_DEADLOCK,
            kind=EventKind.CONFIRMED_WAIT_FOR_BUILT,
            details=(
                ("members", self._identity_label(result.scope_identity)),
                ("cycle_core", self._identity_label(result.trigger_core_identity)),
                ("outcome", result.outcome.value),
                ("edges", len(result.graph.edges)),
            ),
        )
        self.trace.append(
            tick=self.world.tick,
            phase=TickPhase.CONFIRM_DEADLOCK,
            kind=self._OUTCOME_EVENTS[result.outcome],
            details=(("members", self._identity_label(result.scope_identity)),),
        )
        if result.outcome is ConfirmationOutcome.CONFIRMED_DEADLOCK:
            self._attempt_recovery(result)

    def _attempt_recovery(self, result) -> None:
        """Produce a scoped recovery proposal once, as diagnostic state only.

        The full affected containment scope is the MAPF participant set.
        Proposal-only: this never installs plans, changes versions, or mutates
        reservations. A missing map or an already-attempted incident is skipped.
        """
        containment = self.deadlock_controller.containment
        if (
            self.warehouse_map is None
            or containment is None
            or containment.recovery_state is not RecoveryState.NOT_ATTEMPTED
        ):
            return
        incident = ConfirmedRecoveryIncident(
            RecoveryIncidentRef(
                result.trigger_core_identity,
                result.scope_identity,
                self.world.tick,
            ),
            result.graph,
        )
        outcome = build_recovery_proposal(
            self.world,
            incident,
            self.warehouse_map,
            max_timestep=self.recovery_max_timestep,
        )
        self.deadlock_controller.record_recovery(outcome)
        members = self._identity_label(result.scope_identity)
        if isinstance(outcome, RecoveryProposal):
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.CONFIRM_DEADLOCK,
                kind=EventKind.RECOVERY_PROPOSAL_READY,
                details=(
                    ("members", members),
                    ("participants", len(outcome.plans)),
                    ("makespan", outcome.solution.makespan),
                    ("solver", outcome.metadata.solver),
                ),
            )
        else:
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.CONFIRM_DEADLOCK,
                kind=EventKind.RECOVERY_PLANNING_FAILED,
                details=(("members", members), ("reason", outcome.reason.value)),
            )

    def _install_recovery_if_ready(self) -> None:
        containment = self.deadlock_controller.containment
        if (
            containment is None
            or containment.recovery_state is not RecoveryState.PROPOSAL_READY
        ):
            return
        assert containment.recovery_proposal is not None
        outcome = commit_recovery_splice(
            self.world,
            self.deadlock_controller,
            containment.recovery_proposal,
            tick=self.world.tick,
        )
        if isinstance(outcome, RecoveryInstallFailure):
            self.deadlock_controller.record_install_failure(
                outcome, tick=self.world.tick
            )
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.INSTALL_RECOVERY,
                kind=EventKind.RECOVERY_INSTALL_FAILED,
                details=(("reason", outcome.reason.value), ("detail", outcome.detail)),
            )
        else:
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.INSTALL_RECOVERY,
                kind=EventKind.RECOVERY_INSTALL_SUCCEEDED,
                details=(
                    (
                        "versions",
                        self._identity_label(
                            tuple(sorted(outcome.installed_versions.items()))
                        ),
                    ),
                ),
            )

    def _complete_recovery_if_ready(self) -> None:
        if self.deadlock_controller.complete_recovery_if_done(
            self.world, tick=self.world.tick
        ):
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.RECOVERY_COMPLETION,
                kind=EventKind.RECOVERY_COMPLETED,
            )
            self._record("after-recovery-completion")
            self.deadlock_controller.release_completed_recovery()

    def _record(
        self,
        checkpoint: str,
        *,
        preview_analysis: PreviewAnalysis | None = None,
        deadlock_update: DeadlockUpdate | None = None,
    ) -> None:
        if self.recorder is not None:
            self.recorder.record(
                checkpoint=checkpoint,
                world=self.world,
                controller=self.deadlock_controller,
                trace=self.trace,
                preview_analysis=preview_analysis,
                deadlock_update=deadlock_update,
            )
