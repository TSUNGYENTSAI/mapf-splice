# Recovery ADG bounded-prefix admission

Status: accepted implementation contract for the first executable recovery
milestone. `docs/ARCHITECTURE.md` remains the canonical system architecture;
this focused document owns the detailed recovery-admission algorithm.

## 1. Problem statement

Normal cruise authority atomically acquires `min(K, remaining actions)`. A MAPF
solution instead compiles safety into ADG precedence. In the installed hero
plans for K=3/4/5, ordinary admission reports safe prefixes R1=0, R2=0, R3=2,
rejects all three full windows, and starts no recovery action. Globally allowing
partial initial prefixes was incorrect because it weakened the braking-distance
contract for every normal plan.

## 2. Explicit admission profiles

`NORMAL_K_CRUISE` retains atomic initial K-window admission, diagnostic-only
partial prefixes, and existing rolling replenishment.

`RECOVERY_ADG_BOUNDED_PREFIX` applies only to the controller-owned installed
recovery generations. Each robot may hold a contiguous prefix of 0..K actions;
fewer than K is valid. A phase may grant only a subset of robots, but publishes
all grants atomically. Sparse prefixes are forbidden. This profile assumes the
vehicle controller guarantees action-boundary stoppability under the selected
low-speed recovery profile; arbitrary hardware braking safety is not proven.

## 3. Domain and ownership boundary

The controller's active incident reference plus its recorded installed plan
versions identify recovery plans. The simulator creates an explicit immutable
recovery admission request from that state. Task names, versions alone, caller
booleans, and solver metadata never select the profile. Generic `Plan` stays
free of incident lifecycle metadata.

## 4. Request and result types

`RecoveryAdmissionRequest` contains incident reference, installed versions,
sorted participants, K, and tick. `RecoveryRobotAdmissionResult` contains robot
and version, completed prefix, existing committed prefix, capacity, candidate
frontier, evaluation order, grants, first blocked action/reason, and resulting
prefix. `RecoveryAdmissionResult` contains all robot results, staged grants,
publication status, new/existing authority, stall classification, and an
optional typed `RecoveryAdmissionFailure`. All collections are tuples or
defensive immutable mappings.

`RecoveryBlockedReason` distinguishes unmet cross-robot dependency, occupied
resource, live committed conflict, staged conflict, no capacity, and complete
plan. `RecoveryAdmissionFailureReason` distinguishes absent/replaced incident,
generation or coverage mismatch, stale versions, task/phase change, invalid
frontier/plan, reservation mismatch, and atomic publication failure.

## 5. Deterministic layered scan

Participants use active scope order, never mapping or solver order. For A/B/C,
the observable evaluation order is A first extension, B first, C first, A
second, B second, C second, through K layers. Extension numbering is relative
to each live execution/commit frontier.

## 6. Contiguous-prefix invariant

For every robot, derive its sequential completed prefix, live sequential
committed prefix, next frontier, and `K - committed_count`. A blocked candidate
marks that robot blocked for the phase; its later suffix is not evaluated.
Other robots continue. A later tick may reevaluate it.

## 7. Same-robot predecessors

A same-robot predecessor permits staging when completed, live committed, or
staged earlier in this phase. This permits a safe contiguous prefix up to K;
the unified executor still starts actions sequentially.

## 8. Cross-robot predecessors

Only `COMPLETED` satisfies a cross-robot dependency. Staged, live committed,
running, or merely dependency-ready predecessors do not. An unmet predecessor
blocks that robot's suffix for the phase while other robots continue.

## 9. Resource conflicts

Candidates use the ledger's canonical destination-vertex and undirected-edge
claims against authoritative occupancy, live reservations, and earlier staged
grants. Conflict blocks only that robot's suffix for the phase. Existing
same-plan overlap semantics remain canonical.

## 10. Blocking versus hard failure

Ordinary frontier blocking produces a valid zero/short prefix. Hard failures
include stale incident/generation/task/phase, malformed plans or dependencies,
noncontiguous live commitments, stale reservations, and coverage mismatch. Hard
failure publishes no staged grant and transitions through typed controller
evidence to fail safe.

