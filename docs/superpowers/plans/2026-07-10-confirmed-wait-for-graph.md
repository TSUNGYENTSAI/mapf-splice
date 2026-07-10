# Confirmed Wait-for Graph & Containment Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a stable prospective SCC is contained and its scope reaches quiescence, build a confirmed wait-for graph from each member's first unfinished action, classify the outcome from authoritative traffic conflict semantics, and drive an explicit containment lifecycle — classify-and-record only.

**Architecture:** A new `confirm.py` holds facts-only graph dataclasses, a shared `cyclic_components` SCC utility, and `build_confirmed_wait_for`, which reuses `traffic.conflicts_for` (admission semantics) over each member's `planning.next_required_action`. `deadlock.py` replaces the `valid`/`quiescence_emitted` booleans with a `ContainmentState` machine + deterministic epoch, adds `confirm`/`prune_resolved`, and classifies. The simulator runs a dedicated `_confirm()` step in a new `CONFIRM_DEADLOCK` phase, recorded at a new `after-confirmation` checkpoint; the replay artifact bumps to `simulation-run.v0.2`.

**Tech Stack:** Python 3.11+, dataclasses (`slots=True`), `jsonschema` (Draft 2020-12), pytest, ruff, `uv`. Vanilla JS inspector.

## Global Constraints

- Python `>=3.11`; dependencies limited to `jsonschema>=4,<5`, `pillow>=10,<13` (no new runtime deps).
- Ruff lints `E, F, I, UP`, line-length 88, target py311. `uv run ruff check .` must pass.
- All work is TDD: failing test first, watch it fail, minimal code, watch it pass, commit.
- Determinism: no `Date`/random; epochs from a monotonic counter; all serialized collections deterministically ordered.
- Milestone is **classify-and-record only**: no MAPF (`adg.py`), no plan replacement/splice, no `install_plan` in the confirm path.
- Full gate before each commit that touches runtime: `uv run ruff check . && uv run pytest -q`.
- `cyclic_components` lives in `confirm.py` (not `deadlock.py` as the spec §8 said) to avoid a `deadlock` ↔ `confirm` circular import; `deadlock.cyclic_sccs` delegates to it. Layering: controller (`deadlock`) → graph module (`confirm`).
- Replay contract bumps `simulation-run.v0.1` → `simulation-run.v0.2` (breaking); prior artifacts are regenerated, no compatibility layer.

## File Structure

- `src/mapf_splice/planning.py` — add `completed_prefix_length`, `next_required_action`.
- `src/mapf_splice/traffic.py` — add public `conflicts_for`.
- `src/mapf_splice/confirm.py` — NEW: `cyclic_components`, `ConfirmationError`, `ConfirmedWaitForEdge`, `ConfirmedWaitForGraph`, `build_confirmed_wait_for`.
- `src/mapf_splice/deadlock.py` — `ContainmentState`, `ConfirmationOutcome`, `Containment` refactor, epoch counter, `classify_confirmation`, `confirm`, `prune_resolved`, revised `observe`/`refresh`/`newly_quiescent`/`is_contained`/`snapshot`, `cyclic_sccs` delegates to `confirm.cyclic_components`.
- `src/mapf_splice/trace.py` — `TickPhase.CONFIRM_DEADLOCK`, five new `EventKind`s.
- `src/mapf_splice/simulation.py` — adopt `next_required_action`; `prune_resolved` + `refresh` events + `_confirm()` + `after-confirmation` record.
- `src/mapf_splice/replay.py` — `after-confirmation` checkpoint, containment lifecycle fields, `confirmed_wait_for` envelope, v0.2 schema path.
- `schemas/simulation-run.v0.2.schema.json` — NEW.
- `pyproject.toml` — force-include v0.2 schema.
- `src/mapf_splice/web_inspector/{app.js,index.html,styles.css}` — second graph panel, bookmarks, state cards.
- Tests: `tests/test_planning.py` (NEW), `tests/test_confirm.py` (NEW), `tests/test_traffic.py`, `tests/test_deadlock.py`, `tests/test_replay.py`, `tests/test_simulation.py`, `tests/test_inspect.py`.

---

### Task 1: Plan-execution helpers (`next_required_action`)

**Files:**
- Modify: `src/mapf_splice/planning.py`
- Modify: `src/mapf_splice/simulation.py:305-318` (`_start_actions`)
- Test: `tests/test_planning.py` (create)

**Interfaces:**
- Produces: `completed_prefix_length(plan: Plan) -> int` (raises `DomainError` if completed actions are not a contiguous prefix); `next_required_action(plan: Plan) -> Action | None`.

- [ ] **Step 1: Write the failing test** — create `tests/test_planning.py`:

```python
import pytest

from mapf_splice.domain import ActionStatus, Cell, DomainError
from mapf_splice.planning import (
    compile_path,
    completed_prefix_length,
    next_required_action,
)


def _plan():
    return compile_path(
        (Cell(0, 0), Cell(0, 1), Cell(0, 2)),
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_planning.py -q`
Expected: FAIL — `ImportError: cannot import name 'completed_prefix_length'`.

- [ ] **Step 3: Add the helpers** — in `src/mapf_splice/planning.py`, add `ActionStatus` to the domain import and append:

```python
def completed_prefix_length(plan: Plan) -> int:
    """Count leading COMPLETED actions; the completed set must be a prefix."""
    prefix = 0
    while (
        prefix < len(plan.actions)
        and plan.actions[prefix].status is ActionStatus.COMPLETED
    ):
        prefix += 1
    if any(
        action.status is ActionStatus.COMPLETED for action in plan.actions[prefix:]
    ):
        raise DomainError("completed actions must form a sequential prefix")
    return prefix


def next_required_action(plan: Plan) -> Action | None:
    """The first action the robot has not COMPLETED, or None if the plan is done."""
    index = completed_prefix_length(plan)
    return plan.actions[index] if index < len(plan.actions) else None
```

The import line becomes:
```python
from mapf_splice.domain import (
    Action,
    ActionKind,
    ActionRef,
    ActionStatus,
    Cell,
    DomainError,
    Plan,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_planning.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Adopt in `_start_actions`** — in `src/mapf_splice/simulation.py`, import the helper and replace the inline scan.

Add to the planning import (top of file has no planning import yet; add one):
```python
from mapf_splice.planning import next_required_action
```
Replace `src/mapf_splice/simulation.py:311-318`:
```python
            action = next(
                (
                    candidate
                    for candidate in plan.actions
                    if candidate.status is not ActionStatus.COMPLETED
                ),
                None,
            )
```
with:
```python
            action = next_required_action(plan)
```

- [ ] **Step 6: Run the full suite (behavior-preserving)**

Run: `uv run ruff check . && uv run pytest -q`
Expected: PASS (all existing tests + new; 69 passed).

- [ ] **Step 7: Commit**

```bash
git add src/mapf_splice/planning.py src/mapf_splice/simulation.py tests/test_planning.py
git commit -m "feat(planning): add validated next_required_action and adopt in start_actions"
```

---

### Task 2: Public traffic conflict query

**Files:**
- Modify: `src/mapf_splice/traffic.py`
- Test: `tests/test_traffic.py`

**Interfaces:**
- Produces: `CommittedReservationLedger.conflicts_for(action: Action, *, occupied: Mapping[Cell, str]) -> tuple[ReservationConflict, ...]` — conflicts of `action` against the current committed state, self-plan excluded.

- [ ] **Step 1: Write the failing test** — append to `tests/test_traffic.py`:

```python
def test_conflicts_for_reports_committed_and_occupied_blockers() -> None:
    blocker = _plan((Cell(0, 1), Cell(0, 2)), robot_id="R2")
    ledger = CommittedReservationLedger(horizon=1)
    _initial(ledger, blocker, occupied={Cell(0, 1): "R2"})

    mover = _plan((Cell(0, 0), Cell(0, 1)), robot_id="R1")
    conflicts = ledger.conflicts_for(
        mover.actions[0], occupied={Cell(0, 1): "R2", Cell(0, 0): "R1"}
    )

    vertex = next(
        c for c in conflicts if c.resource == VertexResource(Cell(0, 1))
    )
    assert vertex.reserved_by == (ActionRef("R2", 1, 0),)
    assert vertex.occupied_by == "R2"


