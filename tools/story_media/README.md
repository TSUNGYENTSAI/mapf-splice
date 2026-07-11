# Deterministic story media

The capture command starts the production Inspector, activates its real Story
Display, and seeks the existing emitted-item controller through the read-only
capture bridge. It never parses story manifests or implements another timeline.

Requirements: Node.js, Playwright with Chrome/Chromium, FFmpeg, and FFprobe.
On Codex desktop, the script discovers the bundled Node runtime automatically.

```bash
uv run python tools/story_media/capture_story_media.py --all
uv run python tools/story_media/capture_story_media.py --story B
uv run python tools/story_media/capture_story_media.py --output /tmp/story-media --all
uv run python tools/story_media/capture_story_media.py --verify-only
```

Lossless frames, timing metadata, WebM, and MP4 remain in ignored `artifacts/`.
The optimized GIFs, posters, and freeze manifest are copied into
`docs/assets/story-media/v0.1/` only after a complete canonical A–F run.
Story-specific fixed encoding scales preserve every emitted item and its
controller-recorded relative timing while fitting the approved README duration;
the exact scale and terminal loop hold are recorded in the freeze manifest.
Each Story starts from a freshly loaded production Inspector document so SVG
rasterization state cannot leak across Story activations.
Captured lossless PNGs receive a fixed 4-bit RGB canonicalization before
encoding. This removes browser anti-alias rounding drift without changing
geometry, emitted-item order, or technical state.
