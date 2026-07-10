from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import urlparse

from mapf_splice.replay import load_replay

ASSET_TYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript"}


def make_handler(replay: dict):
    web_root = resources.files("mapf_splice").joinpath("web_inspector")

    class InspectorHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/run.json":
                payload = json.dumps(replay, sort_keys=True).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            name = "index.html" if path == "/" else path.removeprefix("/")
            if name not in {"index.html", "styles.css", "app.js"}:
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


def create_server(replay_path: Path, host: str = "127.0.0.1", port: int = 0):
    replay = load_replay(replay_path)
    return ThreadingHTTPServer((host, port), make_handler(replay))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve an offline MAPF replay inspector."
    )
    parser.add_argument("replay", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    try:
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
