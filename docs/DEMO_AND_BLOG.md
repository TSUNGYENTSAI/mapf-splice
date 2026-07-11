# Demo and Technical Article Plan

## Communication goal

The audience should understand the value proposition from a short animation:

> MAPF Splice keeps robots independent during normal operation, coordinates
> only the affected subset when a local deadlock persists, and safely splices
> synchronized recovery paths into an asynchronous fleet executor.

The v0.1 deliverable is not merely a finished runtime and test suite. It is a
clean set of visual assets, a concise README, and a technical article that
explain how bounded local MAPF recovery can be integrated into a continuously
operating asynchronous fleet. Runtime expansion stops here unless communication
work reproduces a correctness defect in an existing contract.

## Narrative backbone

README, animation, and article use the same six design problems in the same
order:

1. **From synchronized paths to executable actions.** A solver path is compiled
   into explicit move/wait actions, same-robot sequencing, and cross-robot
   precedence before it reaches the executor.
2. **Local coordination instead of continuous global MAPF.** Independent A*
   and normal traffic authority remain the default; MAPF is invoked only for a
   frozen affected subset.
3. **Detect, contain, then confirm.** A future cyclic dependency is evidence,
   not yet a deadlock. Stable prospective risk is contained, existing authority
   drains, and authoritative wait-for state confirms or clears the incident.
4. **Transactional plan splice.** Incident identity, position, task, phase,
   version, quiescence, and reservation state are revalidated before one atomic
   group transition; all participant plans change or none do.
5. **Asynchronous recovery through an ADG.** Synchronized MAPF paths become a
   dependency graph whose bounded prefixes execute through the ordinary
   asynchronous executor despite unequal deterministic action durations.
6. **Return to lifelong operation.** Recovery is not the end of a run. Robots
   finish tasks, return to dispatch, accept later work, and may complete later
   independently identified recovery incidents.

Together these answer the three integration questions a MAPF solver normally
does not answer:

```text
When to coordinate
How to replace live plans safely
How to execute the result asynchronously
```

## README communication structure

The README should ultimately use this order:

1. **Hero:** title, the one-sentence thesis above, and one 20–30 second four-
   robot animation showing normal work, local cyclic risk, scoped recovery,
   unrelated progress, ADG execution, and resumed work.
2. **Why this project exists:** briefly state the gap between a bounded
   synchronized solver output and a fleet with continuous tasks, rolling
   authority, asynchronous execution, and live plan replacement.
3. **Six feature sections:** each uses `Problem → Design response → 5–10 second
   loop → one-sentence takeaway` and follows the narrative backbone.
4. **One whole-system flow:** lifelong task stream → independent A* → rolling
   normal authority → stable local risk → contain and confirm → scoped MAPF →
   atomic splice → ADG-aware execution → lifelong operation.
5. **Project positioning:** MAPF Splice is a reference integration pattern, not
   another MAPF solver or a production fleet manager.

Long Inspector recordings, raw test matrices, field dumps, and benchmark
dashboards do not belong in the README hero path. They remain reproducible
evidence linked after the narrative.

## Visual clip contract

The shot-level implementation source is
`docs/storyboards/V0_1_CAPTURE_STORYBOARD.md`. Update that storyboard before
changing story presets or producing media. The single story interface always
keeps the physical map, one claim-specific logical visualization, and the
current lifecycle stage aligned; detailed replay state stays in the Evidence
drawer.

### Clip 1 — A path is not an execution policy

- **Problem:** synchronized timestep paths do not specify how an asynchronous
  executor should handle unequal action duration.
- **Visual:** begin with lockstep paths, then reveal move/wait actions and only
  the dependencies needed to preserve safe order while one robot progresses
  more slowly.
- **Takeaway:** MAPF paths are compiled into explicit actions and dependencies
  before entering the fleet executor.

### Clip 2 — Coordinate only what is necessary

- **Problem:** globally replanning every active robot discards useful normal
  autonomy and obscures the local nature of the incident.
- **Visual:** four active robots; R1–R3 become the affected scope while R4 keeps
  its current task and plan, remains outside MAPF/splice, and continues through
  the shared occupancy and reservation authority.
- **Takeaway:** normal robots remain independent; MAPF coordinates only the
  frozen local scope.

### Clip 3 — A predicted cycle is evidence, not yet a deadlock

- **Problem:** reacting to the first preview SCC would confuse transient future
  contention with a hard reservation deadlock.
- **Visual:** prospective dashed edges → stable cycle accent → affected-scope
  containment → committed-authority drain → authoritative confirmed wait-for
  edges. Distinguish the confirmed R1–R2 cycle core from upstream affected R3.
