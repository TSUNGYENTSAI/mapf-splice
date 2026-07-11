# MAPF Splice

On-demand Multi-Agent Path Finding (MAPF) deadlock recovery and asynchronous
execution for robot fleets.

MAPF Splice explores a practical integration pattern for warehouse and mobile
robot fleets that already use independent routing and traffic rules:

1. Route robots cheaply with single-agent A* during normal operation.
2. Reserve a rolling motion-authority window and preview the next uncommitted
   window for approaching conflicts.
3. Contain stable prospective cycles and confirm local reservation deadlocks
   before recovery.
4. Invoke MAPF only for the affected robots.
5. Compile the synchronized MAPF solution into an Action Dependency Graph
   (ADG) that remains safe when robots execute at different speeds.
6. Replace the affected live plans and resume continuous operation.

Recovery authority is intentionally separate from normal traffic authority.
Normal plans retain atomic K-cell cruise admission. Exact controller-owned
recovery generations use a low-speed ADG bounded-prefix profile: same-robot
prefixes may be staged contiguously, cross-robot predecessors must already be
completed, and all grants in a phase publish atomically. This assumes the
vehicle controller can stop at every recovery action boundary; hardware braking
is outside the simulator's safety claim.

The goal is not to replace a fleet management system with a lifelong MAPF
solver. The goal is to show how MAPF can be introduced as a focused recovery
mechanism without discarding an existing task, routing, and traffic stack.

This is an independent clean-room reference implementation, not a module-by-
module port or functional reimplementation of an earlier FMS. It distills
general fleet-management lessons into a small public architecture using this
repository's specifications, public literature, synthetic parameters, and
properly licensed dependencies.

## Project status

The non-UI v0.1 runtime and fixed-topology lifelong validation milestone are
complete. The current phase is communication: select the clearest replay cases
and turn the existing deterministic evidence into a concise README, short
animation set, and technical article. The implemented core includes:

- schema and cross-file validation for the compact hero scenario;
- deterministic single-agent A* and executable hero-route expectations;
- typed robots, tasks, versioned plans, move/wait actions, and resources;
- authoritative world state, deterministic dispatch, and task-phase
  orchestration;
- atomic committed-droplet admission with rolling release and replenishment;
- deterministic phased normal execution with ActionRef-derived delays;
- append-only event tracing and read-only prospective dependency evidence;
- plan-version-scoped stable SCC detection, containment, and deterministic
  quiescence;
- single-incident confirmed wait-for analysis that classifies a quiescent
  containment as a hard reservation deadlock, a cleared false positive, or an
  unsupported external dependency;
- deterministic, schema-versioned runtime replay artifacts and an offline Web
  Inspector that consumes full simulation snapshots;
- MAPF solution validation and ADG compilation;
- scoped PyPIBT MAPF recovery proposals bound to immutable confirmed incidents;
- revalidated all-or-nothing multi-robot plan splice with typed failures;
- asynchronous recovery execution through an ADG bounded-prefix profile;
- active non-participants continuing normal work through shared occupancy and
  reservation authority;
- seeded bounded lifelong task streams, sequential recovery incidents, typed
  run termination, diagnostic summaries, and deterministic failure artifacts;
- a multi-case offline Web Inspector for selecting reproducible K=3 replays;
- a static scenario-topology renderer.

The remaining v0.1 work is visual and editorial. Runtime behavior changes are
out of scope unless producing the communication artifacts reproduces a
correctness defect in an existing contract. The checked-in image shows static
topology only; runtime evidence comes from generated replay artifacts.

Scoped recovery uses a vendored MIT subset of PIBT and requires NumPy, provided
by the optional `recovery` extra (`pip install "mapf-splice[recovery]"`); the
`uv sync` development environment already includes it.

The canonical design documents are:

- [v0.1 vision and scope](docs/V0_1.md)
- [system architecture](docs/ARCHITECTURE.md)
- [demo and article plan](docs/DEMO_AND_BLOG.md)

## Inspect a runtime replay

Export the canonical scenario from the Python simulation kernel, then open the
offline inspector:

```bash
uv run mapf-splice-run \
  --scenario scenarios/compact-three-robot/scenario.json \
  --committed-horizon 3 \
  --until quiescence \
  --max-ticks 200 \
  --output artifacts/hero-k3.run.json

uv run mapf-splice-inspect artifacts/hero-k3.run.json
```

