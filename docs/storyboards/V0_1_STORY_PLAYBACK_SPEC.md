# v0.1 story playback specification

Status: proposed implementation contract. This document defines replay-time
selection and story playback. `docs/DEMO_AND_BLOG.md` continues to own the
public narrative, and `docs/storyboards/V0_1_CAPTURE_STORYBOARD.md` continues
to own shot composition and visual language. This document does not redefine
runtime semantics or system architecture.

## Purpose

The Web story UI must show how recorded runtime state reaches a conclusion. A
semantic stage is therefore a named interval on the replay timeline, not a
replacement for the frames inside that interval.

```text
Story
└── Playback segments
    └── Semantic stages
        └── Selected production replay frames in monotonically increasing order
```

The playback engine consumes production replay. It may select frames, retain
important checkpoints, suppress redundant checkpoints, control presentation
speed, and hold an already-recorded state. It must not route, plan, compile an
ADG, infer authority, select scope, invent validation results, or otherwise
recreate runtime semantics.

## Terms

- **Replay frame:** one immutable production frame identified by its replay
  `index`, `tick`, and `checkpoint`.
- **Emitted item:** one entry in the story's deterministic presentation
  sequence. It is a runtime frame, replay-backed explanatory projection, or a
  montage-gap card.
- **Frame cursor:** the index of the currently displayed emitted item. It does
  not index production `frames[]`, and playback position is never represented
  by a stage index.
- **Canonical physical frame:** the one production checkpoint normally shown
  for a tick when no additional phase transition must be exposed.
- **Event frame:** a production checkpoint retained because a meaningful event
  or state transition becomes visible there.
- **Segment:** an ordered playback unit. A segment is continuous runtime,
  explanatory transition, or montage.
- **Stage:** a semantic label active over one or more ordered frames. A stage
  has an entry frame and inclusive active range.
- **Anchor:** an exact replay `(tick, checkpoint)` used for a boundary or hold.
  An anchor does not skip the interval leading to it.
- **Hold:** additional presentation time on an existing frame. A hold does not
  create technical state.
- **Explanatory frame:** a presentation-only projection of replay-backed data,
  used for compiler or transaction explanation. It must retain provenance to
  the production fields it displays.

## Playback state and data model

The controller state must distinguish replay position from semantic labeling:

```js
{
  storyId,
  segmentIndex,
  frameCursor,
  activeStageId,
  status: "idle" | "playing" | "paused" | "complete",
  elapsedInHoldMs
}
```

Every story is compiled to one ordered emitted sequence before playback:

```js
{
  kind: "runtime" | "explain" | "montage-gap",
  replayIndex: number | null,
  sourceFrames: [{tick, checkpoint, replayIndex}],
  presentation: {
    durationMs,
    stageId,
    logicalView,
    overlays,
    caption
  }
}
```

- A `runtime` item has one source frame and its `replayIndex` equals that
  frame's production index.
- An `explain` item has one or more source frames. Multiple explain steps may
  share the same source frame without moving runtime backward.
- A `montage-gap` item has no replay index. Its two source frames are the exact
  preceding segment terminal and following segment entry; its label is derived
  only from that configured gap.
- Previous/next, pause/resume, holds, and completion operate on emitted items.
  Production order validation operates on their non-null source replay indices.

The normative story shape is:

```js
{
  id,
  replaySource,
  terminalFrame: {tick, checkpoint},
  segments: [{
    id,
    playbackMode: "continuous" | "explain" | "montage",
    interval: {
      from: {tick, checkpoint},
      to: {tick, checkpoint}
    },
    framePolicy: "canonical-tick" | "explicit-checkpoints",
    selection: {
      visibleFields: [],
      eventKinds: [],
      mandatoryCheckpoints: []
    },
    rate: 1,
    stages: [{
      id,
      label,
      entry: {tick, checkpoint},
      through: {tick, checkpoint},
      logicalView: "scc" | "adg" | "splice" | "compiler" | "lifecycle",
      overlays: [],
      caption: "",
      holds: [{at: {tick, checkpoint}, durationMs: 0}]
    }]
  }]
}
```

