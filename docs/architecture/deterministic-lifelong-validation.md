# Deterministic lifelong validation on fixed topologies

Status: completed. `docs/ARCHITECTURE.md` remains the canonical system
architecture. This document owns the bounded validation contract for the final
non-UI v0.1 milestone.

## 1. Purpose and fixed topologies

The milestone verifies repeated dispatch, pickup/drop-off lifecycles, increasing
plan generations, deterministic delays, sequential recovery incidents, and
shared traffic authority over a bounded run. It uses only the checked-in
`compact-three-robot` hero topology and `compact-four-robot` active
non-participant topology. Map cells, stations, and initial robot positions are
never randomized.

## 2. Seeded workload

The existing scenario `task_stream` remains authoritative. Bootstrap tasks are
loaded unchanged. The configured generator supplies pickup and delivery station
sets plus an inclusive release-interval range. A project-owned deterministic
generator uses an explicit workload seed for station-pair and interval choices,
emits ordered nonnegative release ticks, and assigns stable unique task ids.
Pickup and delivery must differ as cells and every generated pair must have a
static route. The existing delay schedule remains independently controlled by
its seed, probability, and extra-tick range.

## 3. Bounded release and drain

Generated tasks are materialized only through `release_until_tick`, inclusive.
After that boundary no new work is created. The simulator continues for at most
the configured drain ticks (and always under an absolute final tick bound). A
drained run has every released task completed; all robots idle; no plans,
running actions, reservations, active containment, or active recovery incident.
Completed `Task.assigned_robot_id` is intentionally retained as historical
provenance; it is harmless only because no robot points back to that task and no
plan, payload, action, or reservation remains active.

## 4. Progress and watchdog

Production trace events define observable progress: task assignment or status
change, plan installation, action start/completion, stable SCC detection,
containment transition, recovery proposal/installation/admission progress,
recovery completion, and containment release. Reservation-frontier progress is
represented by accepted admission or recovery-prefix grants. A future release,
a running multi-tick action, or a deterministic delay is legitimate waiting and
does not expire the watchdog. The runner records the last progress tick, maximum
and current no-progress intervals, and final blocking evidence.

## 5. Typed termination

Every run ends as exactly one of:

- `COMPLETED_AND_DRAINED`: all released work and aggregate authority drained.
- `HORIZON_REACHED_SAFE`: bounded stress horizon ended in a valid world.
- `NO_PROGRESS_TIMEOUT`: no production progress within the configured bound.
- `SAFE_RECOVERY_FAILURE`: a typed planning, installation, admission, or stall
  path preserved valid authoritative state.
- `EXPECTED_UNSUPPORTED_BOUNDARY`: a documented v0.1 boundary was encountered.
- `INVARIANT_VIOLATION`: project-owned validation rejected aggregate state.
- `UNHANDLED_EXCEPTION`: an exception escaped the supported runtime paths.

Timeout is diagnostic rather than automatically equivalent to an invariant
failure. Supported draining acceptance cases must finish
`COMPLETED_AND_DRAINED`.

## 6. Safety and consistency invariants

`WorldState.validate()` remains authoritative for occupancy, task/robot and
payload consistency, current plan generations, running-action ownership,
reservation validity, and dependency completion. Validation checkpoints also
require completed tasks to be unassigned, completed actions to own no claims,
idle robots to own no execution state, recovery participants to equal the frozen
affected scope, non-participants to remain outside proposal/splice generations,
and released incidents to retain no live installed-generation authority.
Sequential incidents must have distinct immutable incident identities.

## 7. Acceptance corpus

Checked-in configurations cover: the K=3 three-robot lifelong baseline; K=4 and
K=5 horizon variants; nonzero deterministic delay and exact rerun equality; the
four-robot case where R4 is active before containment and remains outside the
three-robot recovery; two production-stream sequential recovery lifecycles; and
a production-faithful typed safe recovery failure. Tests use public runtime APIs,
bounded loops, and no private ledger/controller mutation.

## 8. Diagnostic and failure artifacts

Each run may write `config.json`, `summary.json`, and `replay.json`. A run that
misses its expected outcome also writes `failure.json` with classification and
tick, last progress, active robot/task state, generations, running actions,
committed reservations, containment/recovery state, stable blocker evidence,
and exception type/message where applicable. JSON uses sorted project-owned
representations; arbitrary object `repr` is not an interchange format. Generated
run artifacts are not checked in.

## 9. Non-goals and unsupported boundaries

This contract does not claim random/procedural topology robustness, general
fuzzing, fairness, starvation freedom, global liveness, concurrent incidents,
dynamic containment expansion, idle-blocker recruitment, global MAPF, rule-based
comparison, performance benchmarking, failure minimization, or production
safety. Normal-first admission starvation, a second incident while one is
active, external-scope dependencies, and independent concurrent cyclic cores
remain typed or diagnosable unsupported outcomes.

## 10. Verified corpus and reproduction

All supported cases use workload seed `12` and delay seed `7719`. The baseline,
K variants, and four-robot case have zero delay probability. The delayed case
uses probability `0.35` and one to three extra ticks. Generated release stops at
tick 35. The selected stream creates two sequential recovery incidents through
ordinary task dispatch and runtime observation; no controller state is reset.

Run one case with:

```console
uv run mapf-splice-lifelong \
  --config validation/lifelong/three-robot-k3.json \
  --output artifacts/lifelong-validation/three-robot-k3
```

Replace the config name with `three-robot-k4`, `three-robot-k5`,
`three-robot-delayed`, `four-robot-nonparticipant`, or `safe-solver-failure`.
The checked-in corpus verified these outcomes:

| Case | Final tick | Tasks | Recoveries | Termination |
| --- | ---: | ---: | ---: | --- |
| three-robot K=3 | 71 | 5/5 | 2/2 | `COMPLETED_AND_DRAINED` |
| three-robot K=4 | 71 | 5/5 | 2/2 | `COMPLETED_AND_DRAINED` |
| three-robot K=5 | 72 | 5/5 | 2/2 | `COMPLETED_AND_DRAINED` |
| deterministic delay | 122 | 5/5 | 2/2 | `COMPLETED_AND_DRAINED` |
| four-robot non-participant | 35 | 4/4 | 1/1 | `COMPLETED_AND_DRAINED` |
| solver max-timestep failure | 19 | 0/3 | 0/0 | `SAFE_RECOVERY_FAILURE` |

Every row completed with zero aggregate invariant violations. Two independent
runs of the delayed config produced byte-identical `summary.json` and
`replay.json`. The failure case uses the production planner's existing bounded
`max_timestep` input (`1`) and preserves zero installed recovery generations.
