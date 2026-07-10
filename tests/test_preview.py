from mapf_splice.domain import Cell, Robot, Task, TaskStatus, VertexResource
from mapf_splice.planning import compile_path
from mapf_splice.preview import analyze_preview
from mapf_splice.traffic import CommittedReservationLedger
from mapf_splice.world import WorldState


def test_preview_reports_dependencies_and_read_only_contention() -> None:
    starts = {
        "R1": Cell(0, 0),
        "R2": Cell(0, 3),
        "R3": Cell(2, 2),
    }
    routes = {
        "R1": (Cell(0, 0), Cell(0, 1), Cell(0, 2)),
        "R2": (Cell(0, 3), Cell(0, 2)),
        "R3": (Cell(2, 2), Cell(1, 2), Cell(0, 2)),
    }
    robots = {
        robot_id: Robot(robot_id, start, active_task_id=f"T-{robot_id}")
        for robot_id, start in starts.items()
    }
    tasks = {
        robot_id: Task(
            id=f"T-{robot_id}",
            pickup=start,
            dropoff=routes[robot_id][-1],
            release_tick=0,
            status=TaskStatus.ASSIGNED,
            assigned_robot_id=robot_id,
        )
        for robot_id, start in starts.items()
    }
    world = WorldState(
        reservations=CommittedReservationLedger(horizon=1),
        robots=robots,
        tasks={task.id: task for task in tasks.values()},
    )
    plans = {}
    for robot_id in sorted(robots):
        plan = compile_path(
            routes[robot_id],
            robot_id=robot_id,
            plan_version=1,
            task_id=f"T-{robot_id}",
        )
        world.install_plan(plan)
        tasks[robot_id].transition_to(TaskStatus.TO_PICKUP)
        plans[robot_id] = plan
    world.reservations.acquire_initial_batch(
        tuple(plans.values()),
        occupied=world.occupied_cells(),
    )
    world.validate()
    committed_before = world.reservations.all_committed_actions()

    analysis = analyze_preview(world)

    vertex = VertexResource(Cell(0, 2))
    dependencies = [
        dependency
        for dependency in analysis.dependencies
        if dependency.resource == vertex
    ]
    dependency_pairs = {
        (item.waiting_robot_id, item.blocking_robot_id) for item in dependencies
    }
    assert dependency_pairs == {
        ("R1", "R2"),
        ("R3", "R2"),
    }
    assert all(not item.occupied_blocker for item in dependencies)
    assert all(item.blocking_action_refs for item in dependencies)
    contention = next(item for item in analysis.contentions if item.resource == vertex)
    assert contention.robot_ids == ("R1", "R3")
    assert world.reservations.all_committed_actions() == committed_before


def test_preview_reports_current_occupancy_as_blocker() -> None:
    robots = {
        "R1": Robot("R1", Cell(0, 0), active_task_id="T1"),
        "R2": Robot("R2", Cell(0, 2), active_task_id="T2"),
    }
    tasks = {
        "T1": Task(
            "T1",
            Cell(0, 0),
            Cell(0, 2),
            0,
            TaskStatus.ASSIGNED,
            "R1",
        ),
        "T2": Task(
            "T2",
            Cell(0, 2),
            Cell(1, 2),
            0,
            TaskStatus.ASSIGNED,
            "R2",
        ),
    }
    world = WorldState(
        reservations=CommittedReservationLedger(horizon=1),
        robots=robots,
        tasks=tasks,
    )
    plans = (
        compile_path(
            (Cell(0, 0), Cell(0, 1), Cell(0, 2)),
            robot_id="R1",
            plan_version=1,
            task_id="T1",
        ),
        compile_path(
            (Cell(0, 2), Cell(1, 2)),
            robot_id="R2",
            plan_version=1,
            task_id="T2",
        ),
    )
    for plan in plans:
        world.install_plan(plan)
        tasks[plan.task_id].transition_to(TaskStatus.TO_PICKUP)
    world.reservations.acquire_initial_batch(plans, occupied=world.occupied_cells())

    dependency = next(
        item
        for item in analyze_preview(world).dependencies
        if item.waiting_robot_id == "R1" and item.blocking_robot_id == "R2"
    )

    assert dependency.resource == VertexResource(Cell(0, 2))
    assert dependency.occupied_blocker
    assert dependency.blocking_action_refs == ()