For an `explain` segment, `interval` identifies the immutable replay evidence
boundary. Its explanatory frames additionally declare exact source fields:

```js
{
  kind: "explain",
  replayIndex: null,
  sourceFrames: [{tick, checkpoint}],
  sourceFields: ["recovery.paths", "plans[].actions[].dependencies"],
  step: "paths" | "actions" | "same-robot-edges" | "cross-robot-edges" |
        "claims" | "transaction-boundary"
}
```

## Exact anchor resolution

Every configured runtime anchor must resolve to exactly one production frame.

- The `(tick, checkpoint)` pair must exist.
- Unknown checkpoints are configuration errors.
- A missing tick is a configuration error.
- There is no fallback to the last frame in the tick, a later tick, or the final
  replay frame.
- Segment and stage boundaries must be monotonically increasing by replay
  `index`. Equal indices are allowed only for consecutive explanatory items
  explicitly backed by the same source frame. A montage-gap item is ordered by
  its preceding and following source-frame indices.
- Runtime segments may not move backward within a tick.
- `terminalFrame` must resolve exactly and be the final emitted frame of the
  story.

These requirements specifically exclude aliases such as `after-containment`
and `after-action-completion`. The production checkpoint for action completion
is `after-completions`; containment is recorded at `after-preview` together
with its trace event and controller snapshot.

## Canonical frame-selection policy

Production replay normally records nine checkpoints per tick and adds
`after-recovery-completion` when a recovery completes. Playback preserves every
tick in a continuous interval but need not display every redundant checkpoint.

### Canonical checkpoint

For a normal tick, the canonical physical frame is `after-action-start`. It
shows positions after due completions and releases, plus actions newly started
in the current phased tick.

### Mandatory event checkpoints

The following checkpoints must be inserted in addition to the canonical frame
when their associated state or event is relevant to the current story:

| Meaning | Production checkpoint |
| --- | --- |
| action completion and dependency handoff | `after-completions` |
| recovery completion | `after-recovery-completion` |
| prospective dependencies and SCC observation | `after-preview` |
| stable SCC and containment start | `after-preview` |
| quiescence | `after-preview` |
| confirmed wait-for graph and proposal | `after-confirmation` |
| atomic plan installation | `after-recovery-install` |

`tick-start`, `after-release`, `after-task-advance`, and `after-admission` are
included only when they contain story-relevant recorded state or events that
are not visible at the canonical frame. Admission grants may be shown at
`after-admission`; their resulting action starts remain visible at
`after-action-start`.

### Include and skip rule

Within a continuous interval:

1. Include at least one frame for every tick.
2. Include every mandatory event checkpoint relevant to the story.
3. Include an additional checkpoint when any story-visible value changes
   between the preceding emitted frame and the canonical frame: robot position,
   active action, remaining ticks, task status, plan version, containment,
   prospective graph, confirmed graph, recovery state, admission state, or a
   displayed trace event.
4. A checkpoint may be skipped only when its physical state, logical state,
   displayed overlays, and relevant events are all unchanged from the adjacent
   emitted state.
5. Skipping a redundant checkpoint must never skip its tick.

The browser compares only the segment's configured `selection.visibleFields`
and `selection.eventKinds` to suppress duplicates. It does not decide at
runtime which values or events are story-relevant. Mandatory checkpoint names
are supplied by `selection.mandatoryCheckpoints`. This deterministic comparison
is presentation filtering, not a reconstruction of runtime state.

## Timing, holds, and pause behavior

- Runtime rate is expressed per emitted canonical tick, not per semantic
  stage. Event checkpoints inside the same tick use a shorter phase duration.
- `pause` freezes the exact `frameCursor`, segment, stage, and remaining hold.
- `resume` continues from that cursor; it does not restart the stage.
- A hold repeats presentation time on the current emitted item. For a runtime
  item it remains on the same immutable production frame.
