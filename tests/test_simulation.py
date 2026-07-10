from pathlib import Path

from mapf_splice.delay import DeterministicDelaySchedule
from mapf_splice.domain import ActionRef, Cell, Robot, Task, TaskStatus
from mapf_splice.scenario import load_scenario
from mapf_splice.simulation import DeterministicSimulator
from mapf_splice.trace import EventKind
from mapf_splice.traffic import CommittedReservationLedger
from mapf_splice.world import WorldState

ROOT = Path(__file__).parents[1]


def _simulator(*, delay_probability: float = 0) -> DeterministicSimulator:
    cells = {Cell(0, col) for col in range(3)}
    world = WorldState(
        reservations=CommittedReservationLedger(horizon=2),
        robots={"R1": Robot("R1", Cell(0, 0))},
        tasks={"T1": Task("T1", Cell(0, 0), Cell(0, 2), release_tick=0)},
    )
    return DeterministicSimulator(
        world=world,
        is_traversable=cells.__contains__,
        delay_schedule=DeterministicDelaySchedule(
            seed=17,
            probability=delay_probability,
            minimum_extra_ticks=2,
            maximum_extra_ticks=2,
        ),
    )


def test_normal_execution_completes_task_with_deterministic_phase_trace() -> None:
    simulator = _simulator()

    simulator.tick()
    assert simulator.world.robots["R1"].position == Cell(0, 0)
    assert simulator.world.robots["R1"].remaining_ticks == 1
    simulator.tick()
    assert simulator.world.robots["R1"].position == Cell(0, 1)
    simulator.tick()

    task = simulator.world.tasks["T1"]
    robot = simulator.world.robots["R1"]
    assert task.status is TaskStatus.COMPLETED
    assert robot.position == Cell(0, 2)
    assert robot.active_task_id is None
    assert robot.plan_version == 2
    assert simulator.world.plans == {}
    assert simulator.world.reservations.all_committed_actions() == ()
    assert [event.sequence for event in simulator.trace.events] == list(
        range(len(simulator.trace.events))
    )
    for tick in range(3):
        phases = [event.phase for event in simulator.trace.events if event.tick == tick]
        assert phases == sorted(phases)
    status_changes = [
        event
        for event in simulator.trace.events
        if event.kind is EventKind.TASK_STATUS_CHANGED
    ]
    assert [dict(event.details)["to"] for event in status_changes] == [
        "to-pickup",
        "carrying",
        "to-drop-off",
        "completed",
    ]


def test_delay_is_action_ref_derived_and_keeps_position_at_source() -> None:
    simulator = _simulator(delay_probability=1)
    schedule = simulator.delay_schedule
    refs = (ActionRef("R1", 2, 0), ActionRef("R2", 9, 4))
    forward = {ref: schedule.extra_ticks(ref) for ref in refs}
    reverse = {ref: schedule.extra_ticks(ref) for ref in reversed(refs)}
    assert forward == reverse
    assert forward[refs[0]] == 2

    simulator.tick()
    robot = simulator.world.robots["R1"]
    assert robot.position == Cell(0, 0)
    assert robot.remaining_ticks == 3
    simulator.tick()
    assert robot.position == Cell(0, 0)
    assert robot.remaining_ticks == 2


def test_same_tick_completions_apply_as_one_deterministic_batch() -> None:
    floor = {Cell(row, col) for row in range(2) for col in range(2)}
    world = WorldState(
        reservations=CommittedReservationLedger(horizon=1),
        robots={
            "R2": Robot("R2", Cell(1, 0)),
            "R1": Robot("R1", Cell(0, 0)),
        },
        tasks={
            "T2": Task("T2", Cell(1, 0), Cell(1, 1), release_tick=0),
            "T1": Task("T1", Cell(0, 0), Cell(0, 1), release_tick=0),
        },
    )
    simulator = DeterministicSimulator(
        world=world,
        is_traversable=floor.__contains__,
        delay_schedule=DeterministicDelaySchedule(0, 0, 0, 0),
    )
    simulator.tick()

    simulator.tick()

    assert world.robots["R1"].position == Cell(0, 1)
    assert world.robots["R2"].position == Cell(1, 1)
    completion_events = [
        event
        for event in simulator.trace.events
        if event.tick == 1 and event.kind is EventKind.ACTION_COMPLETED
    ]
    assert [event.robot_id for event in completion_events] == ["R1", "R2"]


def test_hero_scenario_produces_identical_seeded_trace() -> None:
    scenario_path = ROOT / "scenarios/compact-three-robot/scenario.json"
    left = DeterministicSimulator.from_scenario(load_scenario(scenario_path))
    right = DeterministicSimulator.from_scenario(load_scenario(scenario_path))

    for _ in range(5):
        left.tick()
        right.tick()

    assert left.trace.events == right.trace.events
    assert left.world.tick == right.world.tick == 5