def test_conflicts_for_excludes_self_and_is_read_only() -> None:
    plan = _plan((Cell(0, 0), Cell(0, 1)), robot_id="R1")
    ledger = CommittedReservationLedger(horizon=1)
    _initial(ledger, plan, occupied={Cell(0, 0): "R1"})
    before = ledger.all_committed_actions()

    assert ledger.conflicts_for(plan.actions[0], occupied={Cell(0, 0): "R1"}) == ()
    assert ledger.all_committed_actions() == before
```

Add `Mapping` import at the top of `tests/test_traffic.py` is **not** needed (tests call with a dict literal).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_traffic.py -q -k conflicts_for`
Expected: FAIL — `AttributeError: 'CommittedReservationLedger' object has no attribute 'conflicts_for'`.

- [ ] **Step 3: Add the method** — in `src/mapf_splice/traffic.py`, add after `_conflicts_for`:

```python
    def conflicts_for(
        self,
        action: Action,
        *,
        occupied: Mapping[Cell, str],
    ) -> tuple[ReservationConflict, ...]:
        """Report an action's conflicts against current committed state (read-only)."""
        return self._conflicts_for(action, occupied, self._owners_by_resource)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_traffic.py -q`
Expected: PASS.

- [ ] **Step 5: Full gate + commit**

Run: `uv run ruff check . && uv run pytest -q`
```bash
git add src/mapf_splice/traffic.py tests/test_traffic.py
git commit -m "feat(traffic): expose public read-only conflicts_for query"
```

---

### Task 3: Shared SCC utility (`confirm.cyclic_components`)

**Files:**
- Create: `src/mapf_splice/confirm.py`
- Modify: `src/mapf_splice/deadlock.py` (make `cyclic_sccs` delegate; import from confirm)
- Test: `tests/test_confirm.py` (create)

**Interfaces:**
- Produces: `cyclic_components(edges: Iterable[tuple[str, str]]) -> tuple[tuple[str, ...], ...]` — Tarjan SCCs of size ≥ 2 over `waiting -> blocking` pairs, deterministically sorted.
- `deadlock.cyclic_sccs(analysis)` keeps its signature and return, now delegating.

- [ ] **Step 1: Write the failing test** — create `tests/test_confirm.py`:

```python
from mapf_splice.confirm import cyclic_components


def test_cyclic_components_finds_multi_node_cycles_only() -> None:
    assert cyclic_components([("R1", "R2"), ("R2", "R1")]) == (("R1", "R2"),)
    assert cyclic_components([("R1", "R2"), ("R2", "R3")]) == ()


def test_cyclic_components_is_deterministic_over_input_order() -> None:
    forward = cyclic_components([("R1", "R2"), ("R2", "R3"), ("R3", "R1")])
    reverse = cyclic_components([("R3", "R1"), ("R2", "R3"), ("R1", "R2")])
    assert forward == reverse == (("R1", "R2", "R3"),)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_confirm.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mapf_splice.confirm'`.

- [ ] **Step 3: Create `confirm.py` with the SCC utility** — move the Tarjan out of `deadlock.py`:

```python
from __future__ import annotations

from collections.abc import Iterable


def cyclic_components(
    edges: Iterable[tuple[str, str]],
) -> tuple[tuple[str, ...], ...]:
    """Tarjan SCCs of size >= 2 over waiting -> blocking edges, sorted."""
    graph: dict[str, set[str]] = {}
    for waiting, blocking in edges:
        graph.setdefault(waiting, set()).add(blocking)
        graph.setdefault(blocking, set())

    index = 0
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def connect(node: str) -> None:
        nonlocal index
        indexes[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in sorted(graph[node]):
            if neighbor not in indexes:
                connect(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[neighbor])
        if lowlinks[node] == indexes[node]:
            members: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                members.append(member)
                if member == node:
                    break
            component = tuple(sorted(members))
            if len(component) >= 2:
                components.append(component)

    for node in sorted(graph):
        if node not in indexes:
            connect(node)
    return tuple(sorted(components))
```

- [ ] **Step 4: Delegate `deadlock.cyclic_sccs`** — in `src/mapf_splice/deadlock.py`, replace the whole `cyclic_sccs` function body (lines 56-99) with a delegation, and add the import:

```python
from mapf_splice.confirm import cyclic_components
```
```python
def cyclic_sccs(analysis: PreviewAnalysis) -> tuple[tuple[str, ...], ...]:
    return cyclic_components(
        (dependency.waiting_robot_id, dependency.blocking_robot_id)
        for dependency in analysis.dependencies
    )
```

- [ ] **Step 5: Run tests (confirm + existing deadlock SCC tests)**

Run: `uv run pytest tests/test_confirm.py tests/test_deadlock.py -q`
Expected: PASS — existing `cyclic_sccs`-based tests still pass via delegation.

- [ ] **Step 6: Full gate + commit**

Run: `uv run ruff check . && uv run pytest -q`
```bash
git add src/mapf_splice/confirm.py src/mapf_splice/deadlock.py tests/test_confirm.py
git commit -m "refactor(deadlock): extract shared cyclic_components into confirm module"
```

---

### Task 4: Confirmed wait-for graph builder

**Files:**
- Modify: `src/mapf_splice/confirm.py`
- Test: `tests/test_confirm.py`

**Interfaces:**
- Produces:
  - `class ConfirmationError(ValueError)`
  - `ConfirmedWaitForEdge` (frozen): `waiting_robot_id, waiting_plan_version, waiting_action_ref, resource, blocking_robot_id, blocking_plan_version, committed_blocker_refs: tuple[ActionRef,...], occupied_blocker: bool, blocking_in_scope: bool`
  - `ConfirmedWaitForGraph` (frozen): `scope: tuple[tuple[str,int],...], epoch: int, captured_at_tick: int, edges: tuple[ConfirmedWaitForEdge,...], cyclic_sccs: tuple[tuple[str,...],...]`
  - `build_confirmed_wait_for(world, scope, *, epoch: int, tick: int) -> ConfirmedWaitForGraph`
- Consumes: `traffic.conflicts_for` (Task 2), `planning.next_required_action` (Task 1), `cyclic_components` (Task 3).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_confirm.py`. Shared world builder mirrors `tests/test_preview.py`:

```python
import pytest

from mapf_splice.confirm import (
    ConfirmationError,
    build_confirmed_wait_for,
)
from mapf_splice.domain import (
    ActionStatus,
    Cell,
    Robot,
    Task,
    TaskStatus,
    VertexResource,
)
from mapf_splice.planning import compile_path
from mapf_splice.traffic import CommittedReservationLedger
from mapf_splice.world import WorldState


def _world(routes: dict[str, tuple[Cell, ...]], *, admit: bool) -> WorldState:
    starts = {robot_id: route[0] for robot_id, route in routes.items()}
    robots = {
        robot_id: Robot(robot_id, start, active_task_id=f"T-{robot_id}")
        for robot_id, start in starts.items()
    }
    tasks = {
        f"T-{robot_id}": Task(
            f"T-{robot_id}", start, routes[robot_id][-1], 0,
            TaskStatus.ASSIGNED, robot_id,
        )
        for robot_id, start in starts.items()
    }
    world = WorldState(
        reservations=CommittedReservationLedger(horizon=1),
        robots=robots,
        tasks=tasks,
    )
    plans = []
    for robot_id in sorted(routes):
        plan = compile_path(
            routes[robot_id], robot_id=robot_id, plan_version=1,
            task_id=f"T-{robot_id}",
        )
        world.install_plan(plan)
        tasks[f"T-{robot_id}"].transition_to(TaskStatus.TO_PICKUP)
        plans.append(plan)
    if admit:
        world.reservations.acquire_initial_batch(plans, occupied=world.occupied_cells())
    return world