- Stage entry updates labels and logical view without changing the resolved
  runtime cursor.
- Reaching the terminal frame changes status to `complete`; replay does not
  automatically jump to the beginning.
- Starting from `complete` is an explicit restart operation.

Recommended capture timing defaults are presentation metadata, not runtime
semantics:

| Frame type | Default presentation |
| --- | ---: |
| canonical tick | 350 ms |
| same-tick phase transition | 250 ms |
| named causal event hold | 800 ms |
| atomic installation hold | 900 ms |
| completion terminal hold | 1,000 ms |

Individual story tables below may override these values.

## Authoritative terminals and stage boundaries

These tables are the machine-definition boundary. Prose ranges in the story
sections summarize these exact inclusive `entry` and `through` anchors; they do
not authorize an implementation to choose different boundaries.

| Story | Exact terminal frame |
| --- | --- |
| A | T19 `after-action-start` |
| B | T34 `after-recovery-completion` |
| C | T18 `after-confirmation` |
| D | T18 `after-recovery-install` |
| E | T36 `after-action-start` |
| F | T147 `after-task-advance` |

### Exact stage manifest

| Story/stage | Exact entry | Exact through |
| --- | --- | --- |
| A synchronized paths | T18 `after-confirmation` | T18 `after-confirmation` |
| A action compilation | T18 `after-recovery-install` | T18 `after-recovery-install` |
| A same-robot sequencing | T18 `after-recovery-install` | T18 `after-recovery-install` |
| A cross-robot precedence | T18 `after-recovery-install` | T18 `after-recovery-install` |
| A resource claims | T18 `after-recovery-install` | T18 `after-recovery-install` |
| A executable context | T19 `after-admission` | T19 `after-action-start` |
| B all robots active | T12 `after-action-start` | T13 `after-preview` |
| B prospective dependencies | T14 `after-action-start` | T15 `after-preview` |
| B stable SCC and containment | T16 `after-action-start` | T16 `after-preview` |
| B committed authority drains | T17 `after-completions` | T18 `after-action-start` |
| B quiescence and confirmation | T18 `after-preview` | T18 `after-confirmation` |
| B atomic splice | T18 `after-recovery-install` | T18 `after-recovery-install` |
| B recovery execution | T19 `after-completions` | T33 `after-action-start` |
| B recovery completes | T34 `after-completions` | T34 `after-recovery-completion` |
| C future dependencies | T14 `after-action-start` | T15 `after-preview` |
| C stable cyclic risk | T16 `after-action-start` | T16 `after-preview` |
| C containment emphasis | T16 `after-preview` | T16 `after-preview` |
| C existing authority drains | T17 `after-completions` | T17 `after-preview` |
| C quiescence | T18 `after-completions` | T18 `after-preview` |
| C confirmed wait-for cycle | T18 `after-confirmation` | T18 `after-confirmation` |
| D proposal ready | T18 `after-confirmation` | T18 `after-confirmation` |
| D validation requirements | T18 `after-confirmation` | T18 `after-confirmation` |
| D atomic publication | T18 `after-recovery-install` | T18 `after-recovery-install` |
| E predecessor starts | T33 `after-action-start` | T33 `after-action-start` |
| E delay progresses | T34 `after-action-start` | T34 `after-action-start` |
| E successor waits | T35 `tick-start` | T35 `after-action-start` |
| E dependency completes | T36 `after-completions` | T36 `after-completions` |
| E successor admission | T36 `after-admission` | T36 `after-admission` |
| E successor starts | T36 `after-action-start` | T36 `after-action-start` |
| F first incident formation | T6 `after-action-start` | T10 `after-recovery-install` |
| F first recovery tail | T23 `after-action-start` | T26 `after-recovery-completion` |
| F resumed operation | T27 `tick-start` | T32 `after-action-start` |
| F later useful work | T48 `after-action-start` | T52 `after-action-start` |
| F second recovery tail | T92 `after-action-start` | T95 `after-recovery-completion` |
| F clean drain | T143 `after-action-start` | T147 `after-task-advance` |

