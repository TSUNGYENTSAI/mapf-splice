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

The goal is not to replace a fleet management system with a lifelong MAPF
solver. The goal is to show how MAPF can be introduced as a focused recovery
mechanism without discarding an existing task, routing, and traffic stack.

This is an independent clean-room reference implementation, not a module-by-
module port or functional reimplementation of an earlier FMS. It distills
general fleet-management lessons into a small public architecture using this
repository's specifications, public literature, synthetic parameters, and
properly licensed dependencies.

## Project status

MAPF Splice is currently building the v0.1 executable vertical slice. The
foundation currently includes:

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
- a scoped PyPIBT MAPF recovery **proposal** on a confirmed deadlock (validated
  synchronized paths and compiled new-version ADG plans, produced read-only and
  never installed);
- a static scenario-topology renderer.

Atomic group plan replacement, recovery ADG execution, metrics, and the final
animation are not implemented yet. On a confirmed deadlock the runtime now
produces and validates a scoped MAPF recovery proposal as read-only diagnostic
state; it still never installs a plan, changes a plan version, or mutates
reservations. The checked-in image shows scenario topology only; runtime
evidence comes from generated replay artifacts.

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

Use the **Stable SCC** bookmark to inspect the full `after-preview` snapshot,
then step forward through containment drain and quiescence to the **Confirmed**
and **Hard deadlock** bookmarks and the `after-confirmation` frame. The browser
renders positions, plans, committed reservations, prospective dependencies, the
confirmed wait-for graph, containment state, and trace events already computed by
Python. It does not reconstruct state, run routing or traffic logic, or consume a
parallel route fixture.

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

Confirmed hard deadlock is a runtime state, rendered in the inspector's confirmed
wait-for panel. On confirmation the runtime also produces a scoped MAPF recovery
proposal, shown read-only in the **Recovery proposal** panel and reachable from
the **Recovery** bookmark: participants, quiescent starts, task-phase goals, the
PIBT solver result and makespan, and ADG-compilation status. Recovery ADG
execution and atomic plan replacement are not yet represented; the proposal is
retained as diagnostic state awaiting the next milestone.

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
