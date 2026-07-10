from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mapf_splice.domain import Cell, EdgeResource, Resource, VertexResource
from mapf_splice.routing import RoutePath, find_path


class ScenarioError(ValueError):
    """Raised when scenario data violates a cross-file invariant."""


@dataclass(frozen=True, slots=True)
class WarehouseMap:
    rows: tuple[str, ...]

    @property
    def height(self) -> int:
        return len(self.rows)

    @property
    def width(self) -> int:
        return len(self.rows[0])

    def contains(self, cell: Cell) -> bool:
        return 0 <= cell.row < self.height and 0 <= cell.col < self.width

    def symbol_at(self, cell: Cell) -> str:
        if not self.contains(cell):
            raise ScenarioError(f"cell outside map: {cell}")
        return self.rows[cell.row][cell.col]

    def is_traversable(self, cell: Cell) -> bool:
        return self.contains(cell) and self.symbol_at(cell) != "#"


@dataclass(frozen=True, slots=True)
class ScenarioBundle:
    path: Path
    data: dict[str, Any]
    warehouse_map: WarehouseMap
    stations: dict[str, Cell]


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ScenarioError(f"expected JSON object: {path}")
    return value


def load_map(path: Path) -> WarehouseMap:
    rows = tuple(line.rstrip("\n") for line in path.read_text().splitlines())
    if not rows:
        raise ScenarioError(f"map is empty: {path}")
    width = len(rows[0])
    if width == 0 or any(len(row) != width for row in rows):
        raise ScenarioError(f"map must be a non-empty rectangle: {path}")
    invalid = sorted({symbol for row in rows for symbol in row} - {"#", ".", "P", "D"})
    if invalid:
        raise ScenarioError(f"unsupported map symbols {invalid}: {path}")
    return WarehouseMap(rows=rows)


def load_scenario(path: Path) -> ScenarioBundle:
    path = path.resolve()
    data = load_json(path)
    if data.get("schema_version") != "scenario.v0.1":
        raise ScenarioError("unsupported scenario schema_version")

    map_path = (path.parent / data["map"]["path"]).resolve()
    warehouse_map = load_map(map_path)

    stations: dict[str, Cell] = {}
    station_kinds: dict[str, str] = {}
    station_cells: set[Cell] = set()
    for station in data["stations"]:
        station_id = station["id"]
        if station_id in stations:
            raise ScenarioError(f"duplicate station id: {station_id}")
        cell = Cell.from_dict(station["cell"])
        kind = station["kind"]
        if kind not in {"handoff", "delivery"}:
            raise ScenarioError(f"station {station_id} has invalid kind {kind!r}")
        expected_symbol = "P" if kind == "handoff" else "D"
        if warehouse_map.symbol_at(cell) != expected_symbol:
            raise ScenarioError(
                f"station {station_id} expects {expected_symbol} at {cell}"
            )
        if cell in station_cells:
            raise ScenarioError(f"multiple stations share cell {cell}")
        station_cells.add(cell)
        stations[station_id] = cell
        station_kinds[station_id] = kind

    marked_station_cells = {
        Cell(row, col)
        for row, symbols in enumerate(warehouse_map.rows)
        for col, symbol in enumerate(symbols)
        if symbol in {"P", "D"}
    }
    if marked_station_cells != station_cells:
        raise ScenarioError("every P and D map cell must have exactly one station")

    handoff_ids = {key for key, value in station_kinds.items() if value == "handoff"}
    delivery_ids = {key for key, value in station_kinds.items() if value == "delivery"}

    robot_ids: set[str] = set()
    robot_start_ids: set[str] = set()
    for robot in data["fleet"]["robots"]:
        robot_id = robot["id"]
        if robot_id in robot_ids:
            raise ScenarioError(f"duplicate robot id: {robot_id}")
        robot_ids.add(robot_id)
        if robot["start_station_id"] not in handoff_ids:
            raise ScenarioError(f"robot {robot_id} must start at a handoff station")
        if robot["start_station_id"] in robot_start_ids:
            raise ScenarioError("robots cannot share an initial station")
        robot_start_ids.add(robot["start_station_id"])

    task_ids: set[str] = set()
    bootstrap_pickup_ids: set[str] = set()
    for task in data["task_stream"]["initial_tasks"]:
        task_id = task["id"]
        if task_id in task_ids:
            raise ScenarioError(f"duplicate task id: {task_id}")
        task_ids.add(task_id)
        if task["pickup_station_id"] not in handoff_ids:
            raise ScenarioError(f"task {task_id} has invalid pickup station")
        if task["pickup_station_id"] in bootstrap_pickup_ids:
            raise ScenarioError("bootstrap tasks cannot share a pickup station")
        bootstrap_pickup_ids.add(task["pickup_station_id"])
        if task["delivery_station_id"] not in delivery_ids:
            raise ScenarioError(f"task {task_id} has invalid delivery station")

    generator = data["task_stream"]["generator"]
    if not set(generator["pickup_station_ids"]) <= handoff_ids:
        raise ScenarioError("task generator contains invalid pickup station")
    if not set(generator["delivery_station_ids"]) <= delivery_ids:
        raise ScenarioError("task generator contains invalid delivery station")

    horizon = data["traffic"]["committed_horizon"]
    if not horizon["minimum"] <= horizon["default"] <= horizon["maximum"]:
        raise ScenarioError("committed horizon default must be within its range")
    for label, value in (
        ("extra_wait_ticks", data["execution"]["delay_schedule"]["extra_wait_ticks"]),
        ("release_interval_ticks", generator["release_interval_ticks"]),
    ):
        if value["minimum"] > value["maximum"]:
            raise ScenarioError(f"{label} minimum cannot exceed maximum")

    return ScenarioBundle(
        path=path,
        data=data,
        warehouse_map=warehouse_map,
        stations=stations,
    )