Repeated source frames in A, C, and D emit distinct `explain` items. Repeated
runtime anchors do not create duplicate runtime items.

## Deterministic selection manifest

Field paths below are selectors over replay frame data. An implementation may
normalize array ordering for comparison but may not add unlisted fields or
event kinds based on renderer judgment.

### Runtime graph selection — Stories B and C

```js
selection: {
  visibleFields: [
    "robots[].position",
    "robots[].active_action_ref",
    "robots[].remaining_ticks",
    "robots[].plan_version",
    "plans[].actions[].display_authority",
    "preview.dependencies",
    "preview.cyclic_sccs",
    "deadlock.newly_stable",
    "deadlock.containment",
    "confirmed_wait_for",
    "recovery.state",
    "recovery.paths",
    "recovery.expected_plan_versions",
    "recovery.installed_plan_versions"
  ],
  eventKinds: [
    "action-completed",
    "stable-scc-detected",
    "containment-started",
    "quiescence-reached",
    "confirmed-wait-for-built",
    "hard-deadlock-confirmed",
    "recovery-proposal-ready",
    "recovery-install-succeeded",
    "recovery-admission-evaluated",
    "recovery-prefix-granted",
    "action-started",
    "recovery-completed"
  ],
  mandatoryCheckpoints: [
    "after-completions",
    "after-action-start",
    "after-preview",
    "after-confirmation",
    "after-recovery-install",
    "after-recovery-completion"
  ]
}
```

Story C restricts the same manifest to events and fields through confirmation;
it does not emit recovery installation or execution checkpoints. Story B uses
the full manifest and additionally retains R4 action-completion events.

### ADG handoff selection — Story E

```js
selection: {
  visibleFields: [
    "robots[].position",
    "robots[].active_action_ref",
    "robots[].remaining_ticks",
    "plans[].actions[].status",
    "plans[].actions[].display_authority",
    "plans[].actions[].dependencies",
    "recovery.admission"
  ],
  eventKinds: [
    "action-completed",
    "recovery-admission-evaluated",
    "recovery-prefix-granted",
    "action-started"
  ],
  mandatoryCheckpoints: [
    "tick-start",
    "after-completions",
    "after-admission",
    "after-action-start"
  ]
}
```

### Lifelong montage selection — Story F

```js
selection: {
  visibleFields: [
    "robots[].position",
    "robots[].active_action_ref",
    "robots[].remaining_ticks",
    "robots[].plan_version",
    "tasks[].status",
    "tasks[].assigned_robot_id",
    "deadlock.containment",
    "confirmed_wait_for",
    "recovery.state",
    "recovery.installed_plan_versions",
    "reservations"
  ],
  eventKinds: [
    "task-released",
    "task-assigned",
    "task-status-changed",
    "plan-installed",
    "action-completed",
    "action-started",
    "containment-started",
    "confirmed-wait-for-built",
    "recovery-install-succeeded",
    "recovery-completed"
  ],
  mandatoryCheckpoints: [
    "after-completions",
    "after-release",
    "after-task-advance",
    "after-action-start",
    "after-preview",
    "after-confirmation",
    "after-recovery-install",
    "after-recovery-completion"
  ]
}
```

Story A and D use `explicit-checkpoints`; duplicate suppression does not select
their explain items. Their emitted steps are exactly the stage manifest above.

## Story A — Paths are not executable actions

**Source:** `four-robot-nonparticipant`, incident 1.

**Mode:** replay-backed compiler explainer.

**Evidence boundary:** T18 `after-confirmation` through T18
`after-recovery-install`, with T19 `after-admission` as execution context.

The map remains supporting context. R4 remains visible as an ordinary active
robot but is not included in compiler rows or ADG counts.

