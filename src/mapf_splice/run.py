from __future__ import annotations

import argparse
from pathlib import Path

from mapf_splice.replay import FrameRecorder, replay_json, validate_replay
from mapf_splice.scenario import load_scenario
from mapf_splice.simulation import DeterministicSimulator
from mapf_splice.trace import EventKind


def export_run(
    scenario_path: Path,
    *,
    committed_horizon: int | None,
    until: str,
    max_ticks: int,
    stop_tick: int | None,
) -> dict:
    scenario = load_scenario(scenario_path)
    recorder = FrameRecorder(scenario)
    simulator = DeterministicSimulator.from_scenario(
        scenario, committed_horizon=committed_horizon
    )
    simulator.recorder = recorder
    if until == "tick" and stop_tick is None:
        raise ValueError("--stop-tick is required with --until tick")
    if stop_tick is not None and stop_tick < 1:
        raise ValueError("--stop-tick must be positive")
    reason = None
    for _ in range(max_ticks):
        simulator.tick()
        if until == "quiescence" and any(
            event.kind is EventKind.QUIESCENCE_REACHED
            for event in simulator.trace.events
        ):
            reason = "quiescence"
            break
        if until == "tick" and simulator.world.tick >= (stop_tick or 0):
            reason = "tick"
            break
    if reason is None:
        raise RuntimeError(
            f"max ticks ({max_ticks}) reached before requested {until} condition"
        )
    artifact = recorder.artifact(
        termination_reason=reason, final_tick=simulator.world.tick
    )
    validate_replay(artifact)
    return artifact


def _first_event(data: dict, kind: str):
    for frame in data["frames"]:
        for event in frame["events"]:
            if event["kind"] == kind:
                return event["tick"]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export a deterministic runtime replay."
    )
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--committed-horizon", type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-ticks", type=int, default=200)
    parser.add_argument("--until", choices=("quiescence", "tick"), default="quiescence")
    parser.add_argument("--stop-tick", type=int)
    args = parser.parse_args()
    try:
        data = export_run(
            args.scenario,
            committed_horizon=args.committed_horizon,
            until=args.until,
            max_ticks=args.max_ticks,
            stop_tick=args.stop_tick,
        )
    except (ValueError, RuntimeError) as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(replay_json(data), encoding="utf-8")
    print(f"frames: {len(data['frames'])}")
    print(f"final tick: {data['final_tick']}")
    for label, kind in (
        ("first cyclic SCC", "prospective-scc-observed"),
        ("first stable SCC", "stable-scc-detected"),
        ("first containment", "containment-started"),
        ("quiescence", "quiescence-reached"),
    ):
        print(f"{label}: {_first_event(data, kind)}")
    print(f"output: {args.output}")


if __name__ == "__main__":
    main()