## 11. Atomic group publication

The scan operates on a staged owner snapshot. It may yield R1=0, R2=0, R3=2,
but validates the entire phase before one ledger publication. No intermediate
grant is observable and rollback is not the primary mechanism.

## 12. No fixed-point rescan

Blocked robots are not rescanned in a phase. If A0 depends on B0 and B0 is
staged later, A0 stays blocked because B0 is not completed. A is reconsidered
only in a later phase after completion.

## 13. Recovery lifecycle

States are `NOT_ATTEMPTED`, `PROPOSAL_READY`, `INSTALLED`, `EXECUTING`,
`COMPLETED`, plus planning/install/admission failure and
`ADMISSION_STALLED`. Installation enters `INSTALLED`; it does not imply motion.
The first actual recovery `ACTION_STARTED` enters `EXECUTING`. A zero-action
group may complete from `INSTALLED`. Zero grants with unfinished plans and no
running or committed authority enters terminal `ADMISSION_STALLED` with blocked
frontier evidence and no automatic retry. Completion requires exact installed
generations, no in-flight authority, all actions completed, and goal positions.

## 14. Simulator tick order

Ticks complete actions, record completions, release reservations, record
release, validate/detect/record recovery completion, prevent unfinished recovery
task advancement, advance other tasks, then split admission. Normal plans use
normal admission; the exact installed group uses bounded-prefix admission.
Decisions are recorded, the common executor starts eligible actions and updates
lifecycle on real start, then preview/confirmation/proposal/atomic installation
run. First recovery admission normally occurs one tick after installation.

## 15. Unified executor

Recovery uses the ordinary executor for completed-dependency checks, ownership,
position, duration/delay, completion, and release. There is no MAPF-timestep
executor and no ownership bypass.

## 16. Replay and Inspector evidence

Every recovery admission phase records profile, incident, K, participant and
layer order, installed versions, per-robot completed/live prefixes, capacity,
frontier, grants, first block and reason, resulting prefix, group grants,
atomic outcome, lifecycle transition, and stall evidence. Trace events are
`RECOVERY_ADMISSION_EVALUATED`, `RECOVERY_PREFIX_GRANTED`,
`RECOVERY_ADMISSION_FAILED`, and `RECOVERY_ADMISSION_STALLED`; ordinary action
events identify installed recovery generations. Inspector renders proposal,
installed, authority granted, executing, stalled, and completed states without
deriving policy.

## 17. Safety and liveness claims

Software targets are collision-free vertices/edges, authority before start,
completed dependencies before dependent starts, contiguous bounded prefixes,
unchanged normal admission, atomic recovery grants, and stale-state rejection.
For the supported hero, grants progress as completions release resources and
unlock cross-robot successors until goals, incident release, and normal task
resumption. Physical braking is an external low-speed action-boundary
stoppability assumption.

## 18. Non-goals

This design excludes conditional future ownership/Option A, global normal
partial prefixes, a separate executor, idle recruitment, evacuation goals,
dynamic/nonparticipant scope changes, concurrent incidents, retry/fallback,
distributed transactions, and physical braking implementation.

## 19. Implementation plan

- Keep `CommittedReservationLedger.admit_batch` behavior unchanged and expose it
  as the normal-policy entry point; share only conflict/staging primitives.
- Add immutable recovery request/result/failure types and the layered algorithm
  in `traffic.py` (reservation authority), with controller-owned request
  creation and lifecycle/evidence in `deadlock.py`.
- Stage owner/resource copies and publish through one ledger commit after full
  validation.
- Split simulator admission so exact installed generations cannot enter normal
  admission; reuse `_start_actions` and notify the controller on actual starts.
- Extend trace, replay schema/serialization, and Inspector with admission and
  lifecycle evidence.
- Add traffic unit tests for normal parity, layer order, prefixes, dependencies,
  conflicts, atomicity and stall; controller/simulator tests for lifecycle and
  stale state; hero/delay/replay tests for K=3/4/5 completion and resumption.
- After tests pass, reconcile names and paths here and update canonical scope
  documents and packaged replay/Inspector assets.