def test_confirmed_graph_records_mutual_occupancy_cycle() -> None:
    # R1 at (0,0) must move to (0,1) held by R2; R2 at (0,1) must move to (0,0).
    world = _world(
        {"R1": (Cell(0, 0), Cell(0, 1)), "R2": (Cell(0, 1), Cell(0, 0))},
        admit=False,
    )
    scope = (("R1", 1), ("R2", 1))
    graph = build_confirmed_wait_for(world, scope, epoch=1, tick=7)

    assert graph.epoch == 1 and graph.captured_at_tick == 7
    pairs = {(e.waiting_robot_id, e.blocking_robot_id) for e in graph.edges}
    assert pairs == {("R1", "R2"), ("R2", "R1")}
    assert graph.cyclic_sccs == (("R1", "R2"),)
    r1_to_r2 = next(
        e for e in graph.edges
        if e.waiting_robot_id == "R1" and e.blocking_robot_id == "R2"
    )
    assert r1_to_r2.occupied_blocker is True
    assert r1_to_r2.committed_blocker_refs == ()
    assert r1_to_r2.blocking_in_scope is True
    assert r1_to_r2.resource == VertexResource(Cell(0, 1))


def test_confirmed_graph_clears_when_next_cells_are_free() -> None:
    world = _world(
        {"R1": (Cell(0, 0), Cell(0, 1)), "R2": (Cell(2, 0), Cell(2, 1))},
        admit=False,
    )
    graph = build_confirmed_wait_for(
        world, (("R1", 1), ("R2", 1)), epoch=1, tick=3
    )
    assert graph.edges == ()
    assert graph.cyclic_sccs == ()


def test_confirmed_graph_marks_out_of_scope_blocker() -> None:
    # scope is {R1}; its target (0,1) is occupied by external R2 (not in scope).
    world = _world(
        {"R1": (Cell(0, 0), Cell(0, 1)), "R2": (Cell(0, 1), Cell(0, 2))},
        admit=False,
    )
    graph = build_confirmed_wait_for(world, (("R1", 1),), epoch=1, tick=4)
    edge = next(e for e in graph.edges if e.blocking_robot_id == "R2")
    assert edge.blocking_in_scope is False
    assert graph.cyclic_sccs == ()


def test_confirmed_graph_records_committed_blocker_refs() -> None:
    # Admit ONLY R2 so it holds the committed reservation on (0,1); R1 (sorted
    # first) would otherwise win arbitration for the shared cell.
    world = _world(
        {"R1": (Cell(0, 0), Cell(0, 1)), "R2": (Cell(0, 2), Cell(0, 1))},
        admit=False,
    )
    world.reservations.acquire_initial_batch(
        (world.plans["R2"],), occupied=world.occupied_cells()
    )
    graph = build_confirmed_wait_for(world, (("R1", 1),), epoch=2, tick=9)
    edge = next(
        e for e in graph.edges
        if e.resource == VertexResource(Cell(0, 1)) and e.blocking_robot_id == "R2"
    )
    assert edge.committed_blocker_refs != ()
    assert edge.occupied_blocker is False
    assert all(ref.plan_version >= 1 for ref in edge.committed_blocker_refs)


def test_confirmed_graph_excludes_self_ownership() -> None:
    # A WAIT action claims the robot's own cell; self must not become an edge.
    world = _world({"R1": (Cell(0, 0), Cell(0, 0))}, admit=False)
    graph = build_confirmed_wait_for(world, (("R1", 1),), epoch=1, tick=1)
    assert graph.edges == ()


def test_confirmed_builder_rejects_non_planned_next_action() -> None:
    world = _world({"R1": (Cell(0, 0), Cell(0, 1))}, admit=True)
    world.plans["R1"].actions[0].transition_to(ActionStatus.RUNNING)
    with pytest.raises(ConfirmationError, match="planned"):
        build_confirmed_wait_for(world, (("R1", 1),), epoch=1, tick=1)


def test_confirmed_graph_allows_zero_version_idle_occupancy_blocker() -> None:
    # R2 is idle (no task, plan_version 0) sitting on R1's target cell.
    world = _world({"R1": (Cell(0, 0), Cell(0, 1))}, admit=False)
    world.robots["R2"] = Robot("R2", Cell(0, 1))
    world.validate()
    graph = build_confirmed_wait_for(world, (("R1", 1),), epoch=1, tick=1)
    edge = next(e for e in graph.edges if e.blocking_robot_id == "R2")
    assert edge.blocking_plan_version == 0
    assert edge.occupied_blocker is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_confirm.py -q`
Expected: FAIL — `ImportError: cannot import name 'ConfirmationError'`.

- [ ] **Step 3: Implement the builder** — append to `src/mapf_splice/confirm.py`. Update the top imports:

```python
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from mapf_splice.domain import ActionRef, ActionStatus, Resource, VertexResource
from mapf_splice.planning import next_required_action
from mapf_splice.preview import resource_label
from mapf_splice.world import WorldState


class ConfirmationError(ValueError):
    """Raised when a confirmed wait-for graph cannot be built from valid state."""


@dataclass(frozen=True, slots=True)
class ConfirmedWaitForEdge:
    waiting_robot_id: str
    waiting_plan_version: int
    waiting_action_ref: ActionRef
    resource: Resource
    blocking_robot_id: str
    blocking_plan_version: int
    committed_blocker_refs: tuple[ActionRef, ...]
    occupied_blocker: bool
    blocking_in_scope: bool


@dataclass(frozen=True, slots=True)
class ConfirmedWaitForGraph:
    scope: tuple[tuple[str, int], ...]
    epoch: int
    captured_at_tick: int
    edges: tuple[ConfirmedWaitForEdge, ...]
    cyclic_sccs: tuple[tuple[str, ...], ...]


def build_confirmed_wait_for(
    world: WorldState,
    scope: tuple[tuple[str, int], ...],
    *,
    epoch: int,
    tick: int,
) -> ConfirmedWaitForGraph:
    """Build the authoritative wait-for graph for a quiescent containment scope."""
    scope_members = set(scope)
    occupied = world.occupied_cells()
    accumulator: dict[
        tuple[ActionRef, str, int, Resource], dict[str, object]
    ] = {}

    for robot_id, version in scope:
        plan = world.plans[robot_id]
        action = next_required_action(plan)
        if action is None:
            continue
        if action.status is not ActionStatus.PLANNED:
            raise ConfirmationError(
                "quiescent plan next required action must be planned"
            )
        for conflict in world.reservations.conflicts_for(action, occupied=occupied):
            for ref in conflict.reserved_by:
                key = (action.ref, ref.robot_id, ref.plan_version, conflict.resource)
                entry = accumulator.setdefault(key, {"committed": set(), "occupied": False})
                entry["committed"].add(ref)
            if conflict.occupied_by is not None:
                blocker = world.robots[conflict.occupied_by]
                key = (
                    action.ref,
                    conflict.occupied_by,
                    blocker.plan_version,
                    conflict.resource,
                )
                entry = accumulator.setdefault(key, {"committed": set(), "occupied": False})
                entry["occupied"] = True

    edges = tuple(
        sorted(
            (
                ConfirmedWaitForEdge(
                    waiting_robot_id=action_ref.robot_id,
                    waiting_plan_version=action_ref.plan_version,
                    waiting_action_ref=action_ref,
                    resource=resource,
                    blocking_robot_id=blocking_id,
                    blocking_plan_version=blocking_version,
                    committed_blocker_refs=tuple(sorted(entry["committed"])),
                    occupied_blocker=bool(entry["occupied"]),
                    blocking_in_scope=(blocking_id, blocking_version) in scope_members,
                )
                for (action_ref, blocking_id, blocking_version, resource), entry
                in accumulator.items()
            ),
            key=lambda edge: (
                edge.waiting_robot_id,
                edge.waiting_action_ref,
                edge.blocking_robot_id,
                resource_label(edge.resource),
            ),
        )
    )
    cyclic = cyclic_components(
        (edge.waiting_robot_id, edge.blocking_robot_id) for edge in edges
    )
    return ConfirmedWaitForGraph(
        scope=scope, epoch=epoch, captured_at_tick=tick, edges=edges, cyclic_sccs=cyclic
    )
