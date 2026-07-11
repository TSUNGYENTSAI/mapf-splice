# v0.1 capture storyboard

Status: in progress. `docs/DEMO_AND_BLOG.md` owns the communication narrative;
this document is the shot-level implementation specification for the unified
story interface and media review. `docs/storyboards/V0_1_STORY_PLAYBACK_SPEC.md`
owns replay interval selection, canonical checkpoints, playback ordering, and
the controller acceptance contract. A key frame in this document is a shot
anchor inside that playback contract; it is not permission to omit the runtime
interval leading to the anchor.

## Shared capture contract

- Viewport: 16:9, verified at 1440 × 810 and suitable for 1600 × 900 export.
- Surface: one light-first story UI, one map, one robot palette, and one overlay
  language. Story presets change the logical visual, not application identity.
- Layout: title/claim header; 65–70% physical map; 30–35% logical/evidence rail;
  one named lifecycle row. No browser scrolling at 1440 × 810.
- Logical visual: SCC and affected-scope graph for local recovery and
  confirmation, plan-generation cards for splice, a local ADG for asynchronous
  handoff, and task/recovery milestones for lifelong continuation.
- Evidence: plans, actions, reservations, transaction details, events, and raw
  frames remain available in one drawer without changing story terminology.
- Source: production replay only. UI may select, filter, label, and style replay
  state; it never derives routes, dependencies, scope, authority, or recovery.
- Post-production: zoom, crop, pause, arrows, focus masks, and short captions are
  allowed. It must not create technical state absent from replay.
- Export: hides controls only and preserves the same map, logical visual,
  explanation, and lifecycle content.
- Review gate: each story's named stages must explain the claim as static
  frames before recording begins.

## Visual language

| Meaning | Treatment |
| --- | --- |
| map/background | white and warm light gray; low-contrast walls and grid |
| robot identity | stable robot color across every clip |
| normal path | thin, low-contrast robot-colored path |
| recovery path | stronger blue accent path |
| affected scope | soft amber halo around replay-declared contained robots |
| prospective dependency | gray-blue dashed arrow |
| confirmed dependency | dark gray-blue solid arrow |
| cycle core | heavy node outline |
| affected graph scope | soft amber hull |
| active non-participant | normal robot color, no scope halo |
| unmet ADG dependency | muted card with waiting label |
| satisfied dependency | green accent and successor-ready label |
| typography | dark neutral sans-serif; monospace only for versions/action refs |

## Clip A — Paths are not executable actions

- **Title:** A path is not an execution policy
- **Design problem:** synchronized solver positions cannot be submitted directly
  to an asynchronous executor.
- **Source replay:** `four-robot-nonparticipant`
- **Window:** incident 1, tick 18, `after-confirmation` through
  `after-recovery-install`
- **Target duration:** 6–8 seconds
- **Primary subject:** synchronized path summary changing into action/dependency
  cards; map remains supporting context.
- **Required overlays:** 3 solver paths; 33 compiled moves; 30 same-robot and 10
  cross-robot dependencies; selected predecessor arrows.
- **Required text:** `Synchronized positions → executable actions → explicit
  precedence`.
- **Hide:** raw reservations, full trace, filters, blocker details, schema data.
- **Key frames:** Before `T18 after-confirmation`; Transition `T18
  after-recovery-install`; execution context `T19 after-admission →
  after-action-start`.
- **End state:** installed actions with explicit dependencies.
- **README takeaway:** MAPF paths are compiled into actions and dependencies
  before entering the executor.
- **Blog topic:** solver output versus execution policy.

## Clip B — Local coordination, not global MAPF

- **Title:** Coordinate only what is necessary
- **Design problem:** why the whole active fleet does not need global replanning.
- **Source replay:** `four-robot-nonparticipant`
- **Window:** playback tick 12–34; primary incident shot focus tick 16–34.
- **Target duration:** 20–30 second primary hero, later paced in editing.
- **Primary subject:** map with R1–R3 scope halo and uncontained R4 motion.
- **Required overlays:** four active robots; affected scope R1–R3; R4 as active
  non-participant; R1–R3 `v2 → v3`; R4 `v2 → v2`; recovery state.
- **Required text:** `R1–R3 coordinated locally · R4 keeps its plan`.
- **Hide:** raw action refs, full ADG, reservation rows, checkpoint names, task
  metadata unrelated to active role.
- **Key frames:** Before `T16 after-preview`; Transition `T18
  after-recovery-install`; After `T34 after-recovery-completion`.
- **End state:** recovery completed; R4 has five visible completions during the
  incident. Post-recovery task progress remains a known hero-tail gap.
- **README takeaway:** the unaffected robot keeps its plan and never enters the
  MAPF problem.
