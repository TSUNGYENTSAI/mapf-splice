from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum

from mapf_splice.domain import ActionRef


class TickPhase(IntEnum):
    COLLECT_COMPLETIONS = 1
    VALIDATE_COMPLETIONS = 2
    APPLY_COMPLETIONS = 3
    RELEASE_RESERVATIONS = 4
    ADVANCE_TASKS = 5
    COLLECT_ADMISSION = 6
    APPLY_ADMISSION = 7
    COLLECT_STARTS = 8
    START_ACTIONS = 9
    PREVIEW = 10
    CONFIRM_DEADLOCK = 11
    APPEND_EVENTS = 12
    ADVANCE_TICK = 13


class EventKind(StrEnum):
    ACTION_COMPLETED = "action-completed"
    RESERVATION_RELEASED = "reservation-released"
    TASK_ASSIGNED = "task-assigned"
    TASK_STATUS_CHANGED = "task-status-changed"
    PLAN_INSTALLED = "plan-installed"
    PLANNING_FAILED = "planning-failed"
    ADMISSION_ACCEPTED = "admission-accepted"
    ADMISSION_REJECTED = "admission-rejected"
    ACTION_STARTED = "action-started"
    PROSPECTIVE_DEPENDENCY = "prospective-dependency"
    PREVIEW_CONTENTION = "preview-contention"
    PROSPECTIVE_SCC_OBSERVED = "prospective-scc-observed"
    STABLE_SCC_DETECTED = "stable-scc-detected"
    CONTAINMENT_STARTED = "containment-started"
    CANDIDATE_EXPIRED = "candidate-expired"
    QUIESCENCE_REACHED = "quiescence-reached"
    CONFIRMED_WAIT_FOR_BUILT = "confirmed-wait-for-built"
    HARD_DEADLOCK_CONFIRMED = "hard-deadlock-confirmed"
    CONTAINMENT_EXTERNAL_BLOCKED = "containment-external-blocked"
    CONTAINMENT_CLEARED = "containment-cleared"
    CONTAINMENT_INVALIDATED = "containment-invalidated"
    TICK_ADVANCED = "tick-advanced"


TraceValue = str | int | bool


@dataclass(frozen=True, slots=True)
class TraceEvent:
    sequence: int
    tick: int
    phase: TickPhase
    kind: EventKind
    robot_id: str | None = None
    task_id: str | None = None
    action_ref: ActionRef | None = None
    details: tuple[tuple[str, TraceValue], ...] = ()


@dataclass(slots=True)
class EventTrace:
    _events: list[TraceEvent] = field(default_factory=list, init=False, repr=False)

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        return tuple(self._events)

    def append(
        self,
        *,
        tick: int,
        phase: TickPhase,
        kind: EventKind,
        robot_id: str | None = None,
        task_id: str | None = None,
        action_ref: ActionRef | None = None,
        details: tuple[tuple[str, TraceValue], ...] = (),
    ) -> TraceEvent:
        event = TraceEvent(
            sequence=len(self._events),
            tick=tick,
            phase=phase,
            kind=kind,
            robot_id=robot_id,
            task_id=task_id,
            action_ref=action_ref,
            details=details,
        )
        self._events.append(event)
        return event
