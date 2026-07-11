from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mapf_splice.domain import Cell, DomainError, Plan
from mapf_splice.scenario import WarehouseMap

DEFAULT_RECOVERY_SEED = 0
DEFAULT_RECOVERY_MAX_TIMESTEP = 256
PYPIBT_SOURCE_COMMIT = "a3c97f60413c6619a29a5022969896bc54877edc"


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


@dataclass(frozen=True, slots=True)
class RecoverySolverMetadata:
    solver: str
    seed: int
    max_timestep: int
    makespan: int
    source_commit: str


@dataclass(frozen=True, slots=True)
class RecoveryProposal:
    """A validated, not-yet-installed scoped MAPF recovery for a confirmed deadlock."""

    identity: tuple[tuple[str, int], ...]
    expected_plan_versions: dict[str, int]
    starts: dict[str, Cell]
    goals: dict[str, Cell]
    solution: ScopedMapfSolution
    plans: dict[str, Plan]
    metadata: RecoverySolverMetadata


@dataclass(frozen=True, slots=True)
class RecoveryPlanningFailure:
    reason: RecoveryFailureReason
    detail: str
