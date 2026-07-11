from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema

from mapf_splice.deadlock import (
    DeadlockController,
    DeadlockUpdate,
    cyclic_sccs,
)
from mapf_splice.domain import ActionRef, EdgeResource, Resource, VertexResource
from mapf_splice.preview import PreviewAnalysis, preview_actions
from mapf_splice.recovery import RecoveryState
from mapf_splice.scenario import ScenarioBundle
from mapf_splice.trace import EventTrace, TraceEvent
from mapf_splice.world import WorldState

CHECKPOINTS = (
    "tick-start",
    "after-completions",
    "after-release",
    "after-task-advance",
    "after-admission",
    "after-action-start",
    "after-preview",
    "after-confirmation",
)


def _cell(cell) -> dict[str, int]:
    return {"row": cell.row, "col": cell.col}


def _ref(ref: ActionRef | None) -> dict[str, Any] | None:
    if ref is None:
        return None
    return {
        "robot_id": ref.robot_id,
        "plan_version": ref.plan_version,
        "action_index": ref.action_index,
        "label": f"{ref.robot_id}@{ref.plan_version}:{ref.action_index}",
    }


def _resource(resource: Resource) -> dict[str, Any]:
    if isinstance(resource, VertexResource):
        return {"type": "vertex", "cell": _cell(resource.cell)}
    assert isinstance(resource, EdgeResource)
    return {
        "type": "edge",
        "first": _cell(resource.first),
        "second": _cell(resource.second),
    }


def _identity(identity) -> list[dict[str, Any]]:
    return [
        {"robot_id": robot_id, "plan_version": version}
        for robot_id, version in identity
    ]


def _event(event: TraceEvent) -> dict[str, Any]:
    return {
        "sequence": event.sequence,
        "tick": event.tick,
        "phase": event.phase.name.lower().replace("_", "-"),
        "kind": event.kind.value,
        "robot_id": event.robot_id,
        "task_id": event.task_id,
        "action_ref": _ref(event.action_ref),
        "details": {key: value for key, value in event.details},
    }


def _confirmed_edge(edge) -> dict[str, Any]:
    return {
        "waiting_robot_id": edge.waiting_robot_id,
        "waiting_plan_version": edge.waiting_plan_version,
        "waiting_action_ref": _ref(edge.waiting_action_ref),
        "resource": _resource(edge.resource),
        "blocking_robot_id": edge.blocking_robot_id,
        "blocking_plan_version": edge.blocking_plan_version,
        "committed_blocker_refs": [_ref(ref) for ref in edge.committed_blocker_refs],
        "occupied_blocker": edge.occupied_blocker,
        "blocking_in_scope": edge.blocking_in_scope,
    }


def _confirmed_graph(containment) -> dict[str, Any]:
    graph = containment.confirmed_graph
    return {
        "scope": _identity(graph.scope),
        "captured_at_tick": graph.captured_at_tick,
        "outcome": containment.outcome.value if containment.outcome else None,
        "state": containment.state.value,
        "edges": [_confirmed_edge(edge) for edge in graph.edges],
        "cyclic_sccs": [list(scc) for scc in graph.cyclic_sccs],
    }


def _recovery(containment) -> dict[str, Any] | None:
    if containment is None or containment.recovery_state is RecoveryState.NOT_ATTEMPTED:
        return None
    proposal = containment.recovery_proposal
    if proposal is not None:
        participants = sorted(proposal.plans)
        return {
            "state": containment.recovery_state.value,
            "failure_reason": None,
            "participants": participants,
            "starts": [
                {"robot_id": r, "cell": _cell(proposal.starts[r])}
                for r in participants
            ],
            "goals": [
                {"robot_id": r, "cell": _cell(proposal.goals[r])}
                for r in participants
            ],
            "solver": {
                "solver": proposal.metadata.solver,
                "seed": proposal.metadata.seed,
                "max_timestep": proposal.metadata.max_timestep,
                "makespan": proposal.metadata.makespan,
                "source_commit": proposal.metadata.source_commit,
            },
            "adg_compiled": True,
            "paths": [
                {
                    "robot_id": r,
                    "cells": [_cell(cell) for cell in proposal.solution.paths[r]],
                }
                for r in participants
            ],
        }
    failure = containment.recovery_failure
    return {
        "state": containment.recovery_state.value,
        "failure_reason": failure.reason.value if failure else None,
        "participants": [robot_id for robot_id, _ in containment.identity],
        "starts": [],
        "goals": [],
        "solver": None,
        "adg_compiled": False,
        "paths": [],
    }


