from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

from PIL import Image

from mapf_splice.inspect import create_case_server
from mapf_splice.lifelong import LifelongRunConfig, run_lifelong_validation

ROOT = Path(__file__).parents[2]
CASES = ROOT / "validation/lifelong"
DEFAULT_OUTPUT = ROOT / "artifacts/story-media/v0.1"
DOC_OUTPUT = ROOT / "docs/assets/story-media/v0.1"
BUNDLED_MODULES = (
    Path.home()
    / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
)
STORY_CASES = {
    "A": "four-robot-nonparticipant",
    "B": "four-robot-nonparticipant",
    "C": "three-robot-k3",
    "D": "four-robot-nonparticipant",
    "E": "three-robot-delayed",
    "F": "random-k3-two-recoveries-seed615",
}
SLUGS = {
    "A": "paths-to-actions",
    "B": "local-recovery",
    "C": "detect-contain-confirm",
    "D": "atomic-splice",
    "E": "asynchronous-adg",
    "F": "lifelong-operation",
}
POSTER_CURSOR = {"A": 3, "B": 12, "C": 8, "D": 2, "E": 5, "F": -1}
TARGET_MS = {
    "A": (6000, 8000),
    "B": (20000, 30000),
    "C": (8000, 10000),
    "D": (5000, 7000),
    "E": (6000, 8000),
    "F": (8000, 12000),
}
TERMINAL_HOLD_MS = {
    "A": 1000,
    "B": 1000,
    "C": 1000,
    "D": 2500,
    "E": 1000,
    "F": 1000,
}
TIME_SCALE = {"A": 1.0, "B": 1.0, "C": 1.05, "D": 1.0, "E": 1.3, "F": 0.45}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_png(path: Path) -> None:
    """Canonicalize insignificant browser anti-alias rounding, then save losslessly."""
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        normalized = []
        for pixel in rgb.get_flattened_data():
            value = tuple(min(255, ((channel + 8) // 16) * 16) for channel in pixel)
            if min(value) >= 224 and max(value) - min(value) <= 32:
                gray = min(255, ((sum(value) // 3 + 8) // 16) * 16)
                value = (gray, gray, gray)
            normalized.append(value)
        rgb.putdata(normalized)
        rgb.save(path, optimize=True)


def command_version(command: str) -> str:
    result = subprocess.run(
        [command, "-version"], check=True, capture_output=True, text=True
    )
    return result.stdout.splitlines()[0]


def require_tools() -> dict[str, str]:
    tools = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe")}
    node = shutil.which("node")
    if node is None:
        bundled = (
            Path.home()
            / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
        )
        node = str(bundled) if bundled.exists() else None
    tools["node"] = node
    missing = [name for name, path in tools.items() if path is None]
    if missing:
        raise SystemExit(f"required media dependency missing: {', '.join(missing)}")
    return {name: str(path) for name, path in tools.items()}


def replays() -> dict[str, dict]:
    result = {}
    for case_id in sorted(set(STORY_CASES.values())):
        config = LifelongRunConfig.from_json(CASES / f"{case_id}.json")
        result[case_id] = run_lifelong_validation(config).replay
    return result


def encode_story(story_id: str, story_dir: Path, tools: dict[str, str]) -> None:
    timing = json.loads((story_dir / "timing.json").read_text())
    items = timing["items"]
    concat = story_dir / "frames.concat"
    lines = ["ffconcat version 1.0"]
    for cursor, item in enumerate(items):
        frame = story_dir / "frames" / f"{cursor:04d}.png"
        duration_ms = round(item["presentation"]["durationMs"] * TIME_SCALE[story_id])
        if cursor == len(items) - 1:
            duration_ms += TERMINAL_HOLD_MS[story_id]
        lines.extend(
            [
                f"file '{frame.as_posix()}'",
                f"duration {duration_ms / 1000:.3f}",
            ]
        )
    lines.append(
        f"file '{(story_dir / 'frames' / f'{len(items) - 1:04d}.png').as_posix()}'"
    )
    concat.write_text("\n".join(lines) + "\n")
    mp4, webm = (
        story_dir / f"story-{story_id.lower()}.mp4",
        story_dir / f"story-{story_id.lower()}.webm",
    )
    common = [tools["ffmpeg"], "-y", "-f", "concat", "-safe", "0", "-i", str(concat)]
    output_duration_ms = (
        round(timing["emitted_duration_ms"] * TIME_SCALE[story_id])
        + TERMINAL_HOLD_MS[story_id]
    )
    duration_args = ["-t", f"{output_duration_ms / 1000:.3f}"]
    subprocess.run(
        common
        + duration_args
        + [
            "-vf",
            "fps=12,format=yuv420p",
            "-an",
            "-map_metadata",
            "-1",
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "20",
            str(mp4),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        common
        + duration_args
        + [
            "-vf",
            "fps=12",
            "-an",
            "-map_metadata",
            "-1",
            "-c:v",
            "libvpx-vp9",
            "-crf",
            "28",
            "-b:v",
            "0",
            str(webm),
        ],
        check=True,
        capture_output=True,
    )
    palette, gif = (
        story_dir / "palette.png",
        story_dir / f"story-{story_id.lower()}.gif",
    )
    subprocess.run(
        [
            tools["ffmpeg"],
            "-y",
            "-i",
            str(mp4),
            "-vf",
            "fps=12,scale=1200:-1:flags=lanczos,palettegen=max_colors=192",
            str(palette),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            tools["ffmpeg"],
            "-y",
            "-i",
            str(mp4),
            "-i",
            str(palette),
            "-lavfi",
            "fps=12,scale=1200:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=sierra2_4a",
            "-loop",
            "0",
            str(gif),
        ],
        check=True,
        capture_output=True,
    )
    palette.unlink()
    concat.unlink()
    cursor = POSTER_CURSOR[story_id] if POSTER_CURSOR[story_id] >= 0 else len(items) - 1
    with Image.open(story_dir / "frames" / f"{cursor:04d}.png") as image:
        image.save(story_dir / "poster.png", optimize=True)


def anchor(item: dict, final: bool = False) -> dict:
    source = item["sources"][-1 if final else 0]
    return {
        "kind": item["kind"],
        "tick": source["tick"],
        "checkpoint": source["checkpoint"],
        "stage_id": item["presentation"]["stageId"],
    }


def build_manifest(
    output: Path,
    replay_data: dict[str, dict],
    tools: dict[str, str],
    stories: list[str],
) -> dict:
    source_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    manifest = {
        "schema_version": "story-media-freeze.v1",
        "source_commit": source_commit,
        "viewport": {"width": 1440, "height": 810, "device_scale_factor": 1},
        "tool_versions": {
            "node": subprocess.run(
                [tools["node"], "--version"], check=True, capture_output=True, text=True
            ).stdout.strip(),
            "playwright": json.loads(
                (
                    Path(
                        os.environ.get(
                            "NODE_PATH",
                            BUNDLED_MODULES,
                        )
                    )
                    / "playwright/package.json"
                ).read_text()
            )["version"],
            "chromium": "Google Chrome (Playwright channel)",
            "ffmpeg": command_version(tools["ffmpeg"]),
        },
        "stories": {},
    }
    for story_id in stories:
        story_dir = output / f"story-{story_id.lower()}"
        timing = json.loads((story_dir / "timing.json").read_text())
        items = timing["items"]
        case_id = STORY_CASES[story_id]
        replay_bytes = (
            json.dumps(replay_data[case_id], sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode()
        outputs = {
            "poster": "poster.png",
            "gif": f"story-{story_id.lower()}.gif",
            "webm": f"story-{story_id.lower()}.webm",
            "mp4": f"story-{story_id.lower()}.mp4",
        }
        counts = {
            kind: sum(item["kind"] == kind for item in items)
            for kind in ("runtime", "explain", "montage-gap")
        }
        cursor = (
            POSTER_CURSOR[story_id] if POSTER_CURSOR[story_id] >= 0 else len(items) - 1
        )
        manifest["stories"][story_id] = {
            "story_id": story_id,
            "case_id": case_id,
            "source_commit": source_commit,
            "replay_sha256": hashlib.sha256(replay_bytes).hexdigest(),
            "first_item": anchor(items[0]),
            "terminal_item": anchor(items[-1], True),
            "tick_bounds": {
                "first": items[0]["sources"][0]["tick"],
                "last": items[-1]["sources"][-1]["tick"],
            },
            "emitted_item_count": len(items),
            "runtime_item_count": counts["runtime"],
            "explain_item_count": counts["explain"],
            "montage_gap_item_count": counts["montage-gap"],
            "montage_gap_count": counts["montage-gap"],
            "emitted_duration_ms": timing["emitted_duration_ms"],
            "encoding_time_scale": TIME_SCALE[story_id],
            "capture_duration_ms": (
                round(timing["emitted_duration_ms"] * TIME_SCALE[story_id])
                + TERMINAL_HOLD_MS[story_id]
            ),
            "terminal_loop_hold_ms": TERMINAL_HOLD_MS[story_id],
            "target_duration_ms": list(TARGET_MS[story_id]),
            "poster_frame_cursor": cursor,
            "outputs": outputs,
            "output_sha256": {
                name: sha256(story_dir / filename) for name, filename in outputs.items()
            },
            "intentional_checkpoint_suppression": (
                "production emitted sequence; no capture-side selection"
            ),
            "capture_choreography": "export-view compact overview"
            if story_id == "A"
            else (
                "five labeled montage gaps"
                if story_id == "F"
                else "exact emitted-item seeking"
            ),
        }
    return manifest


def publish_docs(output: Path, manifest: dict) -> None:
    DOC_OUTPUT.mkdir(parents=True, exist_ok=True)
    for story_id, record in manifest["stories"].items():
        source = output / f"story-{story_id.lower()}"
        shutil.copy2(
            source / record["outputs"]["gif"],
            DOC_OUTPUT / f"story-{story_id.lower()}-{SLUGS[story_id]}.gif",
        )
        shutil.copy2(
            source / "poster.png",
            DOC_OUTPUT / f"story-{story_id.lower()}-{SLUGS[story_id]}.png",
        )
    (DOC_OUTPUT / "media-freeze.json").write_text(json.dumps(manifest, indent=2) + "\n")


def verify(output: Path) -> None:
    manifest_path = output / "media-freeze.json"
    if not manifest_path.exists() and output == DEFAULT_OUTPUT:
        manifest_path = DOC_OUTPUT / "media-freeze.json"
    manifest = json.loads(manifest_path.read_text())
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", manifest["source_commit"], head],
        cwd=ROOT,
        check=False,
    )
    if ancestor.returncode:
        raise SystemExit("media freeze source commit is not an ancestor of HEAD")
    for story_id, record in manifest["stories"].items():
        story_dir = output / f"story-{story_id.lower()}"
        for name, filename in record["outputs"].items():
            path = story_dir / filename
            if not path.exists() or sha256(path) != record["output_sha256"][name]:
                raise SystemExit(f"asset hash mismatch: Story {story_id} {name}")
        if record["emitted_item_count"] != sum(
            record[f"{kind}_item_count"]
            for kind in ("runtime", "explain", "montage_gap")
        ):
            raise SystemExit(f"item count mismatch: Story {story_id}")
        if story_id == "F" and record["montage_gap_count"] != 5:
            raise SystemExit("Story F must contain five montage gaps")
    readme = (ROOT / "README.md").read_text()
    for link in __import__("re").findall(r"!\[[^]]*\]\(([^)]+)\)", readme):
        if not (ROOT / link).exists():
            raise SystemExit(f"broken README image link: {link}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture deterministic MAPF Splice story media"
    )
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--story", choices=list(STORY_CASES))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.verify_only:
        verify(output)
        return
    stories = (
        list(STORY_CASES)
        if args.all
        else [args.story]
        if args.story
        else parser.error("use --all or --story")
    )
    tools = require_tools()
    replay_data = replays()
    output.mkdir(parents=True, exist_ok=True)
    server = create_case_server(replay_data)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        env = os.environ.copy()
        module_root = BUNDLED_MODULES
        env["NODE_PATH"] = str(module_root)
        subprocess.run(
            [
                tools["node"],
                str(Path(__file__).with_name("capture_browser.mjs")),
                f"http://127.0.0.1:{server.server_address[1]}",
                str(output),
                ",".join(stories),
            ],
            cwd=ROOT,
            env=env,
            check=True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    for story_id in stories:
        frames = output / f"story-{story_id.lower()}" / "frames"
        for frame in sorted(frames.glob("*.png")):
            normalize_png(frame)
        encode_story(story_id, output / f"story-{story_id.lower()}", tools)
    manifest = build_manifest(output, replay_data, tools, stories)
    (output / "media-freeze.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if stories == list(STORY_CASES) and output == DEFAULT_OUTPUT:
        publish_docs(output, manifest)


if __name__ == "__main__":
    main()