| Stage | Source | Logical content | Required provenance | Hold |
| --- | --- | --- | --- | ---: |
| synchronized paths | T18 `after-confirmation` | all three solver paths as ordered cells with shared timestep columns | `recovery.paths` | 800 ms |
| action compilation | T18 `after-recovery-install` | all 33 compiled actions, grouped by robot; move/wait kind and endpoints | `plans[].actions[]` | 700 ms |
| same-robot sequencing | same source frame | 30 actual same-robot dependency edges | `actions[].dependencies` | 700 ms |
| cross-robot precedence | same source frame | 10 actual cross-robot dependency edges, with selected edges focused but none fabricated | `actions[].dependencies` | 900 ms |
| resource claims | same source frame | claims attached to the selected action/edge | `actions[].claims` | 700 ms |
| executable context | T19 `after-admission` then `after-action-start` | admitted/running recovery actions on the map | `recovery.admission`, action authority/status | 900 ms |

No visual edge may be drawn merely from row position. Every action, dependency,
and claim carries its replay action reference. The explainer may progressively
reveal data, but it must not imply that compilation happened across multiple
runtime ticks.

## Story B — Local recovery, not global replanning

**Source:** `four-robot-nonparticipant`, incident 1.

**Mode:** continuous runtime replay.

**Runtime interval:** T12 `after-action-start` through T34
`after-recovery-completion`. T12–15 provide the active-fleet and risk-formation
lead-in; T16–34 are the incident window.

| Tick/range | Stage | Mandatory frames | Physical/logical contract |
| --- | --- | --- | --- |
| T12–13 | all robots active | `after-action-start`, relevant `after-preview` | show all four active robots and ordinary plan authority; no recovery scope claim |
| T14–15 | prospective dependencies emerge | `after-action-start`, `after-preview` | animate recorded prospective edges and candidate observations as they change |
| T16 | stable SCC and containment | `after-action-start`, `after-preview` | show stable SCC, affected R1–R3 scope, and R4 outside scope; hold 900 ms at `after-preview` |
| T17–18 | committed authority drains | `after-completions`, `after-action-start`, `after-preview` | show each tick and completion-driven movement; no confirmed-deadlock claim before confirmation |
| T18 | quiescence and confirmation | `after-preview`, `after-confirmation` | hold quiescence 700 ms, then switch to authoritative confirmed graph and proposal paths |
| T18 | atomic splice | `after-recovery-install` | versions R1–R3 v2→v3 and R4 v2→v2; hold 900 ms |
| T19–33 | recovery admission and execution | `after-completions`, relevant `after-admission`, `after-action-start` for every tick | show grants, waiting, running actions, completions, remaining ticks, and all five recorded R4 move completions |
| T34 | recovery completes | `after-completions`, `after-recovery-completion` | show terminal completion and incident release; hold 1,000 ms |

No tick from T12 through T34 may be absent. Prospective graph overlays are
shown only from `after-preview` evidence. Confirmed graph overlays begin only at
`after-confirmation`. R4's unchanged version and non-participant status are
read from before/after replay frames, not constant strings.

## Story C — Detect, contain, then confirm

**Source:** `three-robot-k3`, incident 1.

**Mode:** continuous runtime replay.

**Runtime interval:** T14 `after-action-start` through T18
`after-confirmation`.

| Tick/range | Stage | Mandatory frames | Logical contract |
| --- | --- | --- | --- |
| T14–15 | future dependencies | `after-action-start`, `after-preview` | prospective edges and candidate graph evolve from recorded preview state |
| T16 | stable cyclic risk | `after-action-start`, `after-preview` | show stable event and full affected scope R1–R3; hold 900 ms |
| T16 | containment begins | same `after-preview` source | explanatory emphasis may reveal containment styling, but must not name a nonexistent checkpoint |
| T17 | existing authority drains | `after-completions`, `after-action-start`, `after-preview` | show committed motion and retained prospective evidence |
| T18 | quiescence | `after-completions`, `after-action-start`, `after-preview` | hold recorded quiescent contained state 800 ms |
| T18 | confirmed wait-for cycle | `after-confirmation` | replace prospective graph with recorded confirmed graph; distinguish confirmed R1–R2 core from affected R3; hold 1,000 ms |