- **Takeaway:** observe stability, contain, drain, and confirm from current
  authority before invoking recovery.

### Clip 4 — Recovery is a transaction

- **Problem:** a recovery solution may be stale by installation time; sequential
  per-robot assignment could partially replace a live fleet.
- **Visual:** old version cards → incident-bound revalidation → one aggregate
  transition → new version cards. Do not animate a per-robot loop.
- **Takeaway:** every affected plan is replaced together or none change.

### Clip 5 — Safe without lockstep execution

- **Problem:** installed synchronized paths still cannot require simultaneous
  robot motion.
- **Visual:** synchronized paths become a small ADG; highlight the currently
  executable action, unmet predecessor, completed predecessor, and next bounded
  authority grant while one action has deterministic extra ticks.
- **Takeaway:** ADG precedence preserves the MAPF solution while the ordinary
  executor handles unequal action duration.

### Clip 6 — Recovery is part of the lifecycle

- **Problem:** stopping immediately after escape does not demonstrate fleet
  integration.
- **Visual:** active tasks → first recovery → completed tasks and redispatch →
  new work → second recovery → clean drain.
- **Takeaway:** robots return to normal dispatch and later incidents after a
  recovery is released.

## Case roles and exploration gate

The case corpus exists to produce clear visual evidence, not to increase stress
coverage indefinitely. `K=3` is the communication default. Keep one calibrated
three-robot hero for graph semantics and use seeded four-robot cases for
lifelong and local-scope evidence.

Purpose-built fixtures are audited before any seed search. Random-corpus scope
statistics do not invalidate a deterministic fixture designed for a specific
proof. Existing cases have these intended roles:

| Communication proof | Priority source |
| --- | --- |
| normal independent operation | random seed 590 |
| synchronized paths → actions/dependencies | a clean recovery proposal; diagram/plan-card extraction is allowed |
| detect, contain, confirm | calibrated three-robot hero |
| exactly-three local scope and unchanged fourth robot | purpose-built `four-robot-nonparticipant` fixture |
| transactional splice | the same four-robot fixture and installation checkpoint |
| delayed cross-robot ADG unlock | purpose-built `three-robot-delayed` fixture |
| repeated lifelong recovery | random seed 615 |
| external-wait supporting evidence | random seed 1043 |

The evidence predicates are strict:

1. **Paths are not executable actions:** show synchronized solver positions,
   compiled move/wait actions, same-robot dependencies, and at least one cross-
   robot dependency. All dependencies must come from production replay/kernel
   output; the browser derives none.
2. **Local coordination, not global MAPF:** all four robots are active before
   containment; affected scope, proposal, and installed plan sets are exactly
   three; the fourth robot is absent from all three, keeps the same plan version
   across splice, completes a visible move during the incident, and later task
   progress occurs. No unrelated cycle may interfere with the interval.
3. **Detect, contain, confirm:** one compact window must show prospective
   dependency, stable cyclic SCC, distinct cycle core and affected scope,
   containment, committed-authority drain, quiescence, and authoritative
   confirmed wait-for cycle.
4. **Transactional splice:** capture participant versions immediately before
   and after installation, the incident/version/task/phase/position/reservation
   validation boundary, one atomic transition, and an unchanged non-participant
   version.
5. **Delayed ADG handoff:** participant A receives nonzero deterministic extra
   delay on an action that is a cross-robot predecessor of participant B. B
   remains unable to execute until A completes, then receives authority or
   starts within one or two deterministic phases. External blockers or another
   resource conflict must not explain the wait.
6. **Lifelong continuation:** first recovery completes and releases; normal task
   progress and later dispatch resume; a distinct second incident completes;
   the bounded run drains cleanly.

## Communication proof curation workflow

Do not browse hundreds of ticks or seeds manually. A read-only discovery tool
analyzes production replay and reports candidate intervals with case, incident,
tick bounds, cycle core, affected scope, active non-participants, plan versions,
non-participant completions, cross-robot predecessors, delay ticks, unlock tick,
external blockers, and unrelated event count. The Inspector remains the final
human visual-review surface.

Exploration proceeds in three rounds:

1. **Audit existing fixtures:** `four-robot-nonparticipant`,
   `three-robot-delayed`, and seed 1043 external-wait intervals. No new seed is
   introduced in this round.
2. **Fill only proven gaps:** if local-scope evidence fails, keep topology,
   K=3, and zero delay fixed and search workload seed only. If delayed handoff
   fails, keep workload/topology fixed and search delay seed only.
3. **Freeze the media corpus:** each selected clip records source config, replay
   hash, incident number, tick/checkpoint bounds, narrative title,
   communication claim, and visual annotations.