```

Note: `VertexResource` import is retained for readability even though occupancy handling reads `conflict.occupied_by` directly. If ruff flags it as unused (F401), drop it from the import.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_confirm.py -q`
Expected: PASS (all confirm tests).

- [ ] **Step 5: Full gate + commit**

Run: `uv run ruff check . && uv run pytest -q`
```bash
git add src/mapf_splice/confirm.py tests/test_confirm.py
git commit -m "feat(confirm): build confirmed wait-for graph from authoritative conflicts"
```

---

### Task 5: Containment lifecycle state machine

**Files:**
- Modify: `src/mapf_splice/deadlock.py`
- Modify: `src/mapf_splice/replay.py:242-249` (containment serialization)
- Test: `tests/test_deadlock.py`

**Interfaces:**
- Produces:
  - `class ContainmentState(StrEnum)`: `DRAINING, QUIESCENT, CONFIRMED_DEADLOCK, EXTERNAL_BLOCKED, CLEARED, INVALIDATED`
  - `class ConfirmationOutcome(StrEnum)`: `CONFIRMED_DEADLOCK="confirmed-deadlock", EXTERNAL_DEPENDENCY="external-dependency", CLEAR="clear"`
  - `ACTIVE_STATES: frozenset[ContainmentState]`
  - `classify_confirmation(graph: ConfirmedWaitForGraph) -> ConfirmationOutcome`
  - `Containment(identity, epoch, state, confirmation_tick, outcome, confirmed_graph)`
  - `DeadlockController.confirm(world, tick) -> tuple[ConfirmationResult, ...]`, `.prune_resolved() -> None`
  - `ConfirmationResult(identity, epoch, graph, outcome, previous_state, state)`
  - `refresh(world) -> tuple[tuple[CandidateIdentity, int], ...]` (newly invalidated: identity, epoch)
  - `SccObservation` gains `suppressed: bool`
  - `ContainmentSnapshot(identity, epoch, state, confirmation_tick, outcome, confirmed_graph)`
- Consumes: `confirm.build_confirmed_wait_for`, `confirm.ConfirmedWaitForGraph` (Task 4).

- [ ] **Step 1: Write the failing lifecycle tests** — append to `tests/test_deadlock.py`. Add imports at top:

```python
from mapf_splice.confirm import ConfirmedWaitForGraph
from mapf_splice.deadlock import (
    ConfirmationOutcome,
    ContainmentState,
    DeadlockController,
    classify_confirmation,
    cyclic_sccs,
)
```

Reuse the `_world` builder pattern from `tests/test_confirm.py` — copy a local `_quiescent_world` helper into `tests/test_deadlock.py`:

```python
def _quiescent_scope(routes):
    """A controller with an already-quiescent containment over `routes`' robots.

    (compile_path is already imported at the top of this module.)
    """
    starts = {robot_id: route[0] for robot_id, route in routes.items()}
    robots = {
        robot_id: Robot(robot_id, start, active_task_id=f"T-{robot_id}")
        for robot_id, start in starts.items()
    }
    tasks = {
        f"T-{robot_id}": Task(
            f"T-{robot_id}", start, routes[robot_id][-1], 0,
            TaskStatus.ASSIGNED, robot_id,
        )
        for robot_id, start in starts.items()
    }
    world = WorldState(
        reservations=CommittedReservationLedger(1), robots=robots, tasks=tasks
    )
    for robot_id in sorted(routes):
        plan = compile_path(
            routes[robot_id], robot_id=robot_id, plan_version=1,
            task_id=f"T-{robot_id}",
        )
        world.install_plan(plan)
        tasks[f"T-{robot_id}"].transition_to(TaskStatus.TO_PICKUP)
    controller = DeadlockController(1)
    scope = tuple((robot_id, 1) for robot_id in sorted(routes))
    edges = tuple(
        (a, b) for a in sorted(routes) for b in sorted(routes) if a != b
    )
    controller.observe(_analysis(*edges), {robot_id: 1 for robot_id in routes})
    controller.refresh(world)
    controller.newly_quiescent(world)
    return controller, world, scope


def test_confirm_marks_hard_deadlock_on_internal_cycle() -> None:
    controller, world, scope = _quiescent_scope(
        {"R1": (Cell(0, 0), Cell(0, 1)), "R2": (Cell(0, 1), Cell(0, 0))}
    )
    results = controller.confirm(world, tick=18)
    assert len(results) == 1
    assert results[0].outcome is ConfirmationOutcome.CONFIRMED_DEADLOCK
    assert controller.containments[0].state is ContainmentState.CONFIRMED_DEADLOCK
    assert controller.containments[0].confirmation_tick == 18


def test_confirm_clears_when_graph_is_acyclic_and_local() -> None:
    controller, world, scope = _quiescent_scope(
        {"R1": (Cell(0, 0), Cell(0, 1)), "R2": (Cell(2, 0), Cell(2, 1))}
    )
    results = controller.confirm(world, tick=12)
    assert results[0].outcome is ConfirmationOutcome.CLEAR
    assert controller.containments[0].state is ContainmentState.CLEARED


def test_cleared_containment_is_pruned_and_counts_reset() -> None:
    controller, world, scope = _quiescent_scope(
        {"R1": (Cell(0, 0), Cell(0, 1)), "R2": (Cell(2, 0), Cell(2, 1))}
    )
    controller.confirm(world, tick=12)
    controller.prune_resolved()
    assert controller.containments == ()
    # re-accumulation restarts at 1, not at the pre-clear count
    update = controller.observe(_analysis(("R1", "R2"), ("R2", "R1")), {"R1": 1, "R2": 1})
    assert update.observations[0].count == 1


def test_external_blocked_holds_then_reevaluates_to_clear() -> None:
    # A 2-robot scope (so a cyclic SCC can form the containment), but the
    # confirmed graph has no internal cycle -- only R1 blocked by external R3.
    controller, world, scope = _quiescent_scope(
        {"R1": (Cell(0, 0), Cell(0, 1)), "R2": (Cell(5, 0), Cell(5, 1))}
    )
    world.robots["R3"] = Robot("R3", Cell(0, 1))  # external blocker on R1's target
    world.validate()
    first = controller.confirm(world, tick=5)
    assert first[0].outcome is ConfirmationOutcome.EXTERNAL_DEPENDENCY
    assert controller.containments[0].state is ContainmentState.EXTERNAL_BLOCKED
    # external robot leaves; re-evaluation clears
    world.robots["R3"].position = Cell(9, 9)
    world.validate()
    second = controller.confirm(world, tick=6)
    assert second[0].outcome is ConfirmationOutcome.CLEAR
    assert controller.containments[0].state is ContainmentState.CLEARED


def test_overlapping_scc_does_not_accumulate_while_suppressed() -> None:
    controller = DeadlockController(2)
    controller.observe(_analysis(("R1", "R2"), ("R2", "R1")), {"R1": 1, "R2": 1})
    controller.observe(_analysis(("R1", "R2"), ("R2", "R1")), {"R1": 1, "R2": 1})
    assert controller.containments[0].state is ContainmentState.DRAINING
    superset = _analysis(("R1", "R2"), ("R2", "R3"), ("R3", "R1"))
    versions = {"R1": 1, "R2": 1, "R3": 1}
    first = controller.observe(superset, versions)
    second = controller.observe(superset, versions)
    superset_obs = [
        o for o in second.observations
        if o.identity == (("R1", 1), ("R2", 1), ("R3", 1))
    ][0]
    assert superset_obs.suppressed is True
    assert superset_obs.count == 0


def test_classify_confirmation_prefers_internal_cycle() -> None:
    graph = ConfirmedWaitForGraph(
        scope=(("R1", 1), ("R2", 1)), epoch=1, captured_at_tick=0, edges=(),
        cyclic_sccs=(("R1", "R2"),),
    )
    assert classify_confirmation(graph) is ConfirmationOutcome.CONFIRMED_DEADLOCK
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_deadlock.py -q -k "confirm or cleared or external or overlapping or classify"`
Expected: FAIL — `ImportError: cannot import name 'ContainmentState'`.

