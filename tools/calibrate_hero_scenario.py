from __future__ import annotations

import argparse
import json
from copy import deepcopy
from itertools import product
from pathlib import Path
from typing import Any

from mapf_splice.scenario import ScenarioBundle, load_scenario
from mapf_splice.simulation import DeterministicSimulator
from mapf_splice.trace import EventKind, TraceEvent

EVENTS = {
    "first_cyclic_scc": EventKind.PROSPECTIVE_SCC_OBSERVED,
    "first_stable_scc": EventKind.STABLE_SCC_DETECTED,
    "first_containment": EventKind.CONTAINMENT_STARTED,
    "quiescence": EventKind.QUIESCENCE_REACHED,
}


def parse_values(value: str) -> tuple[int, ...]:
    """Parse N, N,M, or inclusive N:M integer specifications."""
    values: set[int] = set()
    for part in value.split(","):
        if ":" in part:
            start_text, end_text = part.split(":", 1)
            start, end = int(start_text), int(end_text)
            if end < start:
                raise ValueError(f"descending range is not supported: {part}")
            values.update(range(start, end + 1))
        else:
            values.add(int(part))
    if not values or min(values) < 0:
        raise ValueError("tick and horizon values must be non-negative")
    return tuple(sorted(values))


def scenario_with_releases(
    scenario: ScenarioBundle,
    releases: dict[str, int],
) -> ScenarioBundle:
    data = deepcopy(scenario.data)
    tasks = {task["id"]: task for task in data["task_stream"]["initial_tasks"]}
    if set(releases) != set(tasks):
        raise ValueError("release override must name every bootstrap task")
    for task_id, release_tick in releases.items():
        tasks[task_id]["release_tick"] = release_tick
    return ScenarioBundle(
        path=scenario.path,
        data=data,
        warehouse_map=scenario.warehouse_map,
        stations=scenario.stations,
    )


def event_snapshot(event: TraceEvent | None) -> dict[str, Any] | None:
    if event is None:
        return None
    members = str(dict(event.details).get("members", ""))
    identity = []
    for member in filter(None, members.split(",")):
        robot_id, version = member.rsplit("@", 1)
        identity.append({"robot_id": robot_id, "plan_version": int(version)})
    return {"tick": event.tick, "members": identity}


def run_candidate(
    scenario: ScenarioBundle,
    *,
    horizon: int,
    max_ticks: int,
) -> dict[str, Any]:
    simulator = DeterministicSimulator.from_scenario(
        scenario,
        committed_horizon=horizon,
    )
    for _ in range(max_ticks):
        simulator.tick()
        if any(
            event.kind is EventKind.QUIESCENCE_REACHED
            for event in simulator.trace.events
        ):
            break
    events = simulator.trace.events
    result: dict[str, Any] = {"committed_horizon": horizon}
    for label, kind in EVENTS.items():
        result[label] = event_snapshot(
            next((e for e in events if e.kind is kind), None)
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Sweep deterministic bootstrap release timing for the hero scenario."
        )
    )
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--t1", default="0")
    parser.add_argument("--t2", default="0")
    parser.add_argument("--t3", default="0")
    parser.add_argument("--horizons", default="3,4,5")
    parser.add_argument("--max-ticks", type=int, default=80)
    parser.add_argument("--max-candidates", type=int, default=10_000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        release_sets = {
            "T1": parse_values(args.t1),
            "T2": parse_values(args.t2),
            "T3": parse_values(args.t3),
        }
        horizons = parse_values(args.horizons)
    except ValueError as error:
        parser.error(str(error))
    if min(horizons) < 1:
        parser.error("committed horizons must be positive")
    if args.max_ticks < 1 or args.max_candidates < 1:
        parser.error("--max-ticks and --max-candidates must be positive")
    candidates = list(product(*(release_sets[key] for key in ("T1", "T2", "T3"))))
    if len(candidates) > args.max_candidates:
        parser.error(
            f"sweep expands to {len(candidates)} candidates; "
            f"increase --max-candidates explicitly"
        )
    base = load_scenario(args.scenario)
    output = []
    for values in candidates:
        releases = dict(zip(("T1", "T2", "T3"), values, strict=True))
        scenario = scenario_with_releases(base, releases)
        output.append(
            {
                "release_ticks": releases,
                "runs": [
                    run_candidate(
                        scenario,
                        horizon=horizon,
                        max_ticks=args.max_ticks,
                    )
                    for horizon in horizons
                ],
            }
        )
    if args.json:
        print(json.dumps(output, indent=2, sort_keys=True))
        return
    print("T1\tT2\tT3\tK\tcyclic\tstable\tcontainment\tquiescence")
    for candidate in output:
        releases = candidate["release_ticks"]
        for run in candidate["runs"]:
            cells = []
            for key in EVENTS:
                value = run[key]
                if value is None:
                    cells.append("-")
                else:
                    members = ",".join(
                        f"{item['robot_id']}@{item['plan_version']}"
                        for item in value["members"]
                    )
                    cells.append(f"{members}@tick-{value['tick']}")
            print(
                releases["T1"],
                releases["T2"],
                releases["T3"],
                run["committed_horizon"],
                *cells,
                sep="\t",
            )


if __name__ == "__main__":
    main()
