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

## Project status

MAPF Splice is currently in the v0.1 design phase. The first release will be a
clean, deterministic reference implementation centered on a polished warehouse
demo and an accompanying technical article.

The canonical design documents are:

- [v0.1 vision and scope](docs/V0_1.md)
- [system architecture](docs/ARCHITECTURE.md)
- [demo and article plan](docs/DEMO_AND_BLOG.md)

## Render the scenario design

The canonical compact scenario separates its map, lifelong workload, and
review-only route overlay. After `uv sync`, regenerate the checked-in PNG with:

```bash
uv run mapf-splice-render \
  --scenario scenarios/compact-three-robot/scenario.json \
  --review scenarios/compact-three-robot/review.json \
  --view prospective-scc-k3 \
  --output docs/assets/compact-three-robot-warehouse.png
```

The scenario and review contracts are machine-readable JSON Schemas under
`schemas/`.

## License

Apache License 2.0. Third-party components and assets will retain their original
licenses and attribution.