- [ ] **Step 3: Rewrite the controller** — in `src/mapf_splice/deadlock.py`:

Add imports:
```python
from enum import StrEnum

from mapf_splice.confirm import (
    ConfirmedWaitForGraph,
    build_confirmed_wait_for,
    cyclic_components,
)
```

Add enums + helpers (after the type aliases):
```python
class ContainmentState(StrEnum):
    DRAINING = "draining"
    QUIESCENT = "quiescent"
    CONFIRMED_DEADLOCK = "confirmed-deadlock"
    EXTERNAL_BLOCKED = "external-blocked"
    CLEARED = "cleared"
    INVALIDATED = "invalidated"


class ConfirmationOutcome(StrEnum):
    CONFIRMED_DEADLOCK = "confirmed-deadlock"
    EXTERNAL_DEPENDENCY = "external-dependency"
    CLEAR = "clear"


ACTIVE_STATES = frozenset(
    {
        ContainmentState.DRAINING,
        ContainmentState.QUIESCENT,
        ContainmentState.CONFIRMED_DEADLOCK,
        ContainmentState.EXTERNAL_BLOCKED,
    }
)

_OUTCOME_STATE = {
    ConfirmationOutcome.CONFIRMED_DEADLOCK: ContainmentState.CONFIRMED_DEADLOCK,
    ConfirmationOutcome.EXTERNAL_DEPENDENCY: ContainmentState.EXTERNAL_BLOCKED,
    ConfirmationOutcome.CLEAR: ContainmentState.CLEARED,
}


def classify_confirmation(graph: ConfirmedWaitForGraph) -> ConfirmationOutcome:
    if graph.cyclic_sccs:
        return ConfirmationOutcome.CONFIRMED_DEADLOCK
    if any(not edge.blocking_in_scope for edge in graph.edges):
        return ConfirmationOutcome.EXTERNAL_DEPENDENCY
    return ConfirmationOutcome.CLEAR
```

Replace the `Containment` dataclass:
```python
@dataclass(slots=True)
class Containment:
    identity: CandidateIdentity
    epoch: int
    state: ContainmentState = ContainmentState.DRAINING
    confirmation_tick: int | None = None
    outcome: ConfirmationOutcome | None = None
    confirmed_graph: ConfirmedWaitForGraph | None = None
```

Update `SccObservation`:
```python
@dataclass(frozen=True, slots=True)
class SccObservation:
    identity: CandidateIdentity
    count: int
    evidence: tuple[ProspectiveDependency, ...]
    suppressed: bool = False
```

Add the result dataclass and replace the containment snapshot:
```python
@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    identity: CandidateIdentity
    epoch: int
    graph: ConfirmedWaitForGraph
    outcome: ConfirmationOutcome
    previous_state: ContainmentState
    state: ContainmentState


@dataclass(frozen=True, slots=True)
class ContainmentSnapshot:
    identity: CandidateIdentity
    epoch: int
    state: ContainmentState
    confirmation_tick: int | None
    outcome: ConfirmationOutcome | None
    confirmed_graph: ConfirmedWaitForGraph | None
```

Add `_epoch_counter` to the controller fields:
```python
    _epoch_counter: int = field(default=0, init=False)
```

Replace `cyclic_sccs` to delegate (already done in Task 3 — leave as is).

Rewrite `snapshot`:
```python
    def snapshot(self) -> DeadlockControllerSnapshot:
        """Serialize current state read-only; callers refresh() beforehand."""
        return DeadlockControllerSnapshot(
            threshold=self.stable_scc_observation_threshold,
            candidates=tuple(
                DeadlockCandidateSnapshot(
                    identity=identity,
                    observation_count=count,
                    stable=identity in self._containments,
                )
                for identity, count in sorted(self._counts.items())
            ),
            containments=tuple(
                ContainmentSnapshot(
                    identity=containment.identity,
                    epoch=containment.epoch,
                    state=containment.state,
                    confirmation_tick=containment.confirmation_tick,
                    outcome=containment.outcome,
                    confirmed_graph=containment.confirmed_graph,
                )
                for identity, containment in sorted(self._containments.items())
            ),
        )
```

Rewrite `observe` with suppression + epoch:
```python
    def _active_members(
        self, exclude: CandidateIdentity | None = None
    ) -> set[PlanMember]:
        return {
            member
            for identity, containment in self._containments.items()
            if containment.state in ACTIVE_STATES and identity != exclude
            for member in identity
        }

    def observe(self, analysis, plan_versions):
        current: dict[CandidateIdentity, tuple[ProspectiveDependency, ...]] = {}
        for members in cyclic_sccs(analysis):
            identity = tuple((robot_id, plan_versions[robot_id]) for robot_id in members)
            evidence = tuple(
                dependency
                for dependency in analysis.dependencies
                if dependency.waiting_robot_id in members
                and dependency.blocking_robot_id in members
            )
            current[identity] = evidence

        expired = tuple(sorted(set(self._counts) - set(current)))
        for identity in expired:
            del self._counts[identity]

        stable: list[CandidateIdentity] = []
        observations: list[SccObservation] = []
        for identity in sorted(current):
            overlap = set(identity) & self._active_members(exclude=identity)
            if overlap:
                self._counts.pop(identity, None)
                observations.append(
                    SccObservation(identity, 0, current[identity], suppressed=True)
                )
                continue
            count = self._counts.get(identity, 0) + 1
            self._counts[identity] = count
            observations.append(SccObservation(identity, count, current[identity]))
            if (
                count >= self.stable_scc_observation_threshold
                and identity not in self._containments
            ):
                self._epoch_counter += 1
                self._containments[identity] = Containment(identity, self._epoch_counter)
                stable.append(identity)
        return DeadlockUpdate(tuple(observations), tuple(stable), expired)
```

Rewrite `refresh` to transition + return invalidated:
```python
    def refresh(self, world: WorldState) -> tuple[tuple[CandidateIdentity, int], ...]:
        invalidated: list[tuple[CandidateIdentity, int]] = []
        for identity in sorted(self._containments):
            containment = self._containments[identity]
            if containment.state not in ACTIVE_STATES:
                continue
            if any(
                robot_id not in world.robots
                or world.robots[robot_id].plan_version != version
                or robot_id not in world.plans
                or world.plans[robot_id].version != version
                for robot_id, version in containment.identity
            ):
                containment.state = ContainmentState.INVALIDATED
                invalidated.append((identity, containment.epoch))
        return tuple(invalidated)
```

Rewrite `is_contained`:
```python
    def is_contained(self, plan: Plan) -> bool:
        member = (plan.robot_id, plan.version)
        return any(
            containment.state in ACTIVE_STATES and member in containment.identity
            for containment in self._containments.values()
        )
```

Rewrite `newly_quiescent` (DRAINING → QUIESCENT):
```python
    def newly_quiescent(self, world: WorldState) -> tuple[CandidateIdentity, ...]:
        results: list[CandidateIdentity] = []
        for identity in sorted(self._containments):
            containment = self._containments[identity]
            if containment.state is not ContainmentState.DRAINING:
                continue
            if self._is_quiescent(world, identity):
                containment.state = ContainmentState.QUIESCENT
                results.append(identity)
        return tuple(results)

    @staticmethod
    def _is_quiescent(world: WorldState, identity: CandidateIdentity) -> bool:
        for robot_id, version in identity:
            robot = world.robots[robot_id]
            plan = world.plans.get(robot_id)
            if (
                robot.plan_version != version
                or robot.active_action_ref is not None
                or world.reservations.committed_actions(robot_id, version)
                or plan is None
                or any(
                    action.status is ActionStatus.RUNNING for action in plan.actions
                )
            ):
                return False
        return True
```