Run the read-only audit against a checked-in config with:

```bash
uv run mapf-splice-communication-proofs \
  --config validation/lifelong/four-robot-nonparticipant.json \
  --output artifacts/communication-proofs/four-robot-nonparticipant.json
```

The analyzer consumes the same production replay as the Inspector and never
routes, admits traffic, creates dependencies, selects scope, or changes runtime
state.

### Round-one fixture audit

| Claim | Source and window | Result |
| --- | --- | --- |
| paths → executable actions | four-robot fixture, incident 1 at confirmation tick 18 | PASS: 3 synchronized paths → 33 move actions, 30 same-robot and 10 cross-robot dependencies |
| local coordination | four-robot fixture, tick 16–34 | GAP: all core predicates pass, including R4 `v2 → v2` and 5 R4 completions; no task progress occurs after recovery because release stops after bootstrap |
| detect → contain → confirm | three-robot hero, tick 14–18 | PASS: stable/containment tick 16, quiescence/confirmation tick 18, prospective core/scope R1–R3, confirmed cyclic SCC R1–R2 |
| transactional splice | four-robot fixture, tick 18 `after-confirmation → after-recovery-install` | PASS: R1–R3 change `v2 → v3` together; R4 remains `v2` |
| delayed ADG unlock | three-robot delayed, `R2@3:2 → R1@3:2`, tick 33–36 | PASS: predecessor receives 2 extra ticks, completes at tick 36, successor starts at tick 36, no external wait |
| lifelong continuation | seed 615 | PASS: recovery completions at ticks 26 and 95 with intervening task progress and clean drain |
| external wait support | seed 1043, incident 3 | PASS supporting evidence: external-wait ticks 83–89, followed by recovery prefix grant at tick 90 |

The delayed proof therefore requires no new delay seed. The four-robot fixture
is already the correct local-scope and splice source; only the strict
post-recovery task-progress predicate remains unresolved for using the same
window as the complete README hero.

Do not search for more recovery counts, larger fleets, random maps, concurrent
incidents, global-MAPF comparisons, or throughput winners. A new case is kept
only when its replay offers a clearer frame sequence than the current candidate
for one named communication role.

## Hero environment

The demo uses a compact warehouse grid with:

- visible shelves and constrained aisles;
- pickup and drop-off stations;
- enough alternate space for meaningful retreat and passing maneuvers;
- a continuous, deterministic task stream;
- visually distinct empty and carrying robots;
- a deterministic timing policy shared by every comparison mode. The canonical
  hero uses zero normal-operation delay so topology and traffic policy alone
  establish the SCC; an explicit delayed recovery action later demonstrates
  asynchronous ADG execution.

The first scenario should create deadlocks naturally from task traffic rather
than start in a pre-constructed deadlock configuration.

![Compact synthetic three-robot warehouse](assets/compact-three-robot-warehouse.png)

The canonical `21 × 15` robot grid keeps human loading zones outside the routing
graph. Three boundary handoff stations feed five carrying-only delivery berths.
The bootstrap workload creates a real three-robot prospective SCC with multiple
blockers; the upper loop remains available for scoped MAPF recovery. Generated
runtime replays support `K=3`, `K=4`, and `K=5` from the same scenario.

The canonical release ticks are `T1=5`, `T2=0`, and `T3=12`. The first cyclic
observation is still the transient pair `R1@2,R3@2`; it never reaches the
stability threshold before the graph expands. The first stable SCC and
containment are exactly `R1@2,R2@2,R3@2` at ticks 16, 15, and 14 for K=3, 4,
and 5 respectively. All three runs reach quiescence at tick 18 with planned
actions remaining. This is prospective containment evidence only; confirmed
wait-for analysis is required before calling it a hard reservation deadlock.

The checked-in bitmap shows static topology only. Runtime screenshots and
animation must select frames from a replay generated by:

```bash
uv run mapf-splice-run \
  --scenario scenarios/compact-three-robot/scenario.json \
  --committed-horizon 3 \
  --until quiescence \
  --max-ticks 200 \
  --output artifacts/hero-k3.run.json
```

Regenerate the topology image independently with:

```bash
uv run mapf-splice-render \
  --scenario scenarios/compact-three-robot/scenario.json \
  --output docs/assets/compact-three-robot-warehouse.png
```

The v0.1 communication artifact demonstrates MAPF Splice alone. A rule-based
side-by-side comparison is deferred and is not a release requirement.

## Deferred comparison, not a v0.1 communication blocker

