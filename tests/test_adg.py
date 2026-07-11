import pytest

from mapf_splice.adg import MapfSolutionError, compile_adg
from mapf_splice.domain import ActionKind, ActionRef, Cell


def test_adg_orders_later_vertex_entries_after_departures() -> None:
    paths = {
        "R1": (Cell(0, 0), Cell(0, 1), Cell(0, 2), Cell(0, 2)),
        "R2": (Cell(1, 0), Cell(0, 0), Cell(0, 1), Cell(0, 1)),
    }

    plans = compile_adg(
        paths,
        plan_versions={"R1": 4, "R2": 7},
        task_ids={"R1": "T1", "R2": "T2"},
    )

    assert len(plans["R1"].actions) == 2
    assert len(plans["R2"].actions) == 2
    assert all(
        action.kind is ActionKind.MOVE
        for plan in plans.values()
        for action in plan.actions
    )
    assert plans["R2"].actions[0].dependencies == (ActionRef("R1", 4, 0),)
    assert plans["R2"].actions[1].dependencies == (
        ActionRef("R1", 4, 1),
        ActionRef("R2", 7, 0),
    )


def test_every_later_action_keeps_its_natural_predecessor() -> None:
    plans = compile_adg(
        {
            "R1": (Cell(0, 0), Cell(0, 1), Cell(0, 2), Cell(0, 3)),
            "R2": (Cell(1, 0), Cell(0, 0), Cell(0, 1), Cell(0, 2)),
        },
        plan_versions={"R1": 2, "R2": 5},
        task_ids={"R1": "T1", "R2": "T2"},
    )
    for plan in plans.values():
        for index, action in enumerate(plan.actions[1:], start=1):
            predecessor = ActionRef(plan.robot_id, plan.version, index - 1)
            assert predecessor in action.dependencies
            assert action.dependencies == tuple(sorted(set(action.dependencies)))


def test_adg_rejects_opposite_edge_swap() -> None:
    paths = {
        "R1": (Cell(0, 0), Cell(0, 1)),
        "R2": (Cell(0, 1), Cell(0, 0)),
    }

    with pytest.raises(MapfSolutionError, match="opposite-edge swap"):
        compile_adg(
            paths,
            plan_versions={"R1": 1, "R2": 1},
            task_ids={"R1": "T1", "R2": "T2"},
        )


def test_adg_rejects_a_synchronized_rotation_that_cannot_be_serialized() -> None:
    paths = {
        "R1": (Cell(0, 0), Cell(0, 1)),
        "R2": (Cell(0, 1), Cell(1, 1)),
        "R3": (Cell(1, 1), Cell(1, 0)),
        "R4": (Cell(1, 0), Cell(0, 0)),
    }

    with pytest.raises(MapfSolutionError, match="contains a cycle"):
        compile_adg(
            paths,
            plan_versions={robot_id: 1 for robot_id in paths},
            task_ids={robot_id: f"T{index}" for index, robot_id in enumerate(paths)},
        )


def test_adg_requires_stay_at_goal_semantics() -> None:
    paths = {
        "R1": (Cell(0, 0), Cell(0, 1), Cell(0, 0), Cell(0, 1)),
    }

    with pytest.raises(MapfSolutionError, match="stay-at-goal"):
        compile_adg(
            paths,
            plan_versions={"R1": 1},
            task_ids={"R1": "T1"},
        )