- **Blog topic:** local planning with global safety arbitration.

## Clip C — Detect, contain, then confirm

- **Title:** A predicted cycle is evidence, not yet a deadlock
- **Design problem:** the first future SCC may be transient.
- **Source replay:** `three-robot-k3`
- **Window:** tick 14–18
- **Target duration:** 8–10 seconds
- **Primary subject:** prospective SCC graph morphing into the confirmed
  wait-for graph, with the map as physical evidence.
- **Required overlays:** prospective dependencies; stable prospective core and
  affected scope R1–R3; containment; quiescence; confirmed cyclic SCC R1–R2.
- **Required text:** `Observe → Contain → Drain → Confirm`.
- **Hide:** recovery solver, ADG detail, task table, complete event log.
- **Key frames:** Before `T14 after-preview`; Transition `T16 after-preview`;
  After `T18 after-confirmation`.
- **End state:** confirmed wait-for graph distinct from prospective graph.
- **README takeaway:** confirm circular wait from authoritative state before
  recovery.
- **Blog topic:** prospective risk versus confirmed deadlock.

## Clip D — Transactional splice

- **Title:** Recovery is a transaction
- **Design problem:** live participant plans cannot be replaced one at a time.
- **Source replay:** `four-robot-nonparticipant`
- **Window:** tick 18, `after-confirmation → after-recovery-install`
- **Target duration:** 5–7 seconds
- **Primary subject:** Before/Validate/After version cards; map is secondary.
- **Required overlays:** incident-bound validation; R1/R2/R3 `v2 → v3`; R4
  unchanged at v2; one atomic transition marker.
- **Required text:** `All participant plans change—or none do`.
- **Hide:** full paths, SCC history, raw trace, reservation list.
- **Key frames:** Before `T18 after-confirmation`; Transition uses the same frame
  with validation card; After `T18 after-recovery-install`.
- **End state:** three new generations installed together; fourth untouched.
- **README takeaway:** recovery is an all-or-nothing live-plan transaction.
- **Blog topic:** stale-state revalidation and version safety.

## Clip E — Delayed ADG handoff

- **Title:** Safe without lockstep execution
- **Design problem:** synchronized recovery cannot require simultaneous motion.
- **Source replay:** `three-robot-delayed`
- **Window:** tick 33–36, `R2@3:2 → R1@3:2`
- **Target duration:** 6–8 seconds
- **Primary subject:** two action cards and their dependency; map shows motion.
- **Required overlays:** R2 predecessor running with `+2` deterministic ticks;
  R1 successor waiting; predecessor completion; successor starts at tick 36. The
  `external waits = 0` claim is not an inline story overlay; it stays in the
  separate communication-proof evidence and is shown inline only when an
  identified analyzer artifact is explicitly loaded with provenance.
- **Required text:** `R1 waits for R2 · dependency satisfied · R1 starts`.
- **Hide:** SCC panels, unrelated actions, complete ADG, task metadata.
- **Key frames:** Before `T33 after-action-start`; Transition `T35 tick-start`;
  After `T36 after-action-start`.
- **End state:** successor starts in the same completion tick.
- **README takeaway:** ADG precedence stays safe while action durations differ.
- **Blog topic:** deterministic asynchronous execution semantics.

## Clip F — Lifelong continuation

- **Title:** Recovery is part of the lifecycle
- **Design problem:** escaping one incident does not prove fleet integration.
- **Source replay:** `random-k3-two-recoveries-seed615`
- **Window:** playback montage across tick 6–147.
- **Target duration:** 8–12 second montage, not real-time playback.
- **Primary subject:** map and a compact lifecycle rail.
- **Required overlays:** first containment/install/complete; intervening task
  progress and dispatch; second distinct incident/install/complete; clean drain.
- **Required text:** `Recover → Resume → Redispatch → Recover again → Drain`.
- **Hide:** per-action detail, raw dependencies except during incident markers,
  complete trace and reservation tables.
- **Key frames:** first incident lead-in begins `T6 after-action-start`;
  Transition `T26 after-recovery-completion`; After `T95
  after-recovery-completion`; exact terminal `T147 after-task-advance`.
- **End state:** two completed recoveries and all released work drained.
- **README takeaway:** recovery returns robots to normal dispatch and later work.
- **Blog topic:** incident release and sequential recovery identity.

## Media-freeze record

After static-frame approval, each clip receives a frozen record containing:

```text
source config
replay SHA-256
incident number
start/end tick
checkpoint bounds
narrative title and claim
visual annotations
approved static frame paths
```

GIF, WebM/MP4, README layout, and final Blog assembly begin only after this
record is approved.