Open **Detect, Contain, Then Confirm** and step through its named semantic
stages to compare the prospective SCC with the authoritative confirmed
wait-for cycle. The browser keeps physical fleet state, the current logical
visual, and the recovery lifecycle visible together. Its Evidence drawer
contains plans, reservations, transaction state, trace events, and the raw
frame already computed by Python. It does not reconstruct state, run routing
or traffic logic, or consume a parallel route fixture.

To review the selected K=3 lifelong cases from one Web Inspector, run:

```bash
uv run mapf-splice-inspect \
  --lifelong-cases validation/lifelong
```

The six-story menu selects the calibrated replay required by each claim. The
light 16:9 surface follows the checked-in
[v0.1 capture storyboard](docs/storyboards/V0_1_CAPTURE_STORYBOARD.md) and stays
within a no-scroll viewport. The map always remains visible while the logical
rail switches between SCC/scope graphs, an action graph, atomic splice cards,
a local ADG handoff, and lifecycle milestones. Export view hides controls only;
it does not change the content model or replay semantics.

Before selecting media windows, generate a read-only communication-proof report
from the same production replay path:

```bash
uv run mapf-splice-communication-proofs \
  --config validation/lifelong/three-robot-delayed.json
```

The report identifies incident windows, local scope and non-participant
evidence, plan versions around atomic splice, delayed cross-robot handoffs,
external waits, replay hash, and repeated-recovery continuation. It does not
make runtime decisions; the Inspector remains the final visual-review surface.

The calibrated hero intentionally distinguishes an early two-robot cyclic
observation from the first candidate that reaches the stability threshold:

| K | first cyclic observation | first stable / containment | quiescence |
|---|---|---|---|
| 3 | `R1@2,R3@2` at tick 14 | `R1@2,R2@2,R3@2` at tick 16 | tick 18 |
| 4 | `R1@2,R3@2` at tick 13 | `R1@2,R2@2,R3@2` at tick 15 | tick 18 |
| 5 | `R1@2,R3@2` at tick 12 | `R1@2,R2@2,R3@2` at tick 14 | tick 18 |

This is a three-robot **stable prospective SCC**. After the scope drains to
quiescence at tick 18, single-incident confirmation rebuilds an authoritative
wait-for graph from each member's first unfinished action and classifies it as a
**confirmed hard deadlock** for K=3, 4, and 5. The confirmed cycle is the
two-robot `R1 <-> R2` mutual-occupancy loop; `R3` waits into that cycle and is
transitively blocked, so the containment scope (three robots) is deliberately
larger than the confirmed cycle (two robots). The bootstrap release ticks are
`T1=5`, `T2=0`, and `T3=12`; one local shelf cell at `(11, 7)` keeps the
interaction in the lower loop. Re-run a bounded timing experiment with:

```bash
uv run python tools/calibrate_hero_scenario.py \
  --scenario scenarios/compact-three-robot/scenario.json \
  --t1 5 --t2 0 --t3 8:14 --horizons 3,4,5
```

Each `simulation-run.v0.2` replay contains deterministic full snapshots at
`tick-start`, `after-completions`, `after-release`, `after-task-advance`,
`after-admission`, `after-action-start`, `after-preview`, and
`after-confirmation`. The JSON Schema is under `schemas/`; the inspector assets
are packaged in the Python distribution and require no network access.

Confirmed hard deadlock is a runtime state rendered in the Inspector's
confirmed wait-for panel. The **Recovery proposal** panel then shows scoped
participants, quiescent starts, phase goals, solver output, ADG compilation,
installed generations, bounded-prefix authority, external blocking evidence,
and completed recovery lifecycle state.

## Render the scenario topology

After `uv sync`, regenerate the checked-in topology PNG directly from the
canonical scenario and map:

```bash
uv run mapf-splice-render \
  --scenario scenarios/compact-three-robot/scenario.json \
  --output docs/assets/compact-three-robot-warehouse.png
```

This renderer draws static topology only. Replay frames are the sole source for
runtime screenshots, dependency graphs, containment, and future animation.

## License

Apache License 2.0. Third-party components and assets retain their original
licenses and attribution; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
The scoped-recovery solver is a vendored MIT subset of
[pypibt](https://github.com/Kei18/pypibt) under `src/mapf_splice/_vendor/pypibt/`.
