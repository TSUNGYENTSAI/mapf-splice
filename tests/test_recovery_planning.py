"""Read-only scoped recovery planner: problem -> solve -> validate -> ADG."""
import pytest

pytest.importorskip("numpy")

from mapf_splice.domain import Cell, Robot, Task, TaskStatus  # noqa: E402
from mapf_splice.planning import compile_path  # noqa: E402
from mapf_splice.recovery import (  # noqa: E402
    RecoveryFailureReason,
    RecoveryPlanningFailure,
    RecoveryProposal,
    plan_recovery,
)
from mapf_splice.scenario import WarehouseMap  # noqa: E402
from mapf_splice.traffic import CommittedReservationLedger  # noqa: E402
from mapf_splice.world import WorldState  # noqa: E402

OPEN = WarehouseMap(rows=("....." , "....." , "....."))


def _route(start: Cell, goal: Cell) -> tuple[Cell, ...]:
    cells = [start]
    row, col = start.row, start.col
    while col != goal.col:
        col += 1 if goal.col > col else -1
        cells.append(Cell(row, col))
    while row != goal.row:
        row += 1 if goal.row > row else -1
        cells.append(Cell(row, col))
    return tuple(cells)


def _dropoff_world(specs: dict[str, tuple[Cell, Cell]]) -> WorldState:
    """specs: robot_id -> (start_position, dropoff_goal). All TO_DROPOFF at v2."""
    robots, tasks, plans = {}, {}, {}
    for robot_id, (start, dropoff) in specs.items():
        task_id = f"T-{robot_id}"
        robots[robot_id] = Robot(
            robot_id,
            start,
            active_task_id=task_id,
            payload_task_id=task_id,
            plan_version=2,
        )
        tasks[task_id] = Task(
            task_id, start, dropoff, 0, TaskStatus.TO_DROPOFF, robot_id
        )
        plans[robot_id] = compile_path(
            _route(start, dropoff), robot_id=robot_id, plan_version=2, task_id=task_id
        )
    return WorldState(
        reservations=CommittedReservationLedger(horizon=1),
        robots=robots,
        tasks=tasks,
        plans=plans,
    )


def _snapshot(world: WorldState):
    return (
        {r: rob.position for r, rob in world.robots.items()},
        {r: rob.plan_version for r, rob in world.robots.items()},
        {t: task.status for t, task in world.tasks.items()},
        {r: plan.version for r, plan in world.plans.items()},
        world.reservations.reservation_snapshot(),
    )


def test_plan_recovery_produces_proposal_reaching_goals() -> None:
    world = _dropoff_world(
        {"R1": (Cell(0, 0), Cell(0, 4)), "R2": (Cell(2, 0), Cell(2, 4))}
    )
    identity = (("R1", 2), ("R2", 2))
    result = plan_recovery(world, identity, OPEN)
    assert isinstance(result, RecoveryProposal)
    assert result.scope_identity == identity
    assert result.expected_plan_versions == {"R1": 2, "R2": 2}
    assert result.starts == {"R1": Cell(0, 0), "R2": Cell(2, 0)}
    assert result.goals == {"R1": Cell(0, 4), "R2": Cell(2, 4)}
    for robot_id in ("R1", "R2"):
        plan = result.plans[robot_id]
        assert plan.version == 3  # current + 1, not installed
        assert plan.phase_goal == result.goals[robot_id]
    assert result.metadata.solver == "pibt"
    assert result.metadata.makespan == result.solution.makespan


def test_plan_recovery_is_read_only() -> None:
    world = _dropoff_world(
        {"R1": (Cell(0, 0), Cell(0, 4)), "R2": (Cell(2, 0), Cell(2, 4))}
    )
    before = _snapshot(world)
    plan_recovery(world, (("R1", 2), ("R2", 2)), OPEN)
    assert _snapshot(world) == before


def test_active_robot_outside_scope_is_unsupported() -> None:
    world = _dropoff_world(
        {
            "R1": (Cell(0, 0), Cell(0, 4)),
            "R2": (Cell(2, 0), Cell(2, 4)),
            "R3": (Cell(1, 0), Cell(1, 4)),
        }
    )
    # scope omits the still-active R3
    result = plan_recovery(world, (("R1", 2), ("R2", 2)), OPEN)
    assert isinstance(result, RecoveryPlanningFailure)
    assert result.reason is RecoveryFailureReason.UNSUPPORTED_SCOPE


def test_shared_phase_goal_is_duplicate_goal() -> None:
    world = _dropoff_world(
        {"R1": (Cell(0, 0), Cell(0, 4)), "R2": (Cell(2, 0), Cell(0, 4))}
    )
    result = plan_recovery(world, (("R1", 2), ("R2", 2)), OPEN)
    assert isinstance(result, RecoveryPlanningFailure)
    assert result.reason is RecoveryFailureReason.DUPLICATE_GOAL


def test_stale_plan_version_in_scope_is_unsupported() -> None:
    world = _dropoff_world(
        {"R1": (Cell(0, 0), Cell(0, 4)), "R2": (Cell(2, 0), Cell(2, 4))}
    )
    result = plan_recovery(world, (("R1", 1), ("R2", 2)), OPEN)
    assert isinstance(result, RecoveryPlanningFailure)
    assert result.reason is RecoveryFailureReason.UNSUPPORTED_SCOPE


def test_recovery_receives_full_affected_scope_not_just_core() -> None:
    from mapf_splice.deadlock import DeadlockController
    from mapf_splice.domain import ActionRef, VertexResource
    from mapf_splice.preview import PreviewAnalysis, ProspectiveDependency

    world = _dropoff_world(
        {
            "R1": (Cell(0, 0), Cell(0, 4)),
            "R2": (Cell(2, 0), Cell(2, 4)),
            "R3": (Cell(1, 0), Cell(1, 4)),  # upstream waiter, not in the cycle core
        }
    )

    def dep(waiting: str, blocking: str) -> ProspectiveDependency:
        return ProspectiveDependency(
            waiting_robot_id=waiting,
            waiting_plan_version=2,
            preview_action_ref=ActionRef(waiting, 2, 1),
            blocking_robot_id=blocking,
            blocking_plan_version=2,
            resource=VertexResource(Cell(0, 0)),
            blocking_action_refs=(ActionRef(blocking, 2, 0),),
            occupied_blocker=False,
        )

    # Core R1<->R2; R3 waits into R1 -> affected scope is R1,R2,R3.
    analysis = PreviewAnalysis(
        (dep("R1", "R2"), dep("R2", "R1"), dep("R3", "R1")), ()
    )
    controller = DeadlockController(1)
    controller.observe(analysis, {"R1": 2, "R2": 2, "R3": 2})
    containment = controller.containment
    assert containment.trigger_core_identity == (("R1", 2), ("R2", 2))
    assert containment.scope_identity == (("R1", 2), ("R2", 2), ("R3", 2))

    result = plan_recovery(world, containment.scope_identity, OPEN)
    assert isinstance(result, RecoveryProposal)
    assert result.scope_identity == (("R1", 2), ("R2", 2), ("R3", 2))
    assert set(result.plans) == {"R1", "R2", "R3"}
