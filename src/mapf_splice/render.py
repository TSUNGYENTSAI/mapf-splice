from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from mapf_splice.scenario import Cell, ScenarioBundle, load_review, load_scenario

BACKGROUND = "#f4f6f8"
HUMAN_ZONE = "#d9dde2"
FLOOR = "#edf6fb"
SHELF = "#4c5561"
GRID = "#bdc9d3"
HANDOFF = "#ef9bb5"
DELIVERY = "#2f638c"
TEXT = "#26313a"
MUTED_TEXT = "#66737d"
ROBOT_COLORS = ("#e87822", "#2f8f83", "#8a5db7")


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


def _blend_with_white(color: str, amount: float) -> tuple[int, int, int, int]:
    rgb = tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
    blended = tuple(round(channel + (255 - channel) * amount) for channel in rgb)
    return (*blended, 210)


def _draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    points: Sequence[tuple[float, float]],
    *,
    fill: str,
    width: int,
    dash: float,
    gap: float,
) -> None:
    for start, end in zip(points, points[1:], strict=False):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = (dx * dx + dy * dy) ** 0.5
        if length == 0:
            continue
        cursor = 0.0
        while cursor < length:
            dash_end = min(cursor + dash, length)
            segment_start = (
                start[0] + dx * cursor / length,
                start[1] + dy * cursor / length,
            )
            segment_end = (
                start[0] + dx * dash_end / length,
                start[1] + dy * dash_end / length,
            )
            draw.line((segment_start, segment_end), fill=fill, width=width)
            cursor += dash + gap


def _route_points(
    cells: Iterable[Cell],
    *,
    grid_left: int,
    grid_top: int,
    cell_size: int,
    offset: tuple[float, float],
) -> list[tuple[float, float]]:
    return [
        (
            grid_left + (cell.col + 0.5 + offset[0]) * cell_size,
            grid_top + (cell.row + 0.5 + offset[1]) * cell_size,
        )
        for cell in cells
    ]


def _select_view(review: dict[str, Any], view_id: str | None) -> dict[str, Any]:
    views = review["views"]
    if view_id is None:
        return views[0]
    for view in views:
        if view["id"] == view_id:
            return view
    available = ", ".join(view["id"] for view in views)
    raise ValueError(f"unknown view {view_id!r}; available views: {available}")


def render_scenario(
    scenario: ScenarioBundle,
    *,
    output: Path,
    review: dict[str, Any] | None = None,
    view_id: str | None = None,
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

    selected_view: dict[str, Any] | None = None
    if review is not None:
        selected_view = _select_view(review, view_id)
        routes: dict[str, tuple[Cell, ...]] = review["_routes"]
        robot_ids = sorted(routes)
        offsets = ((-0.14, -0.14), (0.14, 0.14), (0.0, 0.0))
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        k = selected_view["committed_horizon"]

        for robot_offset, robot_id in enumerate(robot_ids):
            route = routes[robot_id]
            route_index = selected_view["route_indices"][robot_id]
            color = ROBOT_COLORS[robot_offset % len(ROBOT_COLORS)]
            offset = offsets[robot_offset % len(offsets)]
            full_points = _route_points(
                route,
                grid_left=grid_left,
                grid_top=grid_top,
                cell_size=cell_size,
                offset=offset,
            )
            overlay_draw.line(
                full_points,
                fill=_blend_with_white(color, 0.5),
                width=max(2, round(cell_size * 0.07)),
                joint="curve",
            )

            committed_cells = route[route_index : route_index + k + 1]
            committed_points = _route_points(
                committed_cells,
                grid_left=grid_left,
                grid_top=grid_top,
                cell_size=cell_size,
                offset=offset,
            )
            overlay_draw.line(
                committed_points,
                fill=color,
                width=max(5, round(cell_size * 0.2)),
                joint="curve",
            )

            preview_start = min(len(route) - 1, route_index + k)
            preview_cells = route[preview_start : route_index + 2 * k + 1]
            preview_points = _route_points(
                preview_cells,
                grid_left=grid_left,
                grid_top=grid_top,
                cell_size=cell_size,
                offset=offset,
            )
            _draw_dashed_line(
                overlay_draw,
                preview_points,
                fill=color,
                width=max(4, round(cell_size * 0.14)),
                dash=cell_size * 0.35,
                gap=cell_size * 0.22,
            )

            position = route[route_index]
            center = _route_points(
                (position,),
                grid_left=grid_left,
                grid_top=grid_top,
                cell_size=cell_size,
                offset=offset,
            )[0]
            radius = cell_size * 0.32
            overlay_draw.ellipse(
                (
                    center[0] - radius,
                    center[1] - radius,
                    center[0] + radius,
                    center[1] + radius,
                ),
                fill=color,
                outline=BACKGROUND,
                width=max(2, round(cell_size * 0.07)),
            )
            _centered_text(
                overlay_draw,
                center,
                robot_id,
                font=label_font,
                fill=BACKGROUND,
            )

        image = Image.alpha_composite(image, overlay)
        draw = ImageDraw.Draw(image)

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

    if selected_view is not None:
        summary_y = legend_top + swatch_size + 16
        draw.text(
            (margin, summary_y),
            (
                f"{selected_view['id']}  |  committed K="
                f"{selected_view['committed_horizon']}  |  preview=K"
            ),
            font=legend_font,
            fill=TEXT,
        )
        draw.text(
            (margin, summary_y + swatch_size),
            "Prospective dependency edges: "
            + ", ".join(
                sorted(
                    f"{edge['waiting_robot_id']}->{edge['blocking_robot_id']}"
                    for edge in selected_view["prospective_dependencies"]
                )
            ),
            font=legend_font,
            fill=MUTED_TEXT,
        )

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output, format="PNG", optimize=True)
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a MAPF Splice scenario or review view to PNG."
    )
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--view")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cell-size", type=int, default=36)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    scenario = load_scenario(args.scenario)
    review = load_review(args.review, scenario) if args.review else None
    output = render_scenario(
        scenario,
        output=args.output,
        review=review,
        view_id=args.view,
        cell_size=args.cell_size,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
