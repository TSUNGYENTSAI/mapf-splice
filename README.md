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
- deterministic, schema-versioned runtime replay artifacts and an offline Web
  Inspector that consumes full simulation snapshots;
- MAPF solution validation and ADG compilation;
- a legacy static scenario-review renderer.

Confirmed wait-for analysis, hard-deadlock classification, recovery
orchestration, atomic group plan replacement, recovery ADG execution, metrics,
and the final animation are not implemented yet. The checked-in image is a
design-time scenario review, not evidence from a completed simulation. Runtime
review now uses generated replay artifacts.

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
then step forward to containment drain and quiescence. The browser renders
positions, plans, committed reservations, preview dependencies, SCC state, and
trace events already computed by Python. It does not reconstruct state, run
routing or traffic logic, or load `review.json`.

Each `simulation-run.v0.1` replay contains deterministic full snapshots at
`tick-start`, `after-completions`, `after-release`, `after-task-advance`,
`after-admission`, `after-action-start`, and `after-preview`. The JSON Schema is
under `schemas/`; the inspector assets are packaged in the Python distribution
and require no network access.

Confirmed hard deadlock, MAPF recovery, and recovery ADG execution are not yet
represented as runtime states.

## Render the legacy scenario design

The canonical compact scenario separates its map, lifelong workload, and
review-only route overlay. After `uv sync`, regenerate the checked-in PNG with:

```bash
uv run mapf-splice-render \
  --scenario scenarios/compact-three-robot/scenario.json \
  --review scenarios/compact-three-robot/review.json \
  --view prospective-scc-k3 \
  --output docs/assets/compact-three-robot-warehouse.png
```

This compatibility renderer still reads `review.json`. It is a deprecated
design fixture and is not used by replay export or inspection. Runtime replay
is the intended source for future screenshots and animation.

## License

Apache License 2.0. Third-party components and assets will retain their original
licenses and attribution.
