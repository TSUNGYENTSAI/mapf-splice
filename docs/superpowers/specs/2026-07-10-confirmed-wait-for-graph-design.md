# Confirmed Wait-for Graph & Containment Lifecycle — Design

Date: 2026-07-10
Status: Approved for implementation planning (blockers from review round 2 resolved)
Milestone predecessor: `3b5bf00` (replay map provenance + read-only controller snapshot)
Replay contract: bumps `simulation-run.v0.1` → `simulation-run.v0.2` (breaking).

## 1. Goal

After a stable prospective SCC is contained and its scope reaches quiescence, decide
— from authoritative state, not preview heuristics — whether the scope is a genuine
hard reservation deadlock. Build a **confirmed wait-for graph** from each contained
member's first unfinished action, classify the outcome, drive an explicit containment
lifecycle, and surface both graphs distinctly in the replay + inspector.

This milestone **classifies and records only**. It does not call MAPF, replace plans,
splice, or run ADG recovery.

## 2. Non-goals (hard scope boundary)

- No MAPF invocation, no plan replacement, no plan splice, no ADG recovery transaction.
- No containment merging or recovery-group expansion. Overlapping stable SCCs are
  recorded for diagnostics but do not grow or merge an active containment.
- No re-typing of pre-existing loose replay structures (robots/tasks/plans/reservations).
  Only the new confirmed-graph + containment-lifecycle surfaces are formally typed.

## 3. Separation of concerns

Three layers, deliberately kept apart so they cannot silently diverge:

```
graph            = authoritative evidence (facts)
outcome          = policy interpretation of the facts
containment state = control decision over time
```

- The **graph** (`confirm.py`) never carries a classification flag.
- The **outcome** (`ConfirmationOutcome`) is computed by the controller from the graph.
- The **state** (`ContainmentState`) is the controller's lifecycle over ticks.

## 4. Data model — `confirm.py` (new module, mirrors `preview.py`)

`confirm.py` also defines `class ConfirmationError(ValueError)` for invariant violations
during graph construction (e.g. a non-`PLANNED` next required action, §7).

```python
@dataclass(frozen=True, slots=True)
class ConfirmedWaitForEdge:
    waiting_robot_id: str
    waiting_plan_version: int
    waiting_action_ref: ActionRef            # the first UNFINISHED action
    resource: Resource                       # a required claim of that action
    blocking_robot_id: str
    blocking_plan_version: int
    committed_blocker_refs: tuple[ActionRef, ...]   # committed ownership (may be empty)
    occupied_blocker: bool                   # blocker currently occupies the vertex
    blocking_in_scope: bool                  # blocker is a member of this containment
    # blocking_plan_version may be 0 for an idle occupancy blocker with no current
    # plan (Robot.plan_version defaults to 0); committed-blocker ActionRefs are >= 1.

@dataclass(frozen=True, slots=True)
class ConfirmedWaitForGraph:
    scope: CandidateIdentity                 # the containment identity, ((robot, ver), ...)
    epoch: int
    captured_at_tick: int
    edges: tuple[ConfirmedWaitForEdge, ...]  # deterministically ordered
    cyclic_sccs: tuple[tuple[str, ...], ...] # robot-level SCCs of size >= 2
```

The graph holds facts only. No `hard_deadlock` flag (correction 1).

