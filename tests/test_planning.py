import pytest

from mapf_splice.domain import ActionStatus, Cell, DomainError
from mapf_splice.planning import (
    compile_path,
    completed_prefix_length,
    next_required_action,
)


def _plan():
    return compile_path(
        (Cell(0, 0), Cell(0, 1), Cell(0, 2), Cell(0, 3)),
        robot_id="R1",
        plan_version=1,
        task_id="T1",
    )


def _complete(plan, index: int) -> None:
    plan.actions[index].transition_to(ActionStatus.RUNNING)
    plan.actions[index].transition_to(ActionStatus.COMPLETED)


def test_next_required_action_is_first_uncompleted() -> None:
    plan = _plan()
    _complete(plan, 0)
    assert next_required_action(plan) is plan.actions[1]
    assert completed_prefix_length(plan) == 1


def test_next_required_action_is_none_when_plan_complete() -> None:
    plan = _plan()
    for index in range(len(plan.actions)):
        _complete(plan, index)
    assert next_required_action(plan) is None
    assert completed_prefix_length(plan) == len(plan.actions)


def test_completed_prefix_rejects_noncontiguous_completions() -> None:
    plan = _plan()
    _complete(plan, 0)
    _complete(plan, 2)  # index 1 still planned -> illegal gap
    with pytest.raises(DomainError, match="sequential prefix"):
        completed_prefix_length(plan)
    with pytest.raises(DomainError, match="sequential prefix"):
        next_required_action(plan)