Every tick T14–18 is emitted. The prospective graph is never carried into a
checkpoint where `frame.preview.dependencies` is absent. The confirmed graph is
never shown before T18 `after-confirmation`.

## Story D — Atomic plan splice

**Source:** `four-robot-nonparticipant`, incident 1.

**Mode:** replay-backed transaction explainer.

**Evidence boundary:** T18 `after-confirmation` to T18
`after-recovery-install`.

| Stage | Source | Contract |
| --- | --- | --- |
| proposal ready | `after-confirmation` | show proposal participants, expected versions, starts/goals, and incident identity fields actually present in replay |
| validation boundary | T18 `after-confirmation` only | show the categories that installation must revalidate as requirements, not passed results |
| atomic publication | `after-recovery-install` | switch map and cards together to R1–R3 v3 while R4 remains v2; display the single `recovery-install-succeeded` event |

The UI may show the required validation categories as requirements or a gate
description. It may not show six replay-backed checkmarks because the current
replay does not record six individual validation results. Before cards come
only from `after-confirmation`; after cards come only from
`after-recovery-install`. No `before + 1` plan-version inference is allowed.

## Story E — Asynchronous recovery through an ADG

**Source:** `three-robot-delayed`, dependency `R2@3:2 → R1@3:2`.

**Mode:** continuous runtime replay.

**Runtime interval:** T33 `after-action-start` through T36
`after-action-start`.

| Tick/checkpoint | Stage | Required display |
| --- | --- | --- |
| T33 `after-action-start` | predecessor starts | R2@3:2 running, R1@3:2 waiting/preview, actual dependency, R2 remaining ticks = 3 |
| T34 `after-action-start` | deterministic delay progresses | both action states and recorded remaining ticks |
| T35 `tick-start` and `after-action-start` | successor remains waiting | R2 still running with remaining ticks = 2 at tick start; R1 not running |
| T36 `after-completions` | dependency completes | R2@3:2 completed, R1@3:2 not yet running; hold 800 ms |
| T36 `after-admission` | successor admitted when story-relevant | recorded grant/authority state only |
| T36 `after-action-start` | successor starts | R1@3:2 running in the same phased tick; hold 1,000 ms |

Every tick T33–36 is emitted. The dependency visual is derived only by looking
up the actual dependency reference in `actions[].dependencies`. Edge
satisfaction follows predecessor action status, never stage index. Remaining
ticks come from `robots[].remaining_ticks`. The claim of zero external waits is
supporting communication-analysis evidence; it may be shown only if the story
artifact supplies that read-only report with provenance, not as an unqualified
hardcoded frame field.

## Story F — Return to lifelong operation

**Source:** `random-k3-two-recoveries-seed615`.

**Mode:** explicit montage composed of continuous sub-intervals.

The story is not an equal-speed T7–147 playback. It uses visible continuous
windows joined by labeled time compression. A montage cut may omit ticks
between windows, but no selected window may collapse to endpoint snapshots.

| Segment | Interval | Mode/rate | Required content |
| --- | --- | --- | --- |
| first incident formation | T6 `after-action-start` → T10 `after-recovery-install` | continuous, 1× | prospective cycle, containment, drain, confirmation, first splice |
| first recovery tail | T23 `after-action-start` → T26 `after-recovery-completion` | continuous, 1× | visible recovery execution and completion |
| resumed operation | T27 `tick-start` → T32 `after-action-start` | continuous, 1.5× | ordinary movement/task progress after incident release |
| later useful work | T48 `after-action-start` → T52 `after-action-start` | continuous, 1.5× | T50 task release, assignment, plan install, and movement |
| second recovery tail | T92 `after-action-start` → T95 `after-recovery-completion` | continuous, 1× | distinct later incident execution and completion |
| clean drain | T143 `after-action-start` → T147 `after-task-advance` | continuous, 2× | final useful motion, last completion/release, final task transition, and all released work drained |

