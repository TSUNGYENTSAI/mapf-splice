from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema

from mapf_splice.domain import (
    Cell,
    Robot,
    Task,
)
from mapf_splice.traffic import CommittedReservationLedger
from mapf_splice.world import WorldState


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
    path = path.resolve()
    try:
        with path.open(encoding="utf-8") as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise ScenarioError(f"cannot parse JSON document {path}: {error}") from error
    if not isinstance(value, dict):
        raise ScenarioError(f"expected JSON object: {path}")
    schema_reference = value.get("$schema")
    if not isinstance(schema_reference, str) or not schema_reference:
        raise ScenarioError(f"JSON document does not declare $schema: {path}")
    schema_path = (path.parent / schema_reference).resolve()
    try:
        with schema_path.open(encoding="utf-8") as file:
            schema = json.load(file)
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(value)
    except (OSError, json.JSONDecodeError, jsonschema.SchemaError) as error:
        raise ScenarioError(
            f"cannot load JSON schema {schema_path}: {error}"
        ) from error
    except jsonschema.ValidationError as error:
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise ScenarioError(
            f"schema validation failed at {location}: {error.message}"
        ) from error
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


def build_initial_world(
    scenario: ScenarioBundle,
    *,
    committed_horizon: int | None = None,
) -> WorldState:
    data = scenario.data
    configured_horizon = data["traffic"]["committed_horizon"]
    horizon = configured_horizon["default"]
    if committed_horizon is not None:
        horizon = committed_horizon
    if not configured_horizon["minimum"] <= horizon <= configured_horizon["maximum"]:
        raise ScenarioError("committed horizon is outside the scenario range")

    robots: dict[str, Robot] = {}
    for value in data["fleet"]["robots"]:
        if value["initial_payload"] != "empty":
            raise ScenarioError(
                "runtime bootstrap requires an explicit task for carrying payloads"
            )
        robots[value["id"]] = Robot(
            id=value["id"],
            position=scenario.stations[value["start_station_id"]],
        )

    tasks = {
        value["id"]: Task(
            id=value["id"],
            pickup=scenario.stations[value["pickup_station_id"]],
            dropoff=scenario.stations[value["delivery_station_id"]],
            release_tick=value["release_tick"],
        )
        for value in data["task_stream"]["initial_tasks"]
    }
    return WorldState(
        reservations=CommittedReservationLedger(horizon=horizon),
        robots=robots,
        tasks=tasks,
    )
