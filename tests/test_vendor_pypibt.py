"""The vendored PIBT subset imports and solves a tiny instance deterministically.

numpy is an optional dependency (the ``recovery`` extra); skip if unavailable.
"""
import pytest

np = pytest.importorskip("numpy")


def _open_grid(height: int, width: int):
    return np.ones((height, width), dtype=bool)


def test_vendored_pibt_solves_small_swap() -> None:
    from mapf_splice._vendor.pypibt import PIBT

    grid = _open_grid(2, 5)
    # two agents that must pass each other; the second row lets them detour
    starts = [(0, 0), (0, 4)]
    goals = [(0, 4), (0, 0)]
    solution = PIBT(grid, starts, goals, seed=0).run(max_timestep=64)

    assert solution[0] == starts
    assert solution[-1] == goals


def test_vendored_pibt_is_deterministic_under_fixed_seed() -> None:
    from mapf_splice._vendor.pypibt import PIBT

    grid = _open_grid(3, 3)
    starts = [(0, 0), (2, 2)]
    goals = [(2, 2), (0, 0)]

    first = PIBT(grid, starts, goals, seed=0).run(max_timestep=32)
    second = PIBT(grid, starts, goals, seed=0).run(max_timestep=32)

    assert first == second
    assert first[-1] == goals