Between these windows, the UI displays an explicit montage transition such as
`+15 ticks of normal operation`; it does not pretend that adjacent montage
frames are adjacent runtime frames. Montage labels use exact skipped tick
ranges computed from configured segment boundaries, not inferred incidents.

The terminal frame is T147 `after-task-advance`, where the last due action and
reservation release have occurred and the final task is recorded completed.
Clean-drain presentation states only the evidence directly available at the
terminal frame: all recorded tasks are completed, no action is running, and no
reservation remains. The replay also has top-level `termination_reason` and
`final_tick`, but the browser does not convert those fields into a new typed
`COMPLETED_AND_DRAINED` outcome. It must not require
`frame.tick >= final_tick` when the last recorded tick is `final_tick - 1`.

## Physical, logical, overlay, and caption contract

Each stage declares all visible presentation layers. Renderers must not infer
story semantics from `stageIndex`.

### Physical layer

- Grid, stations, robots, positions, plan paths, action state, and remaining
  ticks come from the current production frame.
- Running-action decoration reads `robots[].active_action_ref`, not an alias.
- A route is styled as recovery only when the current plan identity matches an
  installed recovery plan identity present in replay.

### Logical layer

- `scc` reads prospective dependencies/SCCs or confirmed wait-for graph from
  the exact configured frame; it never blends the two.
- `adg` uses recorded action references, dependencies, status, and claims.
- `splice` compares two exact production frames.
- `compiler` progressively reveals exact replay proposal/action/edge/claim
  collections.
- `lifecycle` reads recorded events and task/recovery state within the selected
  montage window.

### Overlay layer

Every overlay has a declared source:

```js
{id: "affected-scope", source: "deadlock.containment.scope"}
{id: "prospective-edges", source: "preview.dependencies"}
{id: "confirmed-edges", source: "confirmed_wait_for.edges"}
{id: "recovery-paths", source: "recovery.paths"}
{id: "action-progress", source: "robots[].remaining_ticks"}
```

An unavailable source hides the overlay or fails story validation according to
whether it is optional or required. It never substitutes a constant value.

### Captions

Captions describe the recorded state or presentation transition. A caption may
say “validation boundary” because that is the role of the production install
operation. It may not say “all six checks passed” unless six recorded outcomes
are available.

## Browser derivation boundary

The browser must not derive or invent:

- routes or path cells;
- move/wait actions;
- action dependencies or ADG edges;
- resource claims;
- plan authority or admission grants;
- prospective graph edges or SCC membership;
- affected scope or trigger core;
- confirmed wait-for edges or cyclic SCCs;
- recovery participants;
- expected or installed plan versions;
- recovery success, completion, or external-wait outcome;
- task state transitions;
- validation pass results not present in replay.

Permitted presentation derivations are limited to:

- exact frame lookup and ordering;
- grouping actions by robot;
- classifying an existing dependency as same-robot or cross-robot by comparing
  its recorded robot IDs;
- counts of displayed recorded collections;
- comparison of fields from two explicitly identified production frames;
- filtering duplicate checkpoints using the display-field rule above;
- formatting labels, elapsed tick counts, and montage gap sizes;
- progressive reveal of already-recorded fields.

Read-only communication-analysis results may be displayed only when the report
is loaded as a separately identified evidence artifact. The UI must distinguish
an analyzer assertion from a field in the current replay frame.

## Confirmed evidence sources

The current production replay artifact already includes top-level
`checkpoint_names`, `termination_reason`, and `final_tick`; no runtime schema
change is required for this playback contract. Anchor validation builds an
exact `(tick, checkpoint) → frame` map from `replay.frames` and verifies that
every configured checkpoint also appears in `checkpoint_names`. The observed
frame map is authoritative for whether a particular anchor exists.

Story D uses the following provenance split:

- proposal and validation-requirement explain items have only T18
  `after-confirmation` in `sourceFrames`;
- the atomic-publication item has T18 `after-confirmation` and T18
  `after-recovery-install` in `sourceFrames` so it may compare exact before and
  after state;
