from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class DomainError(ValueError):
    """Raised when a domain invariant or state transition is invalid."""


@dataclass(frozen=True, order=True, slots=True)
class Cell:
    row: int
    col: int

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Cell:
        return cls(row=int(value["row"]), col=int(value["col"]))

    def manhattan_distance(self, other: Cell) -> int:
        return abs(self.row - other.row) + abs(self.col - other.col)


class TaskStatus(StrEnum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    TO_PICKUP = "to-pickup"
    CARRYING = "carrying"
    TO_DROPOFF = "to-drop-off"
    COMPLETED = "completed"


_NEXT_TASK_STATUS = {
    TaskStatus.PENDING: TaskStatus.ASSIGNED,
    TaskStatus.ASSIGNED: TaskStatus.TO_PICKUP,
    TaskStatus.TO_PICKUP: TaskStatus.CARRYING,
    TaskStatus.CARRYING: TaskStatus.TO_DROPOFF,
    TaskStatus.TO_DROPOFF: TaskStatus.COMPLETED,
}


@dataclass(slots=True)
class Task:
    id: str
    pickup: Cell
    dropoff: Cell
    release_tick: int
    status: TaskStatus = TaskStatus.PENDING
    assigned_robot_id: str | None = None

    def __post_init__(self) -> None:
        if self.release_tick < 0:
            raise DomainError("task release_tick cannot be negative")
        if self.status is TaskStatus.PENDING and self.assigned_robot_id is not None:
            raise DomainError("a pending task cannot have an assigned robot")
        if self.status is not TaskStatus.PENDING and self.assigned_robot_id is None:
            raise DomainError("a non-pending task must have an assigned robot")

    def assign(self, robot_id: str) -> None:
        if self.status is not TaskStatus.PENDING:
            raise DomainError(f"cannot assign task in status {self.status.value}")
        if not robot_id:
            raise DomainError("assigned robot id cannot be empty")
        self.assigned_robot_id = robot_id
        self.status = TaskStatus.ASSIGNED

    def transition_to(self, next_status: TaskStatus) -> None:
        expected = _NEXT_TASK_STATUS.get(self.status)
        if next_status is not expected:
            expected_label = expected.value if expected is not None else "none"
            raise DomainError(
                f"invalid task transition {self.status.value} -> {next_status.value}; "
                f"expected {expected_label}"
            )
        if self.assigned_robot_id is None:
            raise DomainError("task must be assigned before it can advance")
        self.status = next_status


class ActionKind(StrEnum):
    MOVE = "move"
    WAIT = "wait"


class ActionStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELED = "canceled"


@dataclass(frozen=True, order=True, slots=True)
class VertexResource:
    cell: Cell


@dataclass(frozen=True, order=True, slots=True)
class EdgeResource:
    first: Cell
    second: Cell

    def __post_init__(self) -> None:
        if self.first.manhattan_distance(self.second) != 1:
            raise DomainError("edge resource must connect adjacent cells")
        if self.second < self.first:
            first = self.first
            object.__setattr__(self, "first", self.second)
            object.__setattr__(self, "second", first)


Resource = VertexResource | EdgeResource


@dataclass(frozen=True, order=True, slots=True)
class ActionRef:
    robot_id: str
    plan_version: int
    action_index: int

    def __post_init__(self) -> None:
        if not self.robot_id:
            raise DomainError("action robot id cannot be empty")
        if self.plan_version < 1:
            raise DomainError("action plan version must be positive")
        if self.action_index < 0:
            raise DomainError("action index cannot be negative")


@dataclass(slots=True)
class Action:
    ref: ActionRef
    kind: ActionKind
    start: Cell
    end: Cell
    duration_ticks: int = 1
    dependencies: tuple[ActionRef, ...] = ()
    status: ActionStatus = ActionStatus.PLANNED

    def __post_init__(self) -> None:
        if self.duration_ticks < 1:
            raise DomainError("action duration must be at least one tick")
        distance = self.start.manhattan_distance(self.end)
        if self.kind is ActionKind.MOVE and distance != 1:
            raise DomainError("move action must traverse exactly one grid edge")
        if self.kind is ActionKind.WAIT and distance != 0:
            raise DomainError("wait action must remain on one cell")
        if self.ref in self.dependencies:
            raise DomainError("action cannot depend on itself")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise DomainError("action dependencies must be unique")

    def transition_to(self, next_status: ActionStatus) -> None:
        allowed = {
            ActionStatus.PLANNED: {ActionStatus.RUNNING, ActionStatus.CANCELED},
            ActionStatus.RUNNING: {ActionStatus.COMPLETED},
        }
        if next_status not in allowed.get(self.status, set()):
            raise DomainError(
                f"invalid action transition {self.status.value} -> "
                f"{next_status.value}"
            )
        self.status = next_status

    @property
    def claims(self) -> tuple[Resource, ...]:
        if self.kind is ActionKind.WAIT:
            return (VertexResource(self.start),)
        return (VertexResource(self.end), EdgeResource(self.start, self.end))


@dataclass(frozen=True, slots=True)
class Plan:
    robot_id: str
    version: int
    task_id: str
    phase_goal: Cell
    actions: tuple[Action, ...]

    def __post_init__(self) -> None:
        if not self.robot_id or not self.task_id:
            raise DomainError("plan robot and task ids cannot be empty")
        if self.version < 1:
            raise DomainError("plan version must be positive")
        for index, action in enumerate(self.actions):
            expected = ActionRef(self.robot_id, self.version, index)
            if action.ref != expected:
                raise DomainError(
                    f"plan action {index} has ref {action.ref}, expected {expected}"
                )
            if index > 0 and self.actions[index - 1].end != action.start:
                raise DomainError(f"plan action chain breaks at index {index}")
            same_plan_dependencies = {
                dependency
                for dependency in action.dependencies
                if dependency.robot_id == self.robot_id
                and dependency.plan_version == self.version
            }
            expected_dependencies: set[ActionRef] = set()
            if index > 0:
                expected_dependencies.add(
                    ActionRef(self.robot_id, self.version, index - 1)
                )
            if same_plan_dependencies != expected_dependencies:
                raise DomainError(
                    f"plan action {index} has invalid same-robot dependencies"
                )
        if self.actions and self.actions[-1].end != self.phase_goal:
            raise DomainError("plan actions do not end at the phase goal")


@dataclass(slots=True)
class Robot:
    id: str
    position: Cell
    active_task_id: str | None = None
    payload_task_id: str | None = None
    plan_version: int = 0
    active_action_ref: ActionRef | None = None
    remaining_ticks: int = 0

    def __post_init__(self) -> None:
        if not self.id:
            raise DomainError("robot id cannot be empty")
        if self.plan_version < 0 or self.remaining_ticks < 0:
            raise DomainError(
                "robot plan version and remaining ticks cannot be negative"
            )
        if (self.active_action_ref is None) != (self.remaining_ticks == 0):
            raise DomainError(
                "an active action and positive remaining ticks must be set together"
            )

    def install(self, plan: Plan) -> None:
        if self.active_action_ref is not None:
            raise DomainError("cannot replace a plan while an action is in progress")
        if plan.robot_id != self.id:
            raise DomainError("cannot install another robot's plan")
        expected_version = self.plan_version + 1
        if plan.version != expected_version:
            raise DomainError(
                f"expected plan version {expected_version}, got {plan.version}"
            )
        self.plan_version = plan.version

    def owns_current_version(self, action_ref: ActionRef) -> bool:
        return (
            action_ref.robot_id == self.id
            and action_ref.plan_version == self.plan_version
        )