Add `confirm` and `prune_resolved`:
```python
    def confirm(self, world: WorldState, tick: int) -> tuple[ConfirmationResult, ...]:
        results: list[ConfirmationResult] = []
        for identity in sorted(self._containments):
            containment = self._containments[identity]
            if containment.state not in (
                ContainmentState.QUIESCENT,
                ContainmentState.EXTERNAL_BLOCKED,
            ):
                continue
            graph = build_confirmed_wait_for(
                world, containment.identity, epoch=containment.epoch, tick=tick
            )
            outcome = classify_confirmation(graph)
            previous = containment.state
            containment.state = _OUTCOME_STATE[outcome]
            containment.outcome = outcome
            containment.confirmation_tick = tick
            containment.confirmed_graph = graph
            results.append(
                ConfirmationResult(
                    identity, containment.epoch, graph, outcome, previous,
                    containment.state,
                )
            )
        return tuple(results)

    def prune_resolved(self) -> None:
        for identity in list(self._containments):
            if self._containments[identity].state in (
                ContainmentState.CLEARED,
                ContainmentState.INVALIDATED,
            ):
                del self._containments[identity]
                self._counts.pop(identity, None)
```

- [ ] **Step 4: Update `replay.py` containment serialization** — replace `src/mapf_splice/replay.py:242-249`:

```python
                    "containments": [
                        {
                            "identity": _identity(item.identity),
                            "epoch": item.epoch,
                            "state": item.state.value,
                            "confirmation_tick": item.confirmation_tick,
                            "outcome": item.outcome.value if item.outcome else None,
                        }
                        for item in controller_state.containments
                    ],
```

- [ ] **Step 5: Fix the two existing tests that referenced the old booleans** — in `tests/test_deadlock.py`, `test_new_plan_version_is_not_captured_by_stale_containment`:

```python
    controller.refresh(world)

    assert not controller.is_contained(replacement)
    assert controller.containments[0].state is ContainmentState.INVALIDATED
```

and `test_snapshot_is_read_only_and_refresh_is_explicit`:

```python
    world, controller, _ = _stale_containment_world()

    assert controller.snapshot().containments[0].state is ContainmentState.DRAINING
    assert controller.containments[0].state is ContainmentState.DRAINING

    controller.refresh(world)
    assert controller.containments[0].state is ContainmentState.INVALIDATED
    assert (
        controller.snapshot().containments[0].state is ContainmentState.INVALIDATED
    )
```

In `tests/test_replay.py`, `test_runtime_evidence_and_containment_are_recorded_at_source`, replace the `["valid"]` assertion:

```python
    assert stable["deadlock"]["containments"][0]["state"] == "draining"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_deadlock.py tests/test_replay.py -q`
Expected: PASS.

- [ ] **Step 7: Full gate + commit**

Run: `uv run ruff check . && uv run pytest -q`
Expected: PASS — the simulator still uses only `is_contained`/`newly_quiescent`/`refresh`/`observe`, whose observable behavior is preserved.
```bash
git add src/mapf_splice/deadlock.py src/mapf_splice/replay.py tests/test_deadlock.py tests/test_replay.py
git commit -m "feat(deadlock): containment lifecycle state machine with confirm and prune"
```

---

### Task 6: Trace phase + event kinds

**Files:**
- Modify: `src/mapf_splice/trace.py`
- Test: `tests/test_simulation.py` (a small enum-presence check folded into Task 7; here just add the members)

**Interfaces:**
- Produces: `TickPhase.CONFIRM_DEADLOCK`; `EventKind.{CONFIRMED_WAIT_FOR_BUILT, HARD_DEADLOCK_CONFIRMED, CONTAINMENT_EXTERNAL_BLOCKED, CONTAINMENT_CLEARED, CONTAINMENT_INVALIDATED}`.

- [ ] **Step 1: Add the phase** — in `src/mapf_splice/trace.py`, renumber to insert `CONFIRM_DEADLOCK` before `APPEND_EVENTS`:

```python
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
```

- [ ] **Step 2: Add the event kinds** — append to `EventKind`:

```python
    CONFIRMED_WAIT_FOR_BUILT = "confirmed-wait-for-built"
    HARD_DEADLOCK_CONFIRMED = "hard-deadlock-confirmed"
    CONTAINMENT_EXTERNAL_BLOCKED = "containment-external-blocked"
    CONTAINMENT_CLEARED = "containment-cleared"
    CONTAINMENT_INVALIDATED = "containment-invalidated"
```

- [ ] **Step 3: Sanity run**

Run: `uv run ruff check . && uv run pytest -q`
Expected: PASS (no behavior change; enums unused so far).

- [ ] **Step 4: Commit**

```bash
git add src/mapf_splice/trace.py
git commit -m "feat(trace): add confirm-deadlock phase and confirmation event kinds"
```

---

### Task 7: Simulator + replay + schema integration

**Files:**
- Modify: `src/mapf_splice/simulation.py` (`tick`, add `_confirm`, invalidation events)
- Modify: `src/mapf_splice/replay.py` (`CHECKPOINTS`, `confirmed_wait_for` envelope, schema path)
- Create: `schemas/simulation-run.v0.2.schema.json`
- Modify: `pyproject.toml` (force-include v0.2)
- Test: `tests/test_simulation.py`, `tests/test_replay.py`

**Interfaces:**
- Consumes: `DeadlockController.confirm/refresh/prune_resolved` (Task 5); `ConfirmationResult`; the new event kinds (Task 6).
- Produces: `after-confirmation` checkpoint; frame field `confirmed_wait_for: list[envelope]`.

- [ ] **Step 1: Write the failing integration test** — append to `tests/test_replay.py`:

```python
from mapf_splice.replay import CHECKPOINTS as _CP  # noqa: F401 (illustrative)


def test_after_confirmation_checkpoint_present_and_schema_v2() -> None:
    _, artifact = _recorded(2)
    assert "after-confirmation" in artifact["checkpoint_names"]
    assert artifact["schema_version"] == "simulation-run.v0.2"
    assert artifact["$schema"] == "simulation-run.v0.2.schema.json"
    assert [frame["checkpoint"] for frame in artifact["frames"][:8]] == list(CHECKPOINTS)
    for frame in artifact["frames"]:
        assert isinstance(frame["confirmed_wait_for"], list)
    validate_replay(artifact)
```

Append to `tests/test_simulation.py`:

```python
def test_tick_runs_confirm_step_after_preview() -> None:
    simulator = _simulator()
    simulator.tick()
    phases = [event.phase for event in simulator.trace.events if event.tick == 0]
    assert phases == sorted(phases)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_replay.py -q -k after_confirmation`
Expected: FAIL — `KeyError: 'confirmed_wait_for'` / `schema_version` mismatch.