- installation success is shown only by the publication item, from the
  `recovery-install-succeeded` event in the after frame.

Story F's terminal evidence has fourteen completed tasks, no running action,
and zero reservations at T147 `after-task-advance`. The UI may present those
facts and separately label the replay's recorded `termination_reason`. It does
not synthesize another typed lifecycle outcome.

## Validation before playback

Story activation performs validation before rendering its first frame:

1. Replay source exists and schema validates.
2. Every runtime anchor resolves exactly.
3. Segment intervals and runtime stages are monotonically ordered.
4. Every continuous interval emits at least one frame per tick.
5. Required overlays have their declared source fields at required frames.
6. Explain steps name valid source fields and source frames.
7. Terminal frame exists and is the final emitted frame.
8. Story-specific identities exist, including Story E action references.
9. No runtime stage uses a checkpoint outside top-level
   `replay.checkpoint_names`, and every anchor exists in the exact frame map.

Validation failure is visible and blocks playback. It never falls back to a
nearby frame.

## Playback and narrative acceptance tests

### Controller tests

- Play advances `frameCursor`, not `stageIndex`.
- `frameCursor` indexes the emitted sequence, including explain and montage-gap
  items, and never doubles as a production replay index.
- Pause/resume preserves the exact cursor and remaining hold.
- Non-null source replay indices are monotonically increasing, including within
  one tick; montage-gap items remain ordered between their declared sources.
- Previous/next can enter and leave a montage-gap item without inventing a
  replay index.
- Completion remains on the terminal frame until explicit restart.
- Missing tick/checkpoint anchors fail validation.
- Equal-source explanatory frames are allowed only in `explain` segments.
- Continuous intervals emit at least one frame for every tick.
- Redundant checkpoint suppression never suppresses a relevant event frame.

### Story A

- The displayed paths equal all `recovery.paths` cells.
- The explainer displays 33 actions, 30 same-robot dependencies, and 10
  cross-robot dependencies for the selected fixture.
- Every displayed dependency and claim resolves to a replay action/resource.
- R4 is absent from compiler participant rows.

### Story B

- Playback emits every tick T12–34.
- T17–18 drain frames appear before confirmation.
- T19–33 each contain a recovery execution frame.
- All five recorded R4 move completions appear during the incident.
- R4 unchanged-version text is produced by exact before/after comparison.

### Story C

- Playback emits every tick T14–18.
- No `after-containment` anchor exists.
- Prospective graph frames precede the confirmed graph frame.
- Quiescence is shown before confirmation.
- Confirmed R1–R2 core and affected R3 are visually distinct.

### Story D

- Before and after cards match exact production frames.
- The UI never computes after version as `before + 1`.
- One install event changes all participant cards and the map together.
- Unrecorded validation checks are not displayed as passed.

### Story E

- Playback emits every tick T33–36.
- T36 `after-completions` precedes T36 `after-action-start`.
- R2 completion is visible while R1 is not running, followed by R1 running.
- Displayed remaining ticks equal replay robot fields.
- Edge state is independent of semantic stage index.

### Story F

- Every configured montage window plays continuously.
- Every cut identifies its omitted tick range.
- T50 release/assignment/plan events appear in the useful-work window.
- Both recovery completion events have distinct preceding execution windows.
- The terminal clean-drain frame resolves exactly and displays drained task
  state.

### Evidence/schema tests

- Reservation presentation uses `resource`, `owners`, `robot_ids`, and
  `plan_versions` from the replay schema.
- Transaction presentation does not require an absent `incident_id`.
- Running-action presentation uses `active_action_ref`.
- UI tests execute the playback controller and renderers; source-string
  presence alone is not narrative coverage.

## Implementation gate

Implementation may begin only after the interval tables, exact anchors,
required overlays, and acceptance tests above are reviewed together with the
capture storyboard. Changes to public story claims remain edits to
`docs/DEMO_AND_BLOG.md`; changes to runtime or architecture invariants remain
edits to their existing canonical documents.
