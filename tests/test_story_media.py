from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "tools/story_media/capture_story_media.py"


def _module():
    spec = importlib.util.spec_from_file_location("story_media", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_story_media_contract_covers_a_through_f() -> None:
    module = _module()
    assert list(module.STORY_CASES) == list("ABCDEF")
    assert module.TARGET_MS["B"] == (20_000, 30_000)
    assert module.POSTER_CURSOR["F"] == -1


def test_verify_detects_asset_hash_mismatch(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    asset = tmp_path / "story-a" / "story-a.gif"
    asset.parent.mkdir()
    asset.write_bytes(b"wrong")
    manifest = {
        "source_commit": "head",
        "stories": {
            "A": {
                "outputs": {"gif": "story-a.gif"},
                "output_sha256": {"gif": "bad"},
                "emitted_item_count": 0,
                "runtime_item_count": 0,
                "explain_item_count": 0,
                "montage_gap_item_count": 0,
                "montage_gap_count": 0,
            }
        },
    }
    (tmp_path / "media-freeze.json").write_text(json.dumps(manifest))

    class Result:
        stdout = "head\n"
        returncode = 0

    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Result())
    try:
        module.verify(tmp_path)
    except SystemExit as error:
        assert "asset hash mismatch" in str(error)
    else:
        raise AssertionError("verify accepted a mismatched asset")


def test_browser_capture_bridge_is_controller_backed_and_read_only() -> None:
    source = (ROOT / "src/mapf_splice/web_inspector/app.js").read_text()
    assert "window.__MAPF_SPLICE_CAPTURE__=Object.freeze" in source
    assert "seek:cursor=>playbackController?.seek(cursor)" in source
    assert "restart:()=>playbackController?.restart()" in source
    assert (
        "buildStorySequence"
        not in (ROOT / "tools/story_media/capture_browser.mjs").read_text()
    )


def test_png_normalization_removes_small_antialias_rounding(tmp_path: Path) -> None:
    from PIL import Image

    module = _module()
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (1, 1), (191, 197, 194)).save(first)
    Image.new("RGB", (1, 1), (188, 194, 191)).save(second)
    module.normalize_png(first)
    module.normalize_png(second)
    assert first.read_bytes() == second.read_bytes()

    Image.new("RGB", (1, 1), (255, 240, 224)).save(first)
    Image.new("RGB", (1, 1), (255, 240, 240)).save(second)
    module.normalize_png(first)
    module.normalize_png(second)
    assert first.read_bytes() == second.read_bytes()