- [ ] **Step 3: Create the v0.2 schema** — copy `schemas/simulation-run.v0.1.schema.json` to `schemas/simulation-run.v0.2.schema.json` and apply:
  - `$id` / `$schema` const → `simulation-run.v0.2.schema.json`; `schema_version` const → `simulation-run.v0.2`.
  - `checkpoint_names` `minItems` 7 → 8.
  - Add `"confirmed_wait_for"` to the `frame` `required` array and to `frame.properties`:
    ```json
    "confirmed_wait_for": {"type": "array", "items": {"$ref": "#/$defs/confirmed_graph"}}
    ```
  - Add `$defs.confirmed_graph`, `$defs.confirmed_edge`, and tighten the containment entry. Insert into `$defs`:
    ```json
    "action_ref": {"type": "object", "additionalProperties": false, "required": ["robot_id", "plan_version", "action_index", "label"], "properties": {"robot_id": {"type": "string"}, "plan_version": {"type": "integer", "minimum": 1}, "action_index": {"type": "integer", "minimum": 0}, "label": {"type": "string"}}},
    "confirmed_edge": {"type": "object", "additionalProperties": false, "required": ["waiting_robot_id", "waiting_plan_version", "waiting_action_ref", "resource", "blocking_robot_id", "blocking_plan_version", "committed_blocker_refs", "occupied_blocker", "blocking_in_scope"], "properties": {"waiting_robot_id": {"type": "string"}, "waiting_plan_version": {"type": "integer", "minimum": 1}, "waiting_action_ref": {"$ref": "#/$defs/action_ref"}, "resource": {"type": "object"}, "blocking_robot_id": {"type": "string"}, "blocking_plan_version": {"type": "integer", "minimum": 0}, "committed_blocker_refs": {"type": "array", "items": {"$ref": "#/$defs/action_ref"}}, "occupied_blocker": {"type": "boolean"}, "blocking_in_scope": {"type": "boolean"}}},
    "confirmed_graph": {"type": "object", "additionalProperties": false, "required": ["scope", "epoch", "captured_at_tick", "outcome", "state", "edges", "cyclic_sccs"], "properties": {"scope": {"type": "array"}, "epoch": {"type": "integer", "minimum": 1}, "captured_at_tick": {"type": "integer", "minimum": 0}, "outcome": {"enum": ["confirmed-deadlock", "external-dependency", "clear"]}, "state": {"enum": ["draining", "quiescent", "confirmed-deadlock", "external-blocked", "cleared", "invalidated"]}, "edges": {"type": "array", "items": {"$ref": "#/$defs/confirmed_edge"}}, "cyclic_sccs": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}}}
    ```

- [ ] **Step 4: Point replay at v0.2 + serialize the envelope** — in `src/mapf_splice/replay.py`:

Add the checkpoint:
```python
CHECKPOINTS = (
    "tick-start",
    "after-completions",
    "after-release",
    "after-task-advance",
    "after-admission",
    "after-action-start",
    "after-preview",
    "after-confirmation",
)
```

Add serialization helpers near `_event`:
```python
def _confirmed_edge(edge) -> dict[str, Any]:
    return {
        "waiting_robot_id": edge.waiting_robot_id,
        "waiting_plan_version": edge.waiting_plan_version,
        "waiting_action_ref": _ref(edge.waiting_action_ref),
        "resource": _resource(edge.resource),
        "blocking_robot_id": edge.blocking_robot_id,
        "blocking_plan_version": edge.blocking_plan_version,
        "committed_blocker_refs": [_ref(ref) for ref in edge.committed_blocker_refs],
        "occupied_blocker": edge.occupied_blocker,
        "blocking_in_scope": edge.blocking_in_scope,
    }


def _confirmed_graph(containment) -> dict[str, Any]:
    graph = containment.confirmed_graph
    return {
        "scope": _identity(graph.scope),
        "epoch": graph.epoch,
        "captured_at_tick": graph.captured_at_tick,
        "outcome": containment.outcome.value if containment.outcome else None,
        "state": containment.state.value,
        "edges": [_confirmed_edge(edge) for edge in graph.edges],
        "cyclic_sccs": [list(scc) for scc in graph.cyclic_sccs],
    }
```

In `record`, build the frame field (add to the frame dict, e.g. right after `"deadlock": {...}`):
```python
                "confirmed_wait_for": [
                    _confirmed_graph(containment)
                    for containment in controller_state.containments
                    if containment.confirmed_graph is not None
                ],
```

Update `artifact` to emit v0.2 consts:
```python
            "$schema": "simulation-run.v0.2.schema.json",
            "schema_version": "simulation-run.v0.2",
```

Point `_schema()` at v0.2:
```python
    path = Path(__file__).parents[2] / "schemas" / "simulation-run.v0.2.schema.json"
    ...
        .joinpath("schemas/simulation-run.v0.2.schema.json")
```

- [ ] **Step 5: Wire the simulator tick** — in `src/mapf_splice/simulation.py`:

Add to imports:
```python
from mapf_splice.deadlock import (
    CandidateIdentity,
    ConfirmationOutcome,
    ConfirmationResult,
    ContainmentState,
    DeadlockController,
    DeadlockUpdate,
)
```

Replace `tick`:
```python
    def tick(self) -> None:
        self.deadlock_controller.prune_resolved()
        self._record("tick-start")
        due = self._complete_due_actions()
        self._record("after-completions")
        self._release_completed(due)
        self._record("after-release")
        self._advance_tasks()
        self._emit_invalidations(self.deadlock_controller.refresh(self.world))
        self._record("after-task-advance")
        self._admit()
        self._record("after-admission")
        self._start_actions()
        self._record("after-action-start")
        analysis, update = self._preview()
        self._record(
            "after-preview", preview_analysis=analysis, deadlock_update=update
        )
        self._confirm()
        self._record("after-confirmation")
        self.trace.append(
            tick=self.world.tick,
            phase=TickPhase.ADVANCE_TICK,
            kind=EventKind.TICK_ADVANCED,
            details=(("next_tick", self.world.tick + 1),),
        )
        self.world.tick += 1
```

Add the two helpers:
```python
    def _emit_invalidations(
        self, invalidated: tuple[tuple[CandidateIdentity, int], ...]
    ) -> None:
        for identity, epoch in invalidated:
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.ADVANCE_TASKS,
                kind=EventKind.CONTAINMENT_INVALIDATED,
                details=(
                    ("members", self._identity_label(identity)),
                    ("epoch", epoch),
                ),
            )

    def _confirm(self) -> None:
        _transition_events = {
            ContainmentState.CONFIRMED_DEADLOCK: EventKind.HARD_DEADLOCK_CONFIRMED,
            ContainmentState.EXTERNAL_BLOCKED: EventKind.CONTAINMENT_EXTERNAL_BLOCKED,
            ContainmentState.CLEARED: EventKind.CONTAINMENT_CLEARED,
        }
        for result in self.deadlock_controller.confirm(self.world, self.world.tick):
            self.trace.append(
                tick=self.world.tick,
                phase=TickPhase.CONFIRM_DEADLOCK,
                kind=EventKind.CONFIRMED_WAIT_FOR_BUILT,
                details=(
                    ("members", self._identity_label(result.identity)),
                    ("epoch", result.epoch),
                    ("outcome", result.outcome.value),
                    ("edges", len(result.graph.edges)),
                ),
            )
            if result.state is not result.previous_state:
                self.trace.append(
                    tick=self.world.tick,
                    phase=TickPhase.CONFIRM_DEADLOCK,
                    kind=_transition_events[result.state],
                    details=(
                        ("members", self._identity_label(result.identity)),
                        ("epoch", result.epoch),
                    ),
                )
```

(`ConfirmationOutcome` import may be unused in simulation.py — drop it if ruff F401 flags it.)

- [ ] **Step 6: Update `pyproject.toml`** — change the wheel force-include:

```toml
[tool.hatch.build.targets.wheel.force-include]
"schemas/simulation-run.v0.2.schema.json" = "mapf_splice/schemas/simulation-run.v0.2.schema.json"
```

- [ ] **Step 7: Update the frame-order assertion** — in `tests/test_replay.py`, `test_replay_contains_topology_and_ordered_full_snapshots`, change the slice from `[:7]` to `[:8]` (CHECKPOINTS now has 8 entries):

```python
    assert [frame["checkpoint"] for frame in artifact["frames"][:8]] == list(
        CHECKPOINTS
    )
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_replay.py tests/test_simulation.py -q`
Expected: PASS. If `test_recording_is_read_only_and_deterministic` fails, confirm `_confirm` mutates only controller state already covered by the snapshot (it does — `confirm` runs before `after-confirmation` record) and that determinism holds across runs.

- [ ] **Step 9: Full gate + commit**

Run: `uv run ruff check . && uv run pytest -q`
```bash
git add src/mapf_splice/simulation.py src/mapf_splice/replay.py schemas/simulation-run.v0.2.schema.json pyproject.toml tests/test_replay.py tests/test_simulation.py
git commit -m "feat(replay): confirm-deadlock tick step, after-confirmation frame, schema v0.2"
```

