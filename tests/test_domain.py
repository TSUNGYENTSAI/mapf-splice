import pytest

from mapf_splice.domain import (
    Action,
    ActionKind,
    ActionRef,
    ActionStatus,
    Cell,
    DomainError,
    EdgeResource,
    Plan,
    Robot,
    Task,
    TaskStatus,
    VertexResource,
)
from mapf_splice.planning import compile_path


def test_task_preserves_every_lifecycle_transition() -> None:
    task = Task(
        id="T1",
        pickup=Cell(0, 0),
        dropoff=Cell(2, 0),
        release_tick=0,
    )

    task.assign("R1")
    observed = [task.status]
    for status in (
        TaskStatus.TO_PICKUP,
        TaskStatus.CARRYING,
        TaskStatus.TO_DROPOFF,
        TaskStatus.COMPLETED,
    ):
        task.transition_to(status)
        observed.append(task.status)

    assert observed == [
        TaskStatus.ASSIGNED,
        TaskStatus.TO_PICKUP,
        TaskStatus.CARRYING,
        TaskStatus.TO_DROPOFF,
        TaskStatus.COMPLETED,
    ]


def test_task_cannot_skip_a_lifecycle_transition() -> None:
    task = Task("T1", Cell(0, 0), Cell(2, 0), release_tick=0)
    task.assign("R1")

    with pytest.raises(DomainError, match="expected to-pickup"):
        task.transition_to(TaskStatus.CARRYING)


def test_pending_task_cannot_already_have_an_assignee() -> None:
    with pytest.raises(DomainError, match="pending task cannot"):
        Task(
            "T1",
            Cell(0, 0),
            Cell(2, 0),
            release_tick=0,
            assigned_robot_id="R1",
        )


def test_action_status_does_not_include_traffic_admission() -> None:
    action = Action(
        ref=ActionRef("R1", 1, 0),
        kind=ActionKind.MOVE,
        start=Cell(0, 0),
        end=Cell(0, 1),
    )

    action.transition_to(ActionStatus.RUNNING)
    action.transition_to(ActionStatus.COMPLETED)

    with pytest.raises(DomainError, match="invalid action transition"):
        action.transition_to(ActionStatus.CANCELED)


def test_action_claims_target_vertex_and_canonical_undirected_edge() -> None:
    start = Cell(0, 1)
    end = Cell(0, 0)
    move = Action(
        ref=ActionRef("R1", 1, 0),
        kind=ActionKind.MOVE,
        start=start,
        end=end,
    )
    wait = Action(
        ref=ActionRef("R1", 1, 1),
        kind=ActionKind.WAIT,
        start=end,
        end=end,
    )

    assert move.claims == (
        VertexResource(end),
        EdgeResource(Cell(0, 0), Cell(0, 1)),
    )
    assert EdgeResource(start, end) == EdgeResource(end, start)
    assert wait.claims == (VertexResource(end),)


def test_plan_versions_are_monotonic_and_robot_local() -> None:
    robot = Robot(id="R1", position=Cell(0, 0))
    first = compile_path(
        (Cell(0, 0), Cell(0, 1)),
        robot_id="R1",
        plan_version=1,
        task_id="T1",
    )
    robot.install(first)

    assert robot.plan_version == 1
    assert robot.owns_current_version(first.actions[0].ref)

    stale_version = Plan("R1", 1, "T1", Cell(0, 1), actions=())
    with pytest.raises(DomainError, match="expected plan version 2"):
        robot.install(stale_version)


def test_plan_rejects_a_discontinuous_action_chain() -> None:
    actions = (
        Action(
            ActionRef("R1", 1, 0),
            ActionKind.MOVE,
            Cell(0, 0),
            Cell(0, 1),
        ),
        Action(
            ActionRef("R1", 1, 1),
            ActionKind.MOVE,
            Cell(2, 0),
            Cell(2, 1),
            dependencies=(ActionRef("R1", 1, 0),),
        ),
    )

    with pytest.raises(DomainError, match="chain breaks"):
        Plan("R1", 1, "T1", Cell(2, 1), actions)


def test_plan_requires_exact_previous_action_dependency() -> None:
    actions = (
        Action(
            ActionRef("R1", 1, 0),
            ActionKind.MOVE,
            Cell(0, 0),
            Cell(0, 1),
        ),
        Action(
            ActionRef("R1", 1, 1),
            ActionKind.MOVE,
            Cell(0, 1),
            Cell(0, 2),
        ),
    )

    with pytest.raises(DomainError, match="same-robot dependencies"):
        Plan("R1", 1, "T1", Cell(0, 2), actions)


def test_path_compiler_distinguishes_move_and_planned_wait() -> None:
    plan = compile_path(
        (Cell(0, 0), Cell(0, 1), Cell(0, 1), Cell(1, 1)),
        robot_id="R1",
        plan_version=3,
        task_id="T1",
        base_duration_ticks=2,
    )

    assert [action.kind for action in plan.actions] == [
        ActionKind.MOVE,
        ActionKind.WAIT,
        ActionKind.MOVE,
    ]
    assert all(action.duration_ticks == 2 for action in plan.actions)
    assert plan.actions[1].dependencies == (ActionRef("R1", 3, 0),)
    assert plan.actions[2].dependencies == (ActionRef("R1", 3, 1),)