Rule-based and global-MAPF comparisons are possible later, but they are not
required to explain or complete the v0.1 integration pattern. The primary
artifact compares system states across one MAPF Splice lifecycle, not two
competing controllers. If a later comparison is added, both sides must receive
the same scenario inputs and neither baseline may be intentionally weakened.

### Rule-based traffic

- Minimal dispatcher and independent A* routes.
- The same committed-droplet and preview-horizon policy as MAPF Splice.
- A small, documented heuristic set, such as fixed priority and one-node
  backtracking.
- Safe admission but possible deadlock, livelock, oscillation, or throughput
  collapse.

The comparison must not implement intentionally weak rules. The point is that
reasonable local heuristics are scenario-dependent and difficult to compose,
not that all rule-based control is ineffective.

### MAPF Splice

- Identical normal task, routing, droplet, and timing behavior.
- Stable prospective-cycle containment followed by confirmed wait-for SCC
  detection.
- Visible selection of a local recovery group.
- MAPF-generated retreat, passing, or rerouting maneuver.
- ADG execution with at least one delayed robot.
- Return to the continuing task stream after recovery.

## Visual language

The animation should make hidden runtime state visible:

- planned A* route: thin robot-colored line;
- committed droplet: translucent reserved cells and edges;
- read-only preview horizon: dashed or lighter continuation beyond the droplet;
- preview conflict: highlighted committed resource and prospective blocker
  edge;
- stable cyclic risk: an amber directed overlay and candidate-group outline;
- confirmed hard-deadlock SCC: a red common outline around the selected group;
- MAPF recovery path: brighter replacement route;
- unmet ADG dependency: labeled waiting indicator;
- delayed robot: clear status marker;
- completed recovery: transition back to normal visual styling.

The visualizer must remain a trace consumer. It must not contain planning,
traffic, or execution decisions.

## Minimal evidence shown at the end

- completed tasks;
- hard deadlocks detected;
- recoveries installed and completed;
- participants per recovery;
- safety invariant violations;

Publication-quality throughput, timing dashboards, solver-wall-clock
comparisons, and reservation-utilization analysis are outside v0.1. The final
frame should reinforce lifecycle completion and safety, not imply a benchmark.

## Suggested animation sequence

1. Introduce the warehouse, robots, tasks, and normal A* routes.
2. Reveal committed droplets as robots maintain cruise motion authority and
   carry loads.
3. Extend the view beyond each committed droplet to reveal the read-only preview
   horizon.
4. Show a prospective dependency cycle while the robots are still separated.
5. Stop extending the affected droplets and let the robots reach safe,
   deterministic quiescent positions.
6. Confirm that the reservation cycle persists and highlight the small subset
   sent to MAPF.
7. Reveal the solver-generated recovery maneuver.
8. Show the ADG dependency order while one robot is delayed.
9. Resume continuous work and present the minimal completion and safety
   evidence. A later comparison cut may place another controller beside the
   same scenario, but is not part of the core artifact.

During steps 6–8, unrelated active robots remain visible and continue ordinary
traffic. They are absent from the scoped MAPF splice but still arbitrate all
occupied and committed resources through the same global ledger; a temporary
external conflict pauses and retries recovery without being presented as a
terminal recovery failure.

## Technical article outline

1. **The integration gap**

   A MAPF solver returns a synchronized plan, while a fleet system has continuous
   tasks, rolling reservations, delays, and existing traffic control.

2. **Why local rules grow**

   Explain the usefulness and compositional limits of priority, yielding, and
   backtracking rules without dismissing them as inherently bad.

3. **Normal operation**

   Minimal dispatch, independent A*, committed droplets, preview horizons,
   admission, and deterministic progress.

4. **Recognizing a hard deadlock**

   Prospective dependencies, stable cyclic risk, containment, confirmed
   wait-for graph, SCC cycle core, and progress-aware stability.

5. **MAPF as an intervention**

   Snapshot the authoritative simulation state, solve only the affected group,
   validate the result, and replace live plans.

6. **From synchronized plan to asynchronous execution**

   ADG compilation, dependency completion, resource claims, and plan versions.

7. **Evidence**

   Animation, deterministic trace, selected seeded cases, completed task and
   recovery counts, invariant status, and typed failure behavior.

8. **Limits and next steps**

   Footprints, idle blockers, physical adapters, richer dispatch, and stronger
   liveness arguments.

## Acceptance criteria for the communication artifacts

- A viewer can identify the deadlock and recovery group without narration.
- The animation shows why MAPF and ADG solve different problems.
- Every published case names its scenario, workload seed, delay seed, and K.
- The article states all modeling assumptions and non-goals.
- The repository contains one canonical command for reproducing the published
  demo replay and minimal evidence summary.
