# Local scoped recovery with active non-participants

Status: completed. `docs/ARCHITECTURE.md` remains the canonical system
architecture. This document owns the detailed boundary between one local
recovery incident and unrelated active traffic.

## 1. Trigger core, affected scope, and participants

The trigger core is the plan-version-scoped cyclic SCC whose stable prospective
evidence starts containment. The affected scope is that core's frozen upstream
blocked closure. The confirmed incident records both identities and its
confirmation tick.

The affected scope is exactly the MAPF participant set. It is neither reduced to
the confirmed SCC nor expanded to all active robots. `build_recovery_proposal`
does not rediscover, enlarge, or normalize it. Active and idle robots outside the
scope are non-participants and are absent from starts, goals, solver paths,
expected versions, and replacement plans.

## 2. Controller ownership

The single active `DeadlockController` incident owns containment and recovery
authority. Scope plans stop normal replenishment while contained and, after
installation, use `RECOVERY_ADG_BOUNDED_PREFIX`. Non-participant plans remain
ordinary `NORMAL_K_CRUISE` plans and may advance tasks, acquire authority, start,
complete, and release actions throughout the incident.

v0.1 supports one active containment/recovery incident, not one globally scoped
incident. After completion and release, preview accumulation may create a later
incident with a new immutable identity and new generations. Concurrent active
incidents remain unsupported.

## 3. Replacement and version boundary

Proposal generation is read-only. Installation validates the complete affected
group before one aggregate publication and replaces only those plan versions
from `v` to `v+1`. Robot position, task, task phase, payload, and occupancy are
preserved. Non-participant robots, tasks, plans, and versions are not members of
the transaction. Failure changes neither participant nor non-participant state.

## 4. Local planning, global safety arbitration

MAPF deliberately omits non-participants, so its synchronized collision checks
apply only to the affected scope. Runtime safety remains global: normal and
recovery admission use the same authoritative occupied-cell snapshot, the same
`CommittedReservationLedger`, the same vertex and undirected-edge claims, the
same completion/release phases, and the same executor. There is no isolated
recovery occupancy or ledger and recovery has no reservation bypass.

Admission remains deterministic. Normal requests retain their existing
robot-id-ordered atomic K-window semantics. Recovery retains its deterministic
layered scan, contiguous prefixes of at most K, and aggregate publication.
Normal admission is evaluated first on every phased tick, so non-participant
traffic has admission-order priority. This is safe but may starve recovery under
continuous normal replenishment; v0.1 does not claim recovery fairness or
global liveness. If a participant and non-participant form new mutual blocking
during an active incident, the single-active-incident controller does not open
a concurrent incident.

## 5. Action dependency model

For every action after index zero, `compile_adg` adds the previous action in the
same robot plan as a natural predecessor. Shared-vertex serialization may add
zero or more cross-robot predecessors. Dependencies are collected in sets and
stored as sorted tuples, so they are deterministic and duplicate-free. Adding
cross-robot ordering never removes or replaces the natural chain.

Recovery admission interprets predecessors as follows:

- a same-robot predecessor is admissible when completed, already committed in
  the same live recovery generation, or staged earlier in this admission phase;
- a cross-robot predecessor is admissible only when completed. Staged,
  committed, running, or merely dependency-ready state is insufficient.

Admission grants authority but does not execute an action. The unified ordinary
executor starts an action only after every same-robot and cross-robot dependency
is completed and the action owns all required resources.

## 6. External wait and internal progress

A scoped recovery frontier may conflict with occupancy or a committed claim
owned by a non-participant. This is a temporary external block. The incident
stays `INSTALLED` or `EXECUTING`, records deterministic evidence, and retries on
a later tick. It is not an invalid MAPF plan and does not by itself enter
`ADMISSION_STALLED`.

An internal block is also non-terminal while any participant has running or
committed authority capable of completing and releasing resources. Recovery
waits for that in-flight progress.

An unfinished recovery becomes terminal `ADMISSION_STALLED` exactly once when
no new grant was published, no participant action is running, no participant
reservation is committed, at least one unfinished frontier is blocked, every
effective blocker is internal to the affected scope, and no currently
authorized participant progress can release it. The terminal state keeps the
existing no-automatic-retry policy. Stale generations, malformed dependencies,
non-contiguous commitments, coverage mismatch, reservation corruption, and
publication failure remain typed hard failures rather than blocking evidence.

## 7. Request identity and blocking evidence

`RecoveryAdmissionRequest` is immutable and contains the complete incident
reference (trigger core, affected scope, confirmation tick), exact installed
generations, sorted participants, K, and tick. Task names, caller flags, solver
metadata, or versions alone cannot select recovery authority.

For each first blocked recovery frontier, the result records the action,
blocking reason, concrete resource, blocker robot ids, blocker action refs, and
whether each blocker is internal or external to the participant set. Blocking
reasons remain separate from typed hard-failure reasons.

## 8. Replay and Inspector evidence

Replay snapshots expose trigger core, affected scope, participants, active
non-participants, admission profile, completed/live/staged recovery prefixes,
blocked frontier and resource, blocker robot/action and internal/external
classification, temporary external wait, terminal internal stall, lifecycle,
and frame-local normal/recovery starts and completions. Ordering is explicit and
deterministic. The Inspector renders this evidence and derives no scope,
dependency, blocker, or stall policy in JavaScript.

## 9. Acceptance tests

- ADG tests protect the natural predecessor plus additive, sorted, deduplicated
  cross-robot dependencies and completed-only executor behavior.
- Proposal and installation tests use more active robots than the affected
  scope and prove exact participant coverage and untouched non-participants.
- Integration tests prove non-participant normal progress during containment and
  recovery while all robots share global resource arbitration. The checked-in
  `compact-four-robot` scenario keeps R4 active from tick 0 across containment,
  proposal generation, atomic splice, and recovery completion.
- External-block tests prove retry without terminal stall and later recovery
  progress after release; internal-zero-progress tests prove one terminal stall.
- Lifecycle tests prove a later independently identified incident is possible
  after release.
- Replay/schema/Inspector and K=3/4/5 plus delayed recovery regressions remain
  executable acceptance evidence.

## 10. Non-goals

Concurrent incidents, global MAPF, dynamic scope expansion, idle blocker
recruitment, evacuation tasks, recovery priority over existing authority, a
second executor, conditional future ownership, physical braking, guaranteed
liveness, networking, persistence, ROS, vendor protocols, and solver portfolios
remain outside v0.1.
