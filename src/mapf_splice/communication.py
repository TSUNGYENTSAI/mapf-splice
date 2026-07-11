from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any


def _events(replay: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    by_sequence = {
        event["sequence"]: event
        for frame in replay["frames"]
        for event in frame["events"]
    }
    return tuple(by_sequence[key] for key in sorted(by_sequence))


def _versions(frame: dict[str, Any]) -> dict[str, int]:
    return {robot["robot_id"]: robot["plan_version"] for robot in frame["robots"]}


def _frame(replay: dict[str, Any], tick: int, checkpoint: str) -> dict[str, Any] | None:
    return next(
        (
            frame
            for frame in replay["frames"]
            if frame["tick"] == tick and frame["checkpoint"] == checkpoint
        ),
        None,
    )


def _incidents(replay: dict[str, Any]) -> list[dict[str, Any]]:
    values: dict[int, dict[str, Any]] = {}
    for frame in replay["frames"]:
        recovery = frame["recovery"]
        if recovery is None or recovery["incident"] is None:
            continue
        key = recovery["incident"]["confirmation_tick"]
        values[key] = recovery
    return [values[key] for key in sorted(values)]


def _action_catalog(replay: dict[str, Any]) -> dict[str, dict[str, Any]]:
    actions: dict[str, dict[str, Any]] = {}
    for frame in replay["frames"]:
        for plan in frame["plans"]:
            for action in plan["actions"]:
                actions[action["action_ref"]["label"]] = action
    return actions


def _local_scope_candidates(
    replay: dict[str, Any], events: tuple[dict[str, Any], ...]
) -> list[dict[str, Any]]:
    candidates = []
    all_robot_ids = tuple(robot["robot_id"] for robot in replay["frames"][0]["robots"])
    containment_events = [
        event for event in events if event["kind"] == "containment-started"
    ]
    stable_events = [
        event for event in events if event["kind"] == "stable-scc-detected"
    ]
    for number, recovery in enumerate(_incidents(replay), start=1):
        confirmation_tick = recovery["incident"]["confirmation_tick"]
        install_tick = recovery["installation_tick"]
        completion_tick = recovery["completion_tick"]
        if install_tick is None or completion_tick is None:
            continue
        containment_tick = max(
            (
                event["tick"]
                for event in containment_events
                if event["tick"] <= confirmation_tick
            ),
            default=confirmation_tick,
        )
        before_containment = _frame(replay, containment_tick, "tick-start")
        before_splice = _frame(replay, install_tick, "after-confirmation")
        after_splice = _frame(replay, install_tick, "after-recovery-install")
        if before_containment is None or before_splice is None or after_splice is None:
            continue
        participants = tuple(recovery["participants"])
        nonparticipants = tuple(
            robot_id for robot_id in all_robot_ids if robot_id not in participants
        )
        active_before = tuple(
            robot["robot_id"]
            for robot in before_containment["robots"]
            if robot["active_task_id"] is not None
        )
        before_versions = _versions(before_splice)
        after_versions = _versions(after_splice)
        nonparticipant_completions = [
            event
            for event in events
            if event["kind"] == "action-completed"
            and event["robot_id"] in nonparticipants
            and containment_tick <= event["tick"] <= completion_tick
        ]
        post_recovery_progress = [
            event
            for event in events
            if event["tick"] > completion_tick
            and event["kind"]
            in {"task-assigned", "task-status-changed", "action-completed"}
        ]
        unrelated_stable = [
            event
            for event in stable_events
            if containment_tick < event["tick"] <= completion_tick
        ]
        unchanged = all(
            before_versions.get(robot_id) == after_versions.get(robot_id)
            for robot_id in nonparticipants
        )
        checks = {
            "four_robots_active_before_containment": len(all_robot_ids) == 4
            and len(active_before) == 4,
            "exactly_three_participants": len(participants) == 3,
            "exactly_one_nonparticipant": len(nonparticipants) == 1,
            "proposal_matches_affected_scope": set(participants)
            == {item["robot_id"] for item in recovery["incident"]["scope"]},
            "exactly_three_installed_plans": len(recovery["installed_plan_versions"])
            == 3,
            "nonparticipant_version_unchanged": unchanged,
            "nonparticipant_moves_during_incident": bool(nonparticipant_completions),
            "recovery_completed": completion_tick is not None,
            "post_recovery_task_progress": bool(post_recovery_progress),
            "no_unrelated_stable_cycle": not unrelated_stable,
        }
        strict_pass = all(checks.values())
        candidates.append(
            {
                "incident": number,
                "start_tick": containment_tick,
                "installation_tick": install_tick,
                "end_tick": completion_tick,
                "cycle_core": [
                    item["robot_id"] for item in recovery["incident"]["trigger_core"]
                ],
                "affected_scope": list(participants),
                "active_before_containment": list(active_before),
                "active_nonparticipants": list(nonparticipants),
                "versions_before": before_versions,
                "versions_after": after_versions,
                "nonparticipant_move_completions": len(nonparticipant_completions),
                "post_recovery_progress_events": len(post_recovery_progress),
                "unrelated_stable_cycles": len(unrelated_stable),
                "checks": checks,
                "strict_pass": strict_pass,
            }
        )
    return candidates


def _path_action_candidates(replay: dict[str, Any]) -> list[dict[str, Any]]:
    actions = _action_catalog(replay)
    candidates = []
    for number, recovery in enumerate(_incidents(replay), start=1):
        versions = {
            item["robot_id"]: item["plan_version"]
            for item in recovery["installed_plan_versions"]
        }
        incident_actions = [
            action
            for action in actions.values()
            if versions.get(action["action_ref"]["robot_id"])
            == action["action_ref"]["plan_version"]
        ]
        same_robot = sum(
            dependency["robot_id"] == action["action_ref"]["robot_id"]
            for action in incident_actions
            for dependency in action["dependencies"]
        )
        cross_robot = sum(
            dependency["robot_id"] != action["action_ref"]["robot_id"]
            for action in incident_actions
            for dependency in action["dependencies"]
        )
        checks = {
            "synchronized_paths_present": bool(recovery["paths"]),
            "compiled_actions_present": bool(incident_actions),
            "same_robot_dependencies_present": same_robot > 0,
            "cross_robot_dependencies_present": cross_robot > 0,
        }
        candidates.append(
            {
                "incident": number,
                "confirmation_tick": recovery["incident"]["confirmation_tick"],
                "synchronized_paths": len(recovery["paths"]),
                "compiled_actions": len(incident_actions),
                "move_actions": sum(
                    action["kind"] == "move" for action in incident_actions
                ),
                "wait_actions": sum(
                    action["kind"] == "wait" for action in incident_actions
                ),
                "same_robot_dependencies": same_robot,
                "cross_robot_dependencies": cross_robot,
                "checks": checks,
                "strict_pass": all(checks.values()),
            }
        )
    return candidates


def _detect_confirm_candidates(
    replay: dict[str, Any], events: tuple[dict[str, Any], ...]
) -> list[dict[str, Any]]:
    candidates = []
    previous_end = -1
    for number, recovery in enumerate(_incidents(replay), start=1):
        confirmation_tick = recovery["incident"]["confirmation_tick"]
        confirmation_frame = _frame(replay, confirmation_tick, "after-confirmation")
        confirmed_graph = (
            None
            if confirmation_frame is None
            else confirmation_frame["confirmed_wait_for"]
        )

        def ticks(kind: str) -> list[int]:
            return [
                event["tick"]
                for event in events
                if event["kind"] == kind
                and previous_end < event["tick"] <= confirmation_tick
            ]

        prospective = ticks("prospective-dependency")
        stable = ticks("stable-scc-detected")
        containment = ticks("containment-started")
        quiescence = ticks("quiescence-reached")
        confirmed = ticks("confirmed-wait-for-built")
        hard_deadlock = ticks("hard-deadlock-confirmed")
        checks = {
            "prospective_dependency_present": bool(prospective),
            "stable_scc_present": bool(stable),
            "containment_present": bool(containment),
            "quiescence_present": bool(quiescence),
            "confirmed_wait_for_present": bool(confirmed),
            "hard_deadlock_present": bool(hard_deadlock),
            "cycle_core_and_scope_explicit": bool(
                recovery["incident"]["trigger_core"] and recovery["incident"]["scope"]
            ),
            "confirmed_graph_explicit": confirmed_graph is not None,
        }
        event_ticks = prospective + stable + containment + quiescence + confirmed
        candidates.append(
            {
                "incident": number,
                "start_tick": min(event_ticks) if event_ticks else None,
                "end_tick": confirmation_tick,
                "prospective_dependency_ticks": sorted(set(prospective)),
                "stable_scc_tick": min(stable) if stable else None,
                "containment_tick": min(containment) if containment else None,
                "quiescence_tick": min(quiescence) if quiescence else None,
                "confirmation_tick": confirmation_tick,
                "cycle_core": [
                    item["robot_id"] for item in recovery["incident"]["trigger_core"]
                ],
                "affected_scope": [
                    item["robot_id"] for item in recovery["incident"]["scope"]
                ],
                "confirmed_cyclic_sccs": (
                    [] if confirmed_graph is None else confirmed_graph["cyclic_sccs"]
                ),
                "checks": checks,
                "strict_pass": all(checks.values()),
            }
        )
        previous_end = recovery["completion_tick"] or confirmation_tick
    return candidates


def _splice_candidates(
    replay: dict[str, Any], events: tuple[dict[str, Any], ...]
) -> list[dict[str, Any]]:
    candidates = []
    for number, recovery in enumerate(_incidents(replay), start=1):
        install_tick = recovery["installation_tick"]
        if install_tick is None:
            continue
        before = _frame(replay, install_tick, "after-confirmation")
        after = _frame(replay, install_tick, "after-recovery-install")
        if before is None or after is None:
            continue
        participants = tuple(recovery["participants"])
        before_versions = _versions(before)
        after_versions = _versions(after)
        nonparticipants = tuple(
            robot_id for robot_id in before_versions if robot_id not in participants
        )
        install_events = [
            event
            for event in events
            if event["kind"] == "recovery-install-succeeded"
            and event["tick"] == install_tick
        ]
        participant_incremented = all(
            after_versions[robot_id] == before_versions[robot_id] + 1
            for robot_id in participants
        )
        nonparticipant_unchanged = all(
            after_versions[robot_id] == before_versions[robot_id]
            for robot_id in nonparticipants
        )
        checks = {
            "incident_identity_present": recovery["incident"] is not None,
            "expected_versions_cover_participants": {
                item["robot_id"] for item in recovery["expected_plan_versions"]
            }
            == set(participants),
            "installed_versions_cover_participants": {
                item["robot_id"] for item in recovery["installed_plan_versions"]
            }
            == set(participants),
            "one_atomic_install_event": len(install_events) == 1,
            "all_participant_versions_increment_together": participant_incremented,
            "nonparticipant_versions_unchanged": nonparticipant_unchanged,
        }
        candidates.append(
            {
                "incident": number,
                "tick": install_tick,
                "before_checkpoint": "after-confirmation",
                "after_checkpoint": "after-recovery-install",
                "participants": list(participants),
                "nonparticipants": list(nonparticipants),
                "versions_before": before_versions,
                "versions_after": after_versions,
                "checks": checks,
                "strict_pass": all(checks.values()),
            }
        )
    return candidates


def _delayed_handoffs(
    replay: dict[str, Any], events: tuple[dict[str, Any], ...]
) -> list[dict[str, Any]]:
    actions = _action_catalog(replay)
    starts = {
        event["action_ref"]["label"]: event
        for event in events
        if event["kind"] == "action-started" and event["action_ref"] is not None
    }
    completions = {
        event["action_ref"]["label"]: event
        for event in events
        if event["kind"] == "action-completed" and event["action_ref"] is not None
    }
    successors: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for action in actions.values():
        for dependency in action["dependencies"]:
            if dependency["robot_id"] != action["action_ref"]["robot_id"]:
                successors[dependency["label"]].append(action)
    candidates = []
    for predecessor_label, successor_actions in sorted(successors.items()):
        start = starts.get(predecessor_label)
        completion = completions.get(predecessor_label)
        if start is None or completion is None:
            continue
        extra_delay = int(start["details"].get("extra_delay_ticks", 0))
        if extra_delay == 0:
            continue
        for successor in successor_actions:
            successor_label = successor["action_ref"]["label"]
            successor_start = starts.get(successor_label)
            if successor_start is None:
                continue
            external_waits = sum(
                event["kind"] == "recovery-admission-external-wait"
                and start["tick"] <= event["tick"] <= successor_start["tick"]
                for event in events
            )
            unlock_delta = successor_start["tick"] - completion["tick"]
            checks = {
                "nonzero_deterministic_delay": extra_delay > 0,
                "cross_robot_predecessor": True,
                "predecessor_completed": completion is not None,
                "successor_started_within_one_tick": unlock_delta in {0, 1},
                "no_external_wait": external_waits == 0,
            }
            candidates.append(
                {
                    "predecessor": predecessor_label,
                    "successor": successor_label,
                    "delay_ticks": extra_delay,
                    "predecessor_start_tick": start["tick"],
                    "predecessor_completion_tick": completion["tick"],
                    "successor_start_tick": successor_start["tick"],
                    "unlock_delta_ticks": unlock_delta,
                    "external_wait_events": external_waits,
                    "checks": checks,
                    "strict_pass": all(checks.values()),
                }
            )
    return candidates


def _external_wait_candidates(
    events: tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    wait_ticks = sorted(
        {
            event["tick"]
            for event in events
            if event["kind"] == "recovery-admission-external-wait"
        }
    )
    candidates = []
    for tick in wait_ticks:
        resumed = next(
            (
                event
                for event in events
                if event["tick"] > tick
                and event["kind"]
                in {
                    "recovery-prefix-granted",
                    "action-started",
                    "recovery-completed",
                }
            ),
            None,
        )
        candidates.append(
            {
                "wait_tick": tick,
                "resume_tick": None if resumed is None else resumed["tick"],
                "resume_event": None if resumed is None else resumed["kind"],
                "strict_pass": resumed is not None,
            }
        )
    return candidates


def analyze_communication_proofs(
    replay: dict[str, Any], *, case_id: str
) -> dict[str, Any]:
    events = _events(replay)
    recovery_completions = [
        event for event in events if event["kind"] == "recovery-completed"
    ]
    task_progress = [
        event
        for event in events
        if event["kind"] in {"task-assigned", "task-status-changed"}
    ]
    lifelong_pass = False
    if len(recovery_completions) >= 2:
        first, second = recovery_completions[:2]
        lifelong_pass = (
            any(
                first["tick"] < event["tick"] < second["tick"]
                for event in task_progress
            )
            and replay["termination_reason"] == "completed-and-drained"
        )
    payload = json.dumps(replay, sort_keys=True, separators=(",", ":")).encode()
    return {
        "case": case_id,
        "scenario_id": replay["scenario_id"],
        "replay_sha256": hashlib.sha256(payload).hexdigest(),
        "final_tick": replay["final_tick"],
        "termination_reason": replay["termination_reason"],
        "paths_to_actions": _path_action_candidates(replay),
        "detect_contain_confirm": _detect_confirm_candidates(replay, events),
        "local_scope": _local_scope_candidates(replay, events),
        "transactional_splice": _splice_candidates(replay, events),
        "delayed_handoffs": _delayed_handoffs(replay, events),
        "external_waits": _external_wait_candidates(events),
        "lifelong_continuation": {
            "recovery_completion_ticks": [
                event["tick"] for event in recovery_completions
            ],
            "strict_pass": lifelong_pass,
        },
    }