---

### Task 8: Inspector second graph

**Files:**
- Modify: `src/mapf_splice/web_inspector/app.js`, `index.html`, `styles.css`
- Test: `tests/test_inspect.py`

**Interfaces:**
- Consumes: v0.2 replay with `frame.confirmed_wait_for` and lifecycle `state` on containments.

- [ ] **Step 1: Write the failing server test** — append to `tests/test_inspect.py`:

```python
def test_inspector_serves_confirmed_wait_for(tmp_path: Path) -> None:
    artifact = _artifact()
    for frame in artifact["frames"]:
        assert "confirmed_wait_for" in frame
    replay_path = tmp_path / "run.json"
    replay_path.write_text(replay_json(artifact), encoding="utf-8")
    server = create_server(replay_path)  # validates against v0.2
    server.server_close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_inspect.py -q -k confirmed`
Expected: FAIL if `_artifact()`'s single-tick run predates Task 7 wiring; otherwise it asserts the field exists. (If Task 7 is complete this may already pass — that is acceptable; proceed.)

- [ ] **Step 3: Add a confirmed-graph panel** — in `index.html`, add a panel next to the existing graph (find the `<div id="graph">` block and add a sibling):

```html
<section class="panel">
  <h2>Confirmed wait-for graph</h2>
  <div id="confirmedMeta" class="muted">No confirmed graph on this frame.</div>
  <div id="confirmedGraph"></div>
</section>
```

- [ ] **Step 4: Render it** — in `app.js`, add a `renderConfirmed(frame)` function and call it from `render()`:

```javascript
function renderConfirmed(frame){
  const graphs=frame.confirmed_wait_for||[];
  if(!graphs.length){
    $('confirmedMeta').textContent='No confirmed graph on this frame.';
    $('confirmedGraph').innerHTML='';
    return;
  }
  const g=graphs[0];
  $('confirmedMeta').textContent=`Confirmed at tick ${g.captured_at_tick} · ${g.outcome} · state ${g.state}`;
  const positions={};
  const robots=[...new Set(g.edges.flatMap(e=>[e.waiting_robot_id,e.blocking_robot_id]))].sort();
  robots.forEach((r,i)=>positions[r]=[30+i*80,76]);
  let svg=robots.map(r=>`<circle cx="${positions[r][0]}" cy="76" r="14" fill="${robotColor(r)}"/><text x="${positions[r][0]}" y="80" text-anchor="middle" fill="#fff" font-size="10">${esc(r)}</text>`).join('');
  g.edges.forEach(e=>{const a=positions[e.waiting_robot_id],b=positions[e.blocking_robot_id];if(a&&b)svg+=`<path d="M${a[0]},${a[1]}L${b[0]},${b[1]}" stroke="${e.occupied_blocker?'#f59e0b':'#a855f7'}" stroke-width="2" stroke-dasharray="${e.blocking_in_scope?'none':'3 3'}" marker-end="url(#g-arrow)"/>`;});
  $('confirmedGraph').innerHTML=`<svg viewBox="0 0 320 152">${svg}</svg>`;
}
```

Call it inside `render()` (add `renderConfirmed(frame);`). Update the bookmark categories on line 15 to include the confirmation events:

```javascript
['Confirmed','confirmed-wait-for-built'],['Hard deadlock','hard-deadlock-confirmed'],['Cleared','containment-cleared']
```

Update the SCC card line (`renderGraph`) to show `state` — replace the `containment?.valid?...` fragment with `containment?containment.state:''` and drop `quiescence_emitted`.

- [ ] **Step 5: Run the inspector to verify visually** — build the confirmed hero run and open the inspector:

```bash
uv run mapf-splice-run scenarios/compact-three-robot/scenario.json --committed-horizon 3 --until quiescence --out /tmp/hero.json
uv run mapf-splice-inspect /tmp/hero.json --no-open
```
Confirm the server prints a URL and `/run.json` validates. (Manual: open the URL, scrub to the confirmed frame, verify the second panel renders.) Adjust CLI flags to match `run.py`'s actual argument names if they differ.

- [ ] **Step 6: Full gate + commit**

Run: `uv run ruff check . && uv run pytest -q`
```bash
git add src/mapf_splice/web_inspector tests/test_inspect.py
git commit -m "feat(inspector): render confirmed wait-for graph and lifecycle state"
```

---

### Task 9: Hero end-to-end confirmation (empirical outcome)

**Files:**
- Test: `tests/test_deadlock.py`

**Interfaces:**
- Consumes: full simulator + confirm step (Tasks 5, 7).

- [ ] **Step 1: Write the hero confirmation test (empirical placeholder)** — append to `tests/test_deadlock.py`. Do NOT hard-code the outcome; discover it, then pin it:

```python
@pytest.mark.parametrize("horizon", [3, 4, 5])
def test_hero_reaches_quiescence_then_confirms(horizon: int) -> None:
    scenario = load_scenario(ROOT / "scenarios/compact-three-robot/scenario.json")
    simulator = DeterministicSimulator.from_scenario(
        scenario, committed_horizon=horizon
    )
    for _ in range(60):
        simulator.tick()
        built = [
            event
            for event in simulator.trace.events
            if event.kind is EventKind.CONFIRMED_WAIT_FOR_BUILT
        ]
        if built:
            break
    assert built, f"K={horizon} never ran confirmation"
    outcome = dict(built[0].details)["outcome"]
    members = dict(built[0].details)["members"]
    assert members == "R1@2,R2@2,R3@2"
    # PIN the empirical outcome after first run (see Step 3):
    assert outcome == "PLACEHOLDER"
```

- [ ] **Step 2: Run to discover the empirical outcome**

Run: `uv run pytest tests/test_deadlock.py -q -k hero_reaches_quiescence 2>&1 | tail -30`
Expected: FAIL on the `PLACEHOLDER` assertion, printing the actual `outcome` value for each K.

- [ ] **Step 3: Pin the observed outcome** — replace `"PLACEHOLDER"` with the value the run reported (identical across K, or per-K if they differ). Record the observed K=3/K=4/K=5 outcomes in the commit message and report them to the user. If the outcome is `clear` or `external-dependency`, that is the empirical signal for a follow-up scenario calibration (out of scope — do NOT modify the scenario here).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_deadlock.py -q -k hero_reaches_quiescence`
Expected: PASS.

- [ ] **Step 5: Full gate + commit**

Run: `uv run ruff check . && uv run pytest -q`
```bash
git add tests/test_deadlock.py
git commit -m "test(deadlock): hero K=3/4/5 confirmation reaches three-robot scope (outcome: <observed>)"
```

---

## Self-Review

Run after drafting; fix inline.

**Spec coverage:** §4 data model → Task 4. §5 helpers → Task 1. §6 conflicts_for → Task 2. §7 builder + PLANNED guard → Task 4. §8 shared SCC → Task 3. §9 lifecycle/epoch/classify/confirm/prune/suppression/is_contained → Task 5. §10 tick integration → Task 7. §11 events (5) → Task 6 + emission Task 7. §12 replay/schema v0.2 + plan_version minimums → Task 7. §13 inspector → Task 8. §14 acceptance mapping → Tasks 4/5/7/9. §15 empirical hero → Task 9. All 15 acceptance criteria trace to a task.

**Placeholder scan:** The only intentional placeholder is Task 9's empirical outcome, resolved within the task by running and pinning — not a plan gap.

**Type consistency:** `ContainmentState`/`ConfirmationOutcome`/`ConfirmationResult`/`ContainmentSnapshot`/`ConfirmedWaitForGraph`/`ConfirmedWaitForEdge` names used identically across Tasks 4/5/7. `confirm(world, tick)`, `refresh(world) -> invalidated`, `prune_resolved()`, `is_contained(plan)`, `next_required_action(plan)`, `conflicts_for(action, *, occupied)` signatures match across producer/consumer tasks.
