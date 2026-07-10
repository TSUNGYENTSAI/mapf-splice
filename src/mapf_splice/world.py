from __future__ import annotations

from dataclasses import dataclass, field

from mapf_splice.domain import Cell, DomainError, Plan, Robot, Task, TaskStatus
from mapf_splice.traffic import CommittedReservationLedger


class WorldStateError(DomainError):
    """Raised when the authoritative simulation state is inconsistent."""


@dataclass(slots=True)
class WorldState:
    """The single mutable source of truth for one deterministic simulation."""

    reservations: CommittedReservationLedger
    tick: int = 0
    robots: dict[str, Robot] = field(default_factory=dict)
    tasks: dict[str, Task] = field(default_factory=dict)
    plans: dict[str, Plan] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.tick < 0:
            raise WorldStateError("world tick cannot be negative")
        for robot_id, robot in self.robots.items():
            if robot_id != robot.id:
                raise WorldStateError("robot dictionary key does not match robot id")
        for task_id, task in self.tasks.items():
            if task_id != task.id:
                raise WorldStateError("task dictionary key does not match task id")

        occupied: dict[Cell, str] = {}
        for robot in self.robots.values():
            other_id = occupied.get(robot.position)
            if other_id is not None:
                raise WorldStateError(
                    f"robots {other_id} and {robot.id} occupy {robot.position}"
                )
            occupied[robot.position] = robot.id

        active_task_ids: set[str] = set()
        for robot in self.robots.values():
            if robot.active_task_id is None:
                if robot.payload_task_id is not None:
                    raise WorldStateError("an idle robot cannot carry a task payload")
                if robot.active_action_ref is not None:
                    raise WorldStateError("an idle robot cannot run an action")
                if robot.id in self.plans:
                    raise WorldStateError("an idle robot cannot retain a current plan")
                if self.reservations.committed_actions(
                    robot.id,
                    robot.plan_version,
                ):
                    raise WorldStateError("an idle robot cannot retain reservations")
                continue
            if robot.active_task_id in active_task_ids:
                raise WorldStateError("one task cannot be active on multiple robots")
            active_task_ids.add(robot.active_task_id)
            task = self.tasks.get(robot.active_task_id)
            if task is None:
                raise WorldStateError(
                    f"robot {robot.id} references an unknown active task"
                )
            if task.status is TaskStatus.COMPLETED:
                raise WorldStateError("a completed task cannot remain active")
            if task.assigned_robot_id != robot.id:
                raise WorldStateError("robot and task assignment do not agree")
            if task.status in {TaskStatus.CARRYING, TaskStatus.TO_DROPOFF}:
                if robot.payload_task_id != task.id:
                    raise WorldStateError(
                        "carrying task and robot payload do not agree"
                    )
            elif robot.payload_task_id is not None:
                raise WorldStateError("robot carries a payload before pickup")
            if task.status in {
                TaskStatus.TO_PICKUP,
                TaskStatus.CARRYING,
                TaskStatus.TO_DROPOFF,
            } and robot.id not in self.plans:
                raise WorldStateError("active task phase has no current plan")

        for task in self.tasks.values():
            if task.status in {TaskStatus.PENDING, TaskStatus.COMPLETED}:
                continue
            robot = self.robots.get(task.assigned_robot_id or "")
            if robot is None or robot.active_task_id != task.id:
                raise WorldStateError("assigned task is not active on its robot")

        for robot_id, plan in self.plans.items():
            robot = self.robots.get(robot_id)
            if robot is None:
                raise WorldStateError("plan references an unknown robot")
            if robot.active_task_id is None:
                raise WorldStateError("idle robot cannot retain a current plan")
            if plan.robot_id != robot_id or plan.version != robot.plan_version:
                raise WorldStateError("current plan does not match robot plan version")
            if (
                robot.active_task_id is not None
                and plan.task_id != robot.active_task_id
            ):
                raise WorldStateError("current plan does not match active task")

        for action_ref in self.reservations.all_committed_actions():
            robot = self.robots.get(action_ref.robot_id)
            if robot is None or robot.plan_version != action_ref.plan_version:
                raise WorldStateError("reservation is owned by a stale plan version")
            plan = self.plans.get(action_ref.robot_id)
            if plan is None or action_ref.action_index >= len(plan.actions):
                raise WorldStateError("reservation references a missing action")
            if plan.actions[action_ref.action_index].ref != action_ref:
                raise WorldStateError("reservation action does not match current plan")

    def occupied_cells(self) -> dict[Cell, str]:
        return {robot.position: robot.id for robot in self.robots.values()}

    def assign_task(self, task_id: str, robot_id: str) -> None:
        self.validate()
        task = self.tasks.get(task_id)
        robot = self.robots.get(robot_id)
        if task is None:
            raise WorldStateError(f"unknown task {task_id}")
        if robot is None:
            raise WorldStateError(f"unknown robot {robot_id}")
        if task.status is not TaskStatus.PENDING:
            raise WorldStateError("only pending tasks can be assigned")
        if task.release_tick > self.tick:
            raise WorldStateError("task has not been released")
        if robot.active_task_id is not None:
            raise WorldStateError("robot is not idle")
        if robot.active_action_ref is not None or robot.payload_task_id is not None:
            raise WorldStateError("idle robot execution state is not quiescent")
        if robot.id in self.plans or self.reservations.committed_actions(
            robot.id,
            robot.plan_version,
        ):
            raise WorldStateError("idle robot retains plan authority")
        task.assign(robot.id)
        robot.active_task_id = task.id
        self.validate()

    def install_plan(self, plan: Plan) -> None:
        self.validate()
        robot = self.robots.get(plan.robot_id)
        if robot is None:
            raise WorldStateError(f"unknown robot {plan.robot_id}")
        if robot.active_task_id != plan.task_id:
            raise WorldStateError("plan task is not active on its robot")
        if plan.task_id not in self.tasks:
            raise WorldStateError(f"unknown task {plan.task_id}")
        start = plan.actions[0].start if plan.actions else plan.phase_goal
        if start != robot.position:
            raise WorldStateError("plan does not start at authoritative robot position")
        if self.reservations.committed_actions(robot.id, robot.plan_version):
            raise WorldStateError("old plan still owns committed reservations")
        robot.install(plan)
        self.plans[robot.id] = plan
        self.validate()
