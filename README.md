# MAPF Splice

MAPF Splice keeps robots independent during normal operation, coordinates only
the affected subset when a local deadlock persists, and safely splices
synchronized recovery paths into an asynchronous fleet executor.

![Story B: R1–R3 enter scoped recovery while active R4 remains outside the participant set](docs/assets/story-media/v0.1/story-b-local-recovery.gif)

**Story B · `four-robot-nonparticipant`.** This focused replay demonstrates
local recovery with an active non-participant: only the affected subset is
coordinated, while R4 keeps its live plan and continues through the shared
traffic authority. Stories E and F separately demonstrate delayed execution and
lifelong redispatch; this clip is not presented as a composite runtime.

## Why this project exists

A bounded MAPF solver returns synchronized timestep paths. A continuous robot
fleet needs more: tasks keep arriving, normal robots retain rolling traffic
authority, live plans may be replaced only at a safe boundary, and actions take
unequal time. MAPF Splice is the integration layer between those worlds. It
detects persistent local risk, drains the affected authority, confirms the
deadlock from current state, compiles scoped recovery into an ADG, and publishes
the new plan generations atomically.

## Six design problems

### A · Paths are not executable actions

**Problem.** Synchronized paths do not specify how an asynchronous executor
should handle unequal action duration.

**Design response.** The compiler turns every solver step into a typed move or
wait action with explicit same-robot order, replay-backed cross-robot
dependencies, and resource claims.

![Story A: synchronized paths become 33 recorded actions and explicit precedence](docs/assets/story-media/v0.1/story-a-paths-to-actions.gif)

**Takeaway.** MAPF paths become explicit actions and dependencies before they
enter the fleet executor.

### B · Coordinate only what is necessary

**Problem.** Globally replanning every active robot discards useful normal
autonomy and obscures the local incident.

**Design response.** Stable prospective risk identifies an affected scope;
only that frozen subset enters MAPF and the atomic splice. Other robots keep
their plans under the same occupancy and reservation authority.

![Story B: affected R1–R3 recover locally while R4 remains active](docs/assets/story-media/v0.1/story-b-local-recovery.gif)

**Takeaway.** Normal robots remain independent; MAPF coordinates only the
frozen local scope.

### C · Detect, contain, then confirm

**Problem.** The first future SCC may be transient; treating it as a hard
deadlock would confuse previewed contention with current reservation authority.

**Design response.** The controller observes stability, contains the affected
scope, drains committed motion to quiescence, then builds an authoritative
wait-for graph from current positions and committed resources.

![Story C: prospective risk becomes a distinct confirmed wait-for graph](docs/assets/story-media/v0.1/story-c-detect-contain-confirm.gif)

**Takeaway.** Confirm circular wait from authoritative state before recovery.

### D · Recovery is a transaction

**Problem.** A proposal may be stale by installation time, while sequential
per-robot replacement could partially mutate a live fleet.

**Design response.** Installation revalidates incident identity, versions,
positions, tasks, quiescence, reservations, and the compiled ADG as one group
gate; one recorded publication changes R1–R3 together and leaves R4 unchanged.

![Story D: one atomic publication replaces every participant plan together](docs/assets/story-media/v0.1/story-d-atomic-splice.gif)

**Takeaway.** Every affected plan is replaced together—or none change.

### E · Safe without lockstep execution

**Problem.** Installed synchronized paths still cannot require simultaneous
robot motion.

**Design response.** ADG precedence gates independently timed actions. In the
captured handoff, R2 completes `R2@3:2` at T36 `after-completions`; only then is
R1 admitted and started at T36 `after-action-start`.

![Story E: R2 completes before dependent R1 starts in the same phased tick](docs/assets/story-media/v0.1/story-e-asynchronous-adg.gif)

**Takeaway.** ADG causality preserves the synchronized solution while ordinary
execution handles unequal duration.

### F · Recovery is part of the lifecycle

**Problem.** Stopping immediately after escape does not demonstrate continuous
fleet integration.

**Design response.** Six continuous replay windows show recovery, resumed
motion, task completion and redispatch, a second recovery, and exact terminal
drain. Five labeled montage gaps disclose every skipped tick range.

![Story F: recover, resume, redispatch, recover again, and drain](docs/assets/story-media/v0.1/story-f-lifelong-operation.gif)

**Takeaway.** Robots return to normal dispatch and later incidents after a
recovery is released.

## Whole-system flow

```text
lifelong tasks
→ independent A*
→ committed traffic authority
→ prospective risk
→ contain and drain
→ confirmed deadlock
→ scoped MAPF
→ ADG compilation
→ atomic plan splice
→ asynchronous execution
→ normal operation
```

The domain and execution kernel remain deterministic and independent of I/O,
wall-clock time, rendering, and a particular MAPF solver. Current simulated
positions and committed resources are authoritative; recovery plans never
replace them by assumption.

## Evidence and reproduction

Launch the production Inspector with the canonical v0.1 corpus:

```bash
uv run mapf-splice-inspect --lifelong-cases validation/lifelong --no-open
```

Reproduce all six media assets from exact emitted items:

```bash
uv run python tools/story_media/capture_story_media.py --all
uv run python tools/story_media/capture_story_media.py --verify-only
```

Run the full suite:

```bash
uv run pytest -q
```

The frozen corpus contains 210 emitted items: Story B retains all 82 causal
items from T12–T34, Story A retains all 33 compiled actions in the DOM, and
Story F retains 89 runtime items plus five labeled montage gaps through exact
T147 `after-task-advance`. The checked-in
[media freeze](docs/assets/story-media/v0.1/media-freeze.json) binds each asset
to source commit `c29354f`, replay SHA-256, exact first/terminal anchors,
timing, tool versions, and output hashes. Raw frames and high-quality WebM/MP4
remain reproducible ignored artifacts.

Canonical design and validation references:

- [v0.1 scope and acceptance criteria](docs/V0_1.md)
- [system architecture and invariants](docs/ARCHITECTURE.md)
- [demo and article narrative](docs/DEMO_AND_BLOG.md)
- [capture storyboard](docs/storyboards/V0_1_CAPTURE_STORYBOARD.md)
- [Story Display playback specification](docs/storyboards/V0_1_STORY_PLAYBACK_SPEC.md)

## Scope and limitations

MAPF Splice is a clean-room reference integration pattern, not a production
FMS and not another MAPF solver. v0.1 uses point robots on a grid, bounded
deterministic validation, and one active incident at a time. It does not claim
fairness, global liveness, physical braking safety, fitted hardware timing, or
dispatch optimization. MAPF supplies scoped recovery paths; the surrounding
architecture owns confirmation, traffic authority, transactional installation,
asynchronous execution, and return to normal work.

## License

The project is licensed under the [MIT License](LICENSE). Vendored or external
components retain their own notices in [NOTICE](NOTICE).
