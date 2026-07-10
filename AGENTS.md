# Repository Guidance

## Purpose

Build MAPF Splice as a clean reference implementation of on-demand MAPF
deadlock recovery and ADG-based asynchronous execution for continuous robot
fleet operation.

## Canonical documents

- `docs/V0_1.md` owns product scope and acceptance criteria.
- `docs/ARCHITECTURE.md` owns system boundaries and invariants.
- `docs/DEMO_AND_BLOG.md` owns the public demonstration and article narrative.

Update these documents when a design decision changes their claims. Do not add
parallel architecture documents that create competing sources of truth.

## Development rules

- Keep the domain and execution kernel deterministic and independent of I/O,
  rendering, wall-clock time, and a specific MAPF solver.
- Model asynchronous execution with deterministic phased ticks and explicit
  independently timed action progress, not nondeterministic threads.
- Treat current simulated positions and committed resources as authoritative.
- Preserve seeded reproducibility across comparison modes.
- Encode safety and plan-version rules as executable invariants with tests.
- Keep normal A*, traffic admission, deadlock analysis, recovery orchestration,
  MAPF integration, ADG compilation, and visualization in separate modules.
- Prefer small typed models and explicit state transitions over service-shaped
  abstractions copied from a production FMS.
- Do not add v0.1 non-goals without explicit scope alignment.

## Clean-room and licensing boundary

- Do not copy code, fitted hardware parameters, maps, logs, naming structures,
  or private implementation details from previous employers.
- Implement from this repository's specifications, public literature, and
  properly licensed dependencies.
- Record third-party licenses and attribution before vendoring code or assets.
- Use synthetic, documented parameters for the committed-droplet and preview
  models.

## Git workflow

- Do not commit or push unless the user explicitly requests it.
- Preserve user changes and keep unrelated work out of the current diff.