@dataclass(slots=True)
class FrameRecorder:
    scenario: ScenarioBundle
    frames: list[dict[str, Any]] = field(default_factory=list)
    _event_cursor: int = 0
    _committed_horizon: int | None = None

    def record(
        self,
        *,
        checkpoint: str,
        world: WorldState,
        controller: DeadlockController,
        trace: EventTrace,
        preview_analysis: PreviewAnalysis | None = None,
        deadlock_update: DeadlockUpdate | None = None,
    ) -> None:
        if checkpoint not in CHECKPOINTS:
            raise ValueError(f"unknown replay checkpoint: {checkpoint}")
        if self._committed_horizon is None:
            self._committed_horizon = world.reservations.horizon
        analysis = preview_analysis or PreviewAnalysis((), ())
        controller_state = controller.snapshot()
        preview_refs = {
            action.ref
            for plan in world.plans.values()
            for action in preview_actions(world, plan)
        }
        committed_refs = set(world.reservations.all_committed_actions())
        containment = controller_state.containment
        contained_members = (
            set(containment.identity) if containment is not None else set()
        )
        robots = []
        for robot_id, robot in sorted(world.robots.items()):
            task = world.tasks.get(robot.active_task_id or "")
            robots.append(
                {
                    "robot_id": robot_id,
                    "position": _cell(robot.position),
                    "active_task_id": robot.active_task_id,
                    "payload_task_id": robot.payload_task_id,
                    "task_status": task.status.value if task else None,
                    "plan_version": robot.plan_version,
                    "active_action_ref": _ref(robot.active_action_ref),
                    "remaining_ticks": robot.remaining_ticks,
                    "contained": (robot_id, robot.plan_version) in contained_members,
                }
            )
        plans = []
        for robot_id, plan in sorted(world.plans.items()):
            actions = []
            for action in plan.actions:
                if action.status.value == "completed":
                    authority = "completed"
                elif action.status.value == "running":
                    authority = "running"
                elif action.status.value == "canceled":
                    authority = "canceled"
                elif action.ref in committed_refs:
                    authority = "committed"
                elif action.ref in preview_refs:
                    authority = "preview"
                else:
                    authority = "future"
                actions.append(
                    {
                        "action_ref": _ref(action.ref),
                        "action_index": action.ref.action_index,
                        "kind": action.kind.value,
                        "start": _cell(action.start),
                        "end": _cell(action.end),
                        "status": action.status.value,
                        "duration_ticks": action.duration_ticks,
                        "dependencies": [_ref(value) for value in action.dependencies],
                        "claims": [_resource(value) for value in action.claims],
                        "committed": action.ref in committed_refs,
                        "preview": action.ref in preview_refs,
                        "running": action.ref
                        == world.robots[robot_id].active_action_ref,
                        "display_authority": authority,
                    }
                )
            plans.append(
                {
                    "robot_id": robot_id,
                    "task_id": plan.task_id,
                    "plan_version": plan.version,
                    "phase_goal": _cell(plan.phase_goal),
                    "initialized": world.reservations.plan_initialized(plan),
                    "actions": actions,
                }
            )
        reservations = []
        for resource, owners in world.reservations.reservation_snapshot():
            reservations.append(
                {
                    "resource": _resource(resource),
                    "owners": [_ref(owner) for owner in owners],
                    "robot_ids": sorted({owner.robot_id for owner in owners}),
                    "plan_versions": sorted({owner.plan_version for owner in owners}),
                }
            )
        dependencies = [
            {
                "waiting_robot_id": item.waiting_robot_id,
                "waiting_plan_version": item.waiting_plan_version,
                "preview_action_ref": _ref(item.preview_action_ref),
                "blocking_robot_id": item.blocking_robot_id,
                "blocking_plan_version": item.blocking_plan_version,
                "resource": _resource(item.resource),
                "blocking_action_refs": [
                    _ref(value) for value in item.blocking_action_refs
                ],
                "occupied_blocker": item.occupied_blocker,
            }
            for item in analysis.dependencies
        ]
        contentions = [
            {
                "resource": _resource(item.resource),
                "robot_ids": list(item.robot_ids),
                "action_refs": [_ref(value) for value in item.action_refs],
            }
            for item in analysis.contentions
        ]
        events = trace.events[self._event_cursor :]
        self._event_cursor = len(trace.events)
        self.frames.append(
            {
                "index": len(self.frames),
                "tick": world.tick,
                "checkpoint": checkpoint,
                "robots": robots,
                "tasks": [
                    {
                        "task_id": task_id,
                        "status": task.status.value,
                        "assigned_robot_id": task.assigned_robot_id,
                        "pickup": _cell(task.pickup),
                        "dropoff": _cell(task.dropoff),
                    }
                    for task_id, task in sorted(world.tasks.items())
                ],
                "plans": plans,
                "reservations": reservations,
                "preview": {
                    "dependencies": dependencies,
                    "contentions": contentions,
                    "cyclic_sccs": [list(value) for value in cyclic_sccs(analysis)],
                },
                "deadlock": {
                    "threshold": controller_state.threshold,
                    "candidates": [
                        {
                            "identity": _identity(item.identity),
                            "observation_count": item.observation_count,
                            "stable": item.stable,
                        }
                        for item in controller_state.candidates
                    ],
                    "containment": (
                        None
                        if containment is None
                        else {
                            "identity": _identity(containment.identity),
                            "state": containment.state.value,
                            "confirmation_tick": containment.confirmation_tick,
                            "outcome": (
                                containment.outcome.value
                                if containment.outcome
                                else None
                            ),
                        }
                    ),
                    "newly_stable": [
                        _identity(value)
                        for value in (deadlock_update.stable if deadlock_update else ())
                    ],
                },
                "confirmed_wait_for": (
                    _confirmed_graph(containment)
                    if containment is not None
                    and containment.confirmed_graph is not None
                    else None
                ),
                "recovery": _recovery(containment),
                "events": [_event(value) for value in events],
            }
        )

    def artifact(self, *, termination_reason: str, final_tick: int) -> dict[str, Any]:
        scenario_bytes = self.scenario.path.read_bytes()
        map_path = self.scenario.path.parent / self.scenario.data["map"]["path"]
        map_bytes = map_path.read_bytes()
        try:
            git_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.scenario.path.parent,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            git_commit = None
        stations_by_id = {item["id"]: item for item in self.scenario.data["stations"]}
        return {
            "$schema": "simulation-run.v0.2.schema.json",
            "schema_version": "simulation-run.v0.2",
            "scenario_id": self.scenario.data["id"],
            "committed_horizon": self._committed_horizon,
            "stable_scc_observation_threshold": self.scenario.data["deadlock_analysis"][
                "stable_scc_observation_threshold"
            ],
            "simulation_config": self.scenario.data["execution"],
            "scenario_content_hash": hashlib.sha256(scenario_bytes).hexdigest(),
            "map_content_hash": hashlib.sha256(map_bytes).hexdigest(),
            "source_git_commit": git_commit,
            "termination_reason": termination_reason,
            "final_tick": final_tick,
            "checkpoint_names": list(CHECKPOINTS),
            "map_rows": list(self.scenario.warehouse_map.rows),
            "stations": [
                {
                    "station_id": station_id,
                    "kind": stations_by_id[station_id]["kind"],
                    "cell": _cell(cell),
                }
                for station_id, cell in sorted(self.scenario.stations.items())
            ],
            "frames": self.frames,
        }


def replay_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _schema() -> dict[str, Any]:
    path = Path(__file__).parents[2] / "schemas" / "simulation-run.v0.2.schema.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(
        resources.files("mapf_splice")
        .joinpath("schemas/simulation-run.v0.2.schema.json")
        .read_text()
    )


def validate_replay(data: dict[str, Any]) -> None:
    jsonschema.Draft202012Validator(_schema()).validate(data)


def load_replay(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_replay(data)
    return data