Edge ordering key (matches preview's determinism):
`(waiting_robot_id, waiting_action_ref, blocking_robot_id, resource_key(resource))`.

## 5. Plan-execution helpers — `planning.py`

```python
def completed_prefix_length(plan: Plan) -> int:
    """Number of leading COMPLETED actions; raises if completed actions are
    not a contiguous prefix (mirrors traffic._completed_prefix's invariant)."""
    prefix = 0
    while prefix < len(plan.actions) and plan.actions[prefix].status is ActionStatus.COMPLETED:
        prefix += 1
    if any(a.status is ActionStatus.COMPLETED for a in plan.actions[prefix:]):
        raise DomainError("completed actions must form a sequential prefix")
    return prefix

def next_required_action(plan: Plan) -> Action | None:
    """First action the robot has not yet completed, or None if the plan is done."""
    index = completed_prefix_length(plan)
    return plan.actions[index] if index < len(plan.actions) else None
```

`next_required_action` validates the sequential prefix rather than silently returning a
mid-plan action (correction 4). This milestone adopts `next_required_action` in:

- the confirmed-graph builder;
- `simulation._start_actions` (replaces its inline `next(... not COMPLETED)`).

`preview.preview_actions` and `traffic._completed_prefix` are left as-is; a later
milestone may route them through `completed_prefix_length`. Not forced now.

## 6. Authoritative conflict query — `traffic.py`

Expose the existing private conflict logic as a public read-only operation so the
confirmed graph reuses admission semantics instead of forking them (correction 2):

```python
def conflicts_for(self, action: Action, *, occupied: Mapping[Cell, str]
                  ) -> tuple[ReservationConflict, ...]:
    return self._conflicts_for(action, occupied, self._owners_by_resource)
```

`ReservationConflict` already separates `reserved_by` (committed owner refs) from
`occupied_by`, applies self-plan exclusion, and reports per-resource evidence. The
ledger remains ignorant of SCCs and deadlock; it only reports conflict facts.

## 7. Confirmed-graph builder — `confirm.py`

Pure function:

```python
def build_confirmed_wait_for(world: WorldState, scope: CandidateIdentity,
                             *, epoch: int, tick: int) -> ConfirmedWaitForGraph
```

Algorithm:

1. `scope_members = set(scope)`; `occupied = world.occupied_cells()`.
2. For each `(robot_id, version)` in `scope` (scope is valid + quiescent, so the current
   plan matches the version):
   - `action = next_required_action(world.plans[robot_id])`; skip if `None`.
   - Assert `action.status is ActionStatus.PLANNED` (a quiescent scope has no
     running/committed actions, so its next required action must be planned); raise
     `ConfirmationError` otherwise, so a stray `CANCELED` action's claims are never read
     as a motion requirement (detail 2).
   - `conflicts = world.reservations.conflicts_for(action, occupied=occupied)`.
   - Translate each `ReservationConflict` into edges keyed by
     `(blocking_robot_id, blocking_plan_version, resource)`:
     - group `reserved_by` refs by `(robot_id, plan_version)` → `committed_blocker_refs`;
     - fold `occupied_by` (its version read from `world.robots`) → `occupied_blocker=True`,
       merging into the same-robot committed edge when present;
     - set `blocking_in_scope = (blocking_robot, blocking_version) in scope_members`.
3. `cyclic_sccs = cyclic_components(edges_as_robot_pairs)` (see §8).
4. Return the graph (facts only).

Self-ownership never yields an edge (`_conflicts_for` excludes self already). Multiple
blockers on one resource → multiple edges. Occupied and committed are distinct fields.

## 8. Shared SCC — `deadlock.py`

Generalize the existing Tarjan into a reusable helper; keep the preview signature stable
so no preview/replay/test call sites change:

```python
def cyclic_components(edges: Iterable[tuple[str, str]]) -> tuple[tuple[str, ...], ...]:
    # Tarjan over waiting -> blocking; returns components of size >= 2, sorted.

def cyclic_sccs(analysis: PreviewAnalysis) -> tuple[tuple[str, ...], ...]:
    return cyclic_components((d.waiting_robot_id, d.blocking_robot_id)
                             for d in analysis.dependencies)
```

The confirmed builder calls `cyclic_components` directly. One SCC implementation.

## 9. Containment lifecycle — `deadlock.py`

Replace the `valid` / `quiescence_emitted` booleans with an explicit state + deterministic
epoch (correction 1, 5, 6):

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

ACTIVE_STATES = frozenset({
    ContainmentState.DRAINING, ContainmentState.QUIESCENT,
    ContainmentState.CONFIRMED_DEADLOCK, ContainmentState.EXTERNAL_BLOCKED,
})

@dataclass(slots=True)
class Containment:
    identity: CandidateIdentity
    epoch: int
    state: ContainmentState = ContainmentState.DRAINING
    confirmation_tick: int | None = None
    outcome: ConfirmationOutcome | None = None
    confirmed_graph: ConfirmedWaitForGraph | None = None
```

`epoch` is assigned from a monotonic controller counter (`_epoch_counter`), fully
deterministic (no clock/random), so two episodes of the same `(robot, version)` identity
are distinguishable in the historical event/replay record.

### 9.1 Classification (controller policy)

```python
def classify_confirmation(graph: ConfirmedWaitForGraph) -> ConfirmationOutcome:
    if graph.cyclic_sccs:
        return ConfirmationOutcome.CONFIRMED_DEADLOCK
    if any(not e.blocking_in_scope for e in graph.edges):
        return ConfirmationOutcome.EXTERNAL_DEPENDENCY
    return ConfirmationOutcome.CLEAR
```

Outcome → state map: `CONFIRMED_DEADLOCK → CONFIRMED_DEADLOCK`,
`EXTERNAL_DEPENDENCY → EXTERNAL_BLOCKED`, `CLEAR → CLEARED`.

### 9.2 Transitions

- `observe(analysis, versions)` — a stable SCC at threshold creates a **DRAINING**
  containment (new epoch) **only if none of its members overlap an existing ACTIVE
  containment** (correction 5). A candidate SCC that overlaps any active containment is
  **suppressed**: it accrues **no eligible stability count** while the overlap holds — its
  `_counts` entry is reset to 0 each tick — and its observation event carries
  `suppressed_by_active_containment=true` (detail 1). Once the overlap disappears, its
  first eligible observation restarts at 1, so a cleared containment's superset cannot
  re-contain instantly.
- `refresh(world)` — any active containment (DRAINING/QUIESCENT/CONFIRMED_DEADLOCK/
  EXTERNAL_BLOCKED) whose scoped robots/versions changed → **INVALIDATED**. Returns the
  invalidated identities+epochs for event emission.
- `newly_quiescent(world)` — DRAINING containment whose scope has no active action, no
  committed reservations, no running actions, all at the current version → **QUIESCENT**.
- `confirm(world, tick)` (new) — each confirmation phase processes a containment's **initial
  confirmation when it is QUIESCENT** and, on **subsequent** phases, **also re-evaluates
  EXTERNAL_BLOCKED** containments. For each such containment: build a fresh authoritative
  graph, `classify_confirmation`, set `confirmation_tick`/`outcome`/`confirmed_graph`,
  transition to the mapped state. Returns the built graphs + resulting states for
  events/recording. **Terminal outcomes**
  (`CONFIRMED_DEADLOCK`, `CLEAR`) are decided once per episode; `EXTERNAL_BLOCKED` is
  **re-evaluated every confirmation phase** because out-of-scope blockers keep moving and
  may free the scope (Blocker 1). `CONFIRMED_DEADLOCK` is not re-evaluated (an internal
  cycle among quiescent contained members cannot dissolve without intervention).
  `confirmation_tick`/`captured_at_tick` always record the **most recent** authoritative
  evaluation.
- `prune_resolved()` (new) — remove **CLEARED** and **INVALIDATED** containments and
  delete their `_counts` entries, forcing re-accumulation to threshold before any new
  instance can form. Called at the very top of `tick()`, so terminal states remain visible
  in the frames of the tick they occurred, then disappear next tick.

`is_contained(plan)` → True iff `(robot, version)` is a member of a containment whose
state is in `ACTIVE_STATES`. CLEARED/INVALIDATED are not contained, so admission resumes
the next tick.

### 9.3 Active vs terminal states

```
Active (containment held, admission suppressed):
    DRAINING, QUIESCENT, CONFIRMED_DEADLOCK, EXTERNAL_BLOCKED
Terminal (pruned next tick, admission resumes, counts reset):
    CLEARED, INVALIDATED
```

`EXTERNAL_BLOCKED` holds the containment but is re-evaluated every confirmation phase: if
the out-of-scope blocker moves off, the next evaluation yields `CLEAR`; if the scope forms
an internal cycle, it becomes `CONFIRMED_DEADLOCK`. Growing the recovery group to absorb a
persistent external blocker is deferred to a future milestone; this milestone only holds or
releases the existing scope.

## 10. Tick integration — `simulation.py`

```
prune_resolved()                              # NEW: clear previous tick's terminal episodes
record "tick-start"
complete_due / release                        -> after-completions, after-release
advance_tasks
invalidated = refresh(world)                  # emit containment-invalidated (phase ADVANCE_TASKS)
record "after-task-advance"
admit                                         -> after-admission
start_actions (uses next_required_action)     -> after-action-start
preview (emits quiescence-reached)            -> after-preview
confirmations = confirm(world, tick)          # emit confirmed-wait-for-built + outcome event
record "after-confirmation"                   # NEW checkpoint, distinct frame
advance tick
```

- New `TickPhase.CONFIRM_DEADLOCK`, inserted before `APPEND_EVENTS` (IntEnum renumbered;
  only phase *names* are serialized, so numeric renumbering is safe).
- Confirmation runs the same tick as quiescence: scope members are already quiescent when
  `confirm` runs, so the authoritative state is valid input.
- Event emission: `confirmed-wait-for-built` on every evaluation; an outcome-specific event
  (`hard-deadlock-confirmed` / `containment-external-blocked` / `containment-cleared`) only
  when the containment actually transitions into that state (see §11). A re-evaluation that
  stays `EXTERNAL_BLOCKED` emits `confirmed-wait-for-built` alone.

## 11. Events — `trace.py`

New `EventKind`s, with fire-frequency semantics (Blocker 1):

- `confirmed-wait-for-built` — emitted on **every** authoritative evaluation (including each
  re-evaluation of an `EXTERNAL_BLOCKED` containment); carries scope, epoch,
  captured_at_tick, outcome, edge count. This is the event the inspector bookmarks to jump
  to a confirmed frame, so it covers every outcome.
- `hard-deadlock-confirmed` — emitted **once**, on the transition into `CONFIRMED_DEADLOCK`.
- `containment-external-blocked` — emitted **once**, on first entry into `EXTERNAL_BLOCKED`
  (not on each re-evaluation that stays external). Confirmed as a legitimate event since
  `EXTERNAL_DEPENDENCY` is now a formal outcome.
- `containment-cleared` — emitted **once**, on the transition into `CLEARED`.
- `containment-invalidated` — from `refresh`, on the transition into `INVALIDATED`.

## 12. Replay frame + schema — `replay.py`, `simulation-run.v0.2.schema.json`

**Schema version bump (Blocker 2).** The checkpoint-count change, the new required
`confirmed_wait_for`, and the containment field swap are a breaking artifact-contract
change, so this introduces `schemas/simulation-run.v0.2.schema.json` with `$schema` /
`schema_version` consts of `simulation-run.v0.2`. The exporter emits v0.2, `replay.py`'s
`_schema()`/`validate_replay` load v0.2, the wheel `force-include` in `pyproject.toml`
points at the v0.2 file, and the inspector consumes v0.2. Since the project iterates fast,
pre-existing v0.1 artifacts are simply regenerated — no frontend compatibility layer, no
`schema_version`-dispatching loader.

- `CHECKPOINTS += ("after-confirmation",)`; schema `checkpoint_names` minItems 7 → 8;
  the frame-order test updates its "first N == CHECKPOINTS" assertion to 8.
- Each frame **always** carries `confirmed_wait_for`: a list of serialized **replay
  records** (envelopes), empty when no containment holds a confirmed graph on that frame.
  Each envelope wraps the facts-only `ConfirmedWaitForGraph` (scope/epoch/captured_at_tick/
  edges/cyclic_sccs) together with the lifecycle provenance (`outcome`, current `state`).
  The envelope is a replay/serialization construct — the domain `ConfirmedWaitForGraph`
  itself never carries outcome or state (clarification 2). The recorder emits the stored
  `confirmed_graph` for every containment in `CONFIRMED_DEADLOCK`/`EXTERNAL_BLOCKED`
  (persistent, so scrubbing later ticks still shows it) plus the just-`CLEARED` containment
  on its confirmation-tick frames. Each serialized graph carries `scope`, `epoch`,
  `captured_at_tick`, `outcome`, `edges`, `cyclic_sccs` (correction 6) so the inspector
  labels it "Confirmed at tick N" rather than implying a per-tick recompute.
- Each `deadlock.containments[]` entry replaces `valid`/`quiescence_emitted` with
  `state`, `epoch`, `confirmation_tick`, `outcome`. To avoid duplicating the graph, the
  full evidence lives only under `frame.confirmed_wait_for`; the inspector joins a
  containment to its graph by `epoch`. `ContainmentSnapshot` therefore exposes the stored
  `confirmed_graph` so the recorder can build `frame.confirmed_wait_for`, but the
  serialized `containments[]` entry carries lifecycle metadata (state/epoch/
  confirmation_tick/outcome) only, not the edges.

Schema is formally typed for the new surfaces (correction 7): `$defs` for the confirmed
graph, the edge, the containment entry, and `confirmed_wait_for` on the frame required
list. Pre-existing loose structures are left unchanged. Plan-version minimums (detail 3):

- `waiting_plan_version`: minimum 1 (a contained member always has a real plan).
- `blocking_plan_version`: minimum **0** (an idle occupancy blocker may have no current
  plan; `Robot.plan_version` defaults to 0).
- committed-blocker `ActionRef.plan_version`: minimum 1 (`ActionRef` forbids 0).
- containment `epoch`: minimum 1; `confirmation_tick`: integer ≥ 0 or null;
  `state` enum (six `ContainmentState` values); `outcome` enum (three values) or null.

## 13. Inspector — `web_inspector/`

- New "Confirmed wait-for graph" panel rendering `frame.confirmed_wait_for` with a distinct
  edge color, evidence on hover (resource, committed vs occupied, in/out of scope), and an
  outcome badge — never the prospective graph with a swapped title.
- Panel header shows provenance: "Confirmed at tick N · <outcome>" and the current
  containment state, so a persisted historical graph is not mistaken for a live recompute.
- Five new event kinds are defined (§11); a subset (primarily `confirmed-wait-for-built`,
  plus the transition events) becomes bookmark categories for jumping to the confirmed frame.
- SCC cards show `state` (and epoch) instead of the two removed booleans.

## 14. Testing (TDD) and acceptance-criteria mapping

New `tests/test_confirm.py` for the builder + classification; lifecycle tests extend
`tests/test_deadlock.py`; replay/inspector tests extend their files.

| # | Criterion | Coverage |
|---|-----------|----------|
| 1 | Confirmation only on valid+quiescent containment | `confirm` skips non-QUIESCENT; unit test |
| 2 | Uses first unfinished action, not preview horizon | builder uses `next_required_action` |
| 3 | Multiple blockers preserved | builder test: one resource, ≥2 blocking robots |
| 4 | Occupied vs committed recorded separately | edge `occupied_blocker` + `committed_blocker_refs` |
| 5 | Self ownership → no edge | builder test with WAIT on own cell |
| 6 | Acyclic (no external) confirmed graph clears containment | classification → CLEAR → terminal |
| 7 | Cleared plan resumes admission next tick | prune + `is_contained` False; sim test |
| 8 | Cleared candidate must re-accumulate threshold | prune deletes counts; observe restarts at 1 |
| 9 | Cyclic confirmed graph → hard deadlock, containment held | classification → CONFIRMED_DEADLOCK |
| 10 | Plan-version change invalidates, no stale analysis | `refresh` → INVALIDATED before confirm |
| 11 | Terminal outcome + event once per episode | `hard-deadlock-confirmed`/`containment-cleared` fire once; `EXTERNAL_BLOCKED` re-evaluates and re-emits `confirmed-wait-for-built` only |
| 12 | Hero K=3/4/5 reach three-robot quiescence | existing hero test + confirm step |
| 13 | Hero outcome decided by algorithm, not hard-coded | test encodes the empirical outcome |
| 14 | Inspector jumps to confirmed frame | bookmark on new event kinds |
| 15 | No MAPF, no plan replacement | no calls to adg/plan install in confirm path |

Additional tests:
- **External re-evaluation (Blocker 1):** external blocker present → `EXTERNAL_BLOCKED`
  (containment held); blocker moves off next tick → re-evaluation yields `CLEAR`; scope
  forms internal cycle → `CONFIRMED_DEADLOCK`. `containment-external-blocked` fires once.
- **Suppressed overlap (detail 1):** a superset SCC overlapping an active containment
  accrues no eligible count; after the containment clears it must re-accumulate from 1.
- **Non-`PLANNED` guard (detail 2):** builder raises `ConfirmationError` on a
  non-`PLANNED` next required action.
- **Idle occupancy blocker (detail 3):** an idle external robot (`plan_version` 0)
  occupying a needed vertex produces an edge with `blocking_plan_version=0`.

## 15. Open empirical question

Whether the hero (K=3/4/5) confirms as `CONFIRMED_DEADLOCK`, `EXTERNAL_DEPENDENCY`, or
`CLEAR` is decided by the algorithm on authoritative state — not assumed here. The hero
test encodes the observed outcome (as the calibration tests encode observed ticks). If the
hero comes back `CLEAR` or `EXTERNAL_DEPENDENCY`, that is the signal for the second scenario
calibration — a follow-up outside this milestone's code.
