from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import urlparse

from mapf_splice.lifelong import LifelongRunConfig, run_lifelong_validation
from mapf_splice.replay import load_replay

ASSET_TYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript"}


def _case_entry(case_id: str, replay: dict) -> dict:
    event_sequences = set()
    recoveries_completed = 0
    for frame in replay["frames"]:
        for event in frame["events"]:
            if event["sequence"] in event_sequences:
                continue
            event_sequences.add(event["sequence"])
            recoveries_completed += event["kind"] == "recovery-completed"
    final_tasks = replay["frames"][-1]["tasks"]
    completed_by_robot = {
        robot_id: sum(
            task["status"] == "completed" and task["assigned_robot_id"] == robot_id
            for task in final_tasks
        )
        for robot_id in sorted(
            {
                task["assigned_robot_id"]
                for task in final_tasks
                if task["assigned_robot_id"] is not None
            }
        )
    }
    return {
        "id": case_id,
        "label": case_id.replace("-", " ").title(),
        "scenario_id": replay["scenario_id"],
        "committed_horizon": replay["committed_horizon"],
        "final_tick": replay["final_tick"],
        "termination_reason": replay["termination_reason"],
        "recoveries_completed": recoveries_completed,
        "tasks_completed_by_robot": completed_by_robot,
    }


def make_handler(replays: dict[str, dict]):
    web_root = resources.files("mapf_splice").joinpath("web_inspector")
    case_ids = tuple(replays)
    catalog = [_case_entry(case_id, replays[case_id]) for case_id in case_ids]

    class InspectorHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/cases.json":
                payload = json.dumps(catalog, sort_keys=True).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if path == "/run.json":
                replay = replays[case_ids[0]]
            elif path.startswith("/runs/") and path.endswith(".json"):
                case_id = path.removeprefix("/runs/").removesuffix(".json")
                replay = replays.get(case_id)
                if replay is None:
                    self.send_error(404)
                    return
            else:
                replay = None
            if replay is not None:
                payload = json.dumps(replay, sort_keys=True).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            name = "index.html" if path == "/" else path.removeprefix("/")
            if name not in {
                "index.html",
                "styles.css",
                "capture.css",
                "story-visuals.css",
                "app.js",
                "playback.js",
            }:
                self.send_error(404)
                return
            payload = web_root.joinpath(name).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ASSET_TYPES[Path(name).suffix])
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args) -> None:
            return

    return InspectorHandler


def create_server(
    replay_path: Path,
    host: str = "127.0.0.1",
    port: int = 0,
):
    replay = load_replay(replay_path)
    return create_case_server({replay_path.stem: replay}, host, port)


def create_case_server(
    replays: dict[str, dict], host: str = "127.0.0.1", port: int = 0
):
    if not replays:
        raise ValueError("at least one replay is required")
    return ThreadingHTTPServer((host, port), make_handler(replays))


def load_lifelong_cases(config_dir: Path) -> dict[str, dict]:
    paths = sorted(config_dir.glob("*.json"))
    if not paths:
        raise ValueError(f"no lifelong case configs found in {config_dir}")
    review_order = {
        "three-robot-k3": 0,
        "four-robot-nonparticipant": 1,
        "three-robot-delayed": 2,
        "random-k3-flow-seed590": 3,
        "random-k3-one-recovery-seed202": 4,
        "random-k3-two-recoveries-seed615": 5,
        "random-k3-three-recoveries-seed213": 6,
        "random-k3-four-recoveries-seed1043": 7,
    }
    selected = []
    for path in paths:
        config = LifelongRunConfig.from_json(path)
        if (
            path.stem
            in {
                "three-robot-k3",
                "four-robot-nonparticipant",
                "three-robot-delayed",
            }
            or config.randomize_initial_tasks
        ):
            selected.append((path.stem, config))
    if not selected:
        raise ValueError(f"no Web review cases found in {config_dir}")
    selected.sort(key=lambda item: review_order.get(item[0], 100))
    return {
        case_id: run_lifelong_validation(config).replay for case_id, config in selected
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve an offline MAPF replay inspector."
    )
    parser.add_argument("replay", nargs="?", type=Path)
    parser.add_argument(
        "--lifelong-cases",
        type=Path,
        help="generate and review every lifelong config in this directory",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    if (args.replay is None) == (args.lifelong_cases is None):
        parser.error("provide either REPLAY or --lifelong-cases")
    try:
        if args.lifelong_cases is not None:
            server = create_case_server(
                load_lifelong_cases(args.lifelong_cases), args.host, args.port
            )
        else:
            server = create_server(args.replay, args.host, args.port)
    except Exception as error:
        parser.error(f"invalid replay: {error}")
    url = f"http://{args.host}:{server.server_address[1]}"
    print(f"MAPF Splice Inspector: {url}")
    if not args.no_open:
        threading.Timer(0.2, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