def _route_cells(route: dict[str, Any]) -> tuple[Cell, ...]:
    return tuple(Cell.from_dict(cell) for cell in route["cells"])


def _action_resources(route: tuple[Cell, ...], start: int, end: int) -> set[Resource]:
    resources: set[Resource] = set()
    for index in range(start, min(end, len(route) - 1)):
        source = route[index]
        target = route[index + 1]
        resources.add(VertexResource(target))
        resources.add(EdgeResource(source, target))
    return resources


def load_review(path: Path, scenario: ScenarioBundle) -> dict[str, Any]:
    data = load_json(path.resolve())
    if data.get("schema_version") != "render-review.v0.1":
        raise ScenarioError("unsupported render review schema_version")
    if data.get("scenario_id") != scenario.data["id"]:
        raise ScenarioError("render review scenario_id does not match scenario")

    robot_ids = {robot["id"] for robot in scenario.data["fleet"]["robots"]}
    routes: dict[str, tuple[Cell, ...]] = {}
    for route in data["routes"]:
        robot_id = route["robot_id"]
        if robot_id in routes:
            raise ScenarioError(f"duplicate review route for {robot_id}")
        routes[robot_id] = _route_cells(route)
    if set(routes) != robot_ids:
        raise ScenarioError("render review must define one route for every robot")

    robot_start_station = {
        robot["id"]: robot["start_station_id"]
        for robot in scenario.data["fleet"]["robots"]
    }
    initial_task_by_pickup = {
        task["pickup_station_id"]: task
        for task in scenario.data["task_stream"]["initial_tasks"]
    }

    for robot_id, cells in routes.items():
        if not cells:
            raise ScenarioError(f"route is empty for {robot_id}")
        for cell in cells:
            if not scenario.warehouse_map.is_traversable(cell):
                raise ScenarioError(f"route for {robot_id} enters blocked cell {cell}")
        for left, right in zip(cells, cells[1:], strict=False):
            if abs(left.row - right.row) + abs(left.col - right.col) != 1:
                raise ScenarioError(f"route for {robot_id} has non-adjacent cells")
        start_station_id = robot_start_station[robot_id]
        if cells[0] != scenario.stations[start_station_id]:
            raise ScenarioError(f"route for {robot_id} does not start at its robot")
        task = initial_task_by_pickup.get(start_station_id)
        if task is None:
            raise ScenarioError(f"no bootstrap task starts at {start_station_id}")
        expected_goal = scenario.stations[task["delivery_station_id"]]
        if cells[-1] != expected_goal:
            raise ScenarioError(
                f"route for {robot_id} does not reach its bootstrap goal"
            )
        runtime_route = find_path(
            cells[0],
            cells[-1],
            is_traversable=scenario.warehouse_map.is_traversable,
        )
        if not isinstance(runtime_route, RoutePath) or runtime_route.cells != cells:
            raise ScenarioError(
                f"review route for {robot_id} does not match deterministic A*"
            )

    horizon = scenario.data["traffic"]["committed_horizon"]
    for view in data["views"]:
        k = view["committed_horizon"]
        if not horizon["minimum"] <= k <= horizon["maximum"]:
            raise ScenarioError(f"view {view['id']} uses K outside scenario range")
        indices = view["route_indices"]
        if set(indices) != robot_ids:
            raise ScenarioError(f"view {view['id']} must position every robot")

        committed: dict[str, set[Resource]] = {}
        preview: dict[str, set[Resource]] = {}
        occupied: dict[str, VertexResource] = {}
        positions: set[Cell] = set()
        for robot_id, route in routes.items():
            index = indices[robot_id]
            if not 0 <= index < len(route):
                raise ScenarioError(f"invalid route index for {robot_id}")
            if route[index] in positions:
                raise ScenarioError(f"robots overlap in view {view['id']}")
            positions.add(route[index])
            occupied[robot_id] = VertexResource(route[index])
            committed[robot_id] = _action_resources(route, index, index + k)
            preview[robot_id] = _action_resources(
                route,
                index + k,
                index + 2 * k,
            )

        robot_list = sorted(robot_ids)
        for offset, robot_id in enumerate(robot_list):
            for other_id in robot_list[offset + 1 :]:
                if committed[robot_id] & committed[other_id]:
                    raise ScenarioError(
                        f"committed claims overlap in view {view['id']}"
                    )
                if occupied[robot_id] in committed[other_id] or occupied[
                    other_id
                ] in committed[robot_id]:
                    raise ScenarioError(
                        f"committed claim enters occupied cell in view {view['id']}"
                    )

        derived_dependencies = {
            (waiting_id, blocking_id)
            for waiting_id in robot_ids
            for blocking_id in robot_ids
            if waiting_id != blocking_id
            and preview[waiting_id]
            & (committed[blocking_id] | {occupied[blocking_id]})
        }
        declared_dependencies = {
            (edge["waiting_robot_id"], edge["blocking_robot_id"])
            for edge in view["prospective_dependencies"]
        }
        if len(declared_dependencies) != len(view["prospective_dependencies"]):
            raise ScenarioError(f"view {view['id']} has duplicate dependencies")
        if declared_dependencies != derived_dependencies:
            raise ScenarioError(
                f"view {view['id']} dependencies do not match route claims"
            )

    data["_routes"] = routes
    return data
