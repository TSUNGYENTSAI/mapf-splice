from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from mapf_splice.scenario import Cell, ScenarioBundle, load_scenario

BACKGROUND = "#f4f6f8"
HUMAN_ZONE = "#d9dde2"
FLOOR = "#edf6fb"
SHELF = "#4c5561"
GRID = "#bdc9d3"
HANDOFF = "#ef9bb5"
DELIVERY = "#2f638c"
TEXT = "#26313a"
MUTED_TEXT = "#66737d"


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    filename = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(filename, size=size)
    except OSError:
        return ImageFont.load_default()


def _centered_text(
    draw: ImageDraw.ImageDraw,
    center: tuple[float, float],
    value: str,
    *,
    font: ImageFont.ImageFont,
    fill: str,
) -> None:
    bounds = draw.textbbox((0, 0), value, font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    draw.text(
        (center[0] - width / 2, center[1] - height / 2 - bounds[1]),
        value,
        font=font,
        fill=fill,
    )


def render_scenario(
    scenario: ScenarioBundle,
    *,
    output: Path,
    cell_size: int = 36,
) -> Path:
    warehouse_map = scenario.warehouse_map
    margin = max(24, cell_size)
    human_band = max(22, round(cell_size * 0.8))
    legend_height = max(76, round(cell_size * 2.2))
    grid_left = margin
    grid_top = margin + human_band
    grid_width = warehouse_map.width * cell_size
    grid_height = warehouse_map.height * cell_size
    image_width = grid_width + 2 * margin
    image_height = grid_height + 2 * margin + 2 * human_band + legend_height

    image = Image.new("RGBA", (image_width, image_height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    label_font = _font(max(12, round(cell_size * 0.34)), bold=True)
    small_font = _font(max(12, round(cell_size * 0.33)))
    legend_font = _font(max(13, round(cell_size * 0.36)))

    top_human = (margin, margin, margin + grid_width, grid_top - 5)
    bottom_human_top = grid_top + grid_height + 5
    bottom_human = (
        margin,
        bottom_human_top,
        margin + grid_width,
        bottom_human_top + human_band - 5,
    )
    draw.rounded_rectangle(top_human, radius=6, fill=HUMAN_ZONE)
    draw.rounded_rectangle(bottom_human, radius=6, fill=HUMAN_ZONE)
    _centered_text(
        draw,
        ((top_human[0] + top_human[2]) / 2, (top_human[1] + top_human[3]) / 2),
        "Human loading zone",
        font=small_font,
        fill=MUTED_TEXT,
    )
    _centered_text(
        draw,
        (
            (bottom_human[0] + bottom_human[2]) / 2,
            (bottom_human[1] + bottom_human[3]) / 2,
        ),
        "Human loading zone",
        font=small_font,
        fill=MUTED_TEXT,
    )

    station_by_cell = {
        cell: station_id for station_id, cell in scenario.stations.items()
    }
    for row_index, row in enumerate(warehouse_map.rows):
        for col_index, symbol in enumerate(row):
            left = grid_left + col_index * cell_size
            top = grid_top + row_index * cell_size
            bounds = (left, top, left + cell_size, top + cell_size)
            fill = {
                "#": SHELF,
                ".": FLOOR,
                "P": HANDOFF,
                "D": DELIVERY,
            }[symbol]
            draw.rectangle(bounds, fill=fill, outline=GRID, width=1)
            cell = Cell(row_index, col_index)
            if cell in station_by_cell:
                station_id = station_by_cell[cell]
                _centered_text(
                    draw,
                    (left + cell_size / 2, top + cell_size / 2),
                    station_id,
                    font=label_font,
                    fill=TEXT if symbol == "P" else BACKGROUND,
                )

    legend_top = bottom_human[3] + max(18, cell_size // 2)
    swatch_size = max(13, round(cell_size * 0.42))
    legend_items = (
        (FLOOR, "Robot floor"),
        (SHELF, "Shelf"),
        (HANDOFF, "Handoff"),
        (DELIVERY, "Delivery"),
    )
    x = margin
    for fill, label in legend_items:
        draw.rectangle(
            (x, legend_top, x + swatch_size, legend_top + swatch_size),
            fill=fill,
            outline=GRID,
        )
        draw.text(
            (x + swatch_size + 7, legend_top - 1),
            label,
            font=legend_font,
            fill=TEXT,
        )
        label_width = draw.textbbox((0, 0), label, font=legend_font)[2]
        x += swatch_size + label_width + 24

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output, format="PNG", optimize=True)
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a MAPF Splice scenario topology to PNG."
    )
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cell-size", type=int, default=36)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    scenario = load_scenario(args.scenario)
    output = render_scenario(
        scenario,
        output=args.output,
        cell_size=args.cell_size,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
