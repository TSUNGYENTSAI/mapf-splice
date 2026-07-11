from __future__ import annotations

import random
from dataclasses import dataclass, field

from mapf_splice.domain import Task, TaskStatus
from mapf_splice.routing import NoPath, find_path
from mapf_splice.scenario import ScenarioBundle
from mapf_splice.world import WorldState


class WorkloadError(ValueError):
    """Raised when a seeded workload cannot satisfy the scenario contract."""


@dataclass(frozen=True, slots=True)
class GeneratedTask:
    id: str
    release_tick: int
    pickup_station_id: str
    delivery_station_id: str


@dataclass(slots=True)
class SeededTaskStream:
    scenario: ScenarioBundle
    seed: int
    release_until_tick: int
    randomize_initial_tasks: bool = False
    _generator: dict = field(init=False, repr=False)
    _rng: random.Random = field(init=False, repr=False)
    _next_release_tick: int = field(init=False, repr=False)
    _next_index: int = field(init=False, repr=False)
    _last_pair: tuple[str, str] | None = field(init=False, repr=False)
    _released: list[GeneratedTask] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.release_until_tick < 0:
            raise WorkloadError("release_until_tick must be nonnegative")
        generator = self.scenario.data["task_stream"]["generator"]
        minimum = generator["release_interval_ticks"]["minimum"]
        maximum = generator["release_interval_ticks"]["maximum"]
        if minimum < 1 or minimum > maximum:
            raise WorkloadError("generator intervals must be positive and ordered")
        if not generator["pickup_station_ids"]:
            raise WorkloadError("generator requires a pickup station")
        if not generator["delivery_station_ids"]:
            raise WorkloadError("generator requires a delivery station")
        if generator["max_pending_tasks"] < 1:
            raise WorkloadError("max_pending_tasks must be positive")
        self._generator = generator
        self._rng = random.Random(self.seed)
        initial = self.scenario.data["task_stream"]["initial_tasks"]
        self._next_release_tick = max(task["release_tick"] for task in initial)
        self._next_release_tick += self._rng.randint(minimum, maximum)
        self._next_index = 1
        self._last_pair: tuple[str, str] | None = None
        self._released: list[GeneratedTask] = []

    def prepare_initial_tasks(self, world: WorldState) -> tuple[GeneratedTask, ...]:
        if not self.randomize_initial_tasks:
            return ()
        if world.tick != 0 or any(
            task.status is not TaskStatus.PENDING for task in world.tasks.values()
        ):
            raise WorkloadError(
                "random initial tasks require an untouched tick-0 world"
            )
        count = len(world.robots)
        pickups = list(self._generator["pickup_station_ids"])
        if len(pickups) < count:
            raise WorkloadError("random initial tasks need one pickup per robot")
        self._rng.shuffle(pickups)
        world.tasks.clear()
        generated = []
        for index, pickup in enumerate(pickups[:count], start=1):
            delivery_choices = [
                delivery
                for delivery in self._generator["delivery_station_ids"]
                if self.scenario.stations[delivery] != self.scenario.stations[pickup]
                and not isinstance(
                    find_path(
                        self.scenario.stations[pickup],
                        self.scenario.stations[delivery],
                        is_traversable=self.scenario.warehouse_map.is_traversable,
                    ),
                    NoPath,
                )
            ]
            if not delivery_choices:
                raise WorkloadError(f"pickup {pickup} has no reachable delivery")
            delivery = delivery_choices[self._rng.randrange(len(delivery_choices))]
            task = GeneratedTask(f"T{index}", 0, pickup, delivery)
            world.tasks[task.id] = Task(
                task.id,
                self.scenario.stations[pickup],
                self.scenario.stations[delivery],
                0,
            )
            generated.append(task)
            self._last_pair = (pickup, delivery)
        minimum = self._generator["release_interval_ticks"]["minimum"]
        maximum = self._generator["release_interval_ticks"]["maximum"]
        self._next_release_tick = self._rng.randint(minimum, maximum)
        world.validate()
        return tuple(generated)

    @property
    def released(self) -> tuple[GeneratedTask, ...]:
        return tuple(self._released)

    @property
    def next_release_tick(self) -> int:
        return self._next_release_tick

    def _pair(self) -> tuple[str, str]:
        pairs = [
            (pickup, delivery)
            for pickup in self._generator["pickup_station_ids"]
            for delivery in self._generator["delivery_station_ids"]
            if self.scenario.stations[pickup] != self.scenario.stations[delivery]
            and not isinstance(
                find_path(
                    self.scenario.stations[pickup],
                    self.scenario.stations[delivery],
                    is_traversable=self.scenario.warehouse_map.is_traversable,
                ),
                NoPath,
            )
        ]
        if self._generator["prevent_immediate_pair_repeat"] and len(pairs) > 1:
            pairs = [pair for pair in pairs if pair != self._last_pair]
        if not pairs:
            raise WorkloadError("generator has no valid station pair")
        return pairs[self._rng.randrange(len(pairs))]

    def release_due(self, world: WorldState) -> tuple[GeneratedTask, ...]:
        if world.tick > self.release_until_tick:
            return ()
        pending = sum(
            task.status is TaskStatus.PENDING and task.release_tick <= world.tick
            for task in world.tasks.values()
        )
        if pending >= self._generator["max_pending_tasks"]:
            return ()
        released: list[GeneratedTask] = []
        while self._next_release_tick <= world.tick:
            if self._next_release_tick > self.release_until_tick:
                break
            pickup, delivery = self._pair()
            while True:
                task_id = f"G{self._next_index:04d}"
                self._next_index += 1
                if task_id not in world.tasks:
                    break
            task = GeneratedTask(task_id, world.tick, pickup, delivery)
            world.tasks[task.id] = Task(
                task.id,
                self.scenario.stations[pickup],
                self.scenario.stations[delivery],
                world.tick,
            )
            self._released.append(task)
            released.append(task)
            self._last_pair = (pickup, delivery)
            pending += 1
            interval = self._rng.randint(
                self._generator["release_interval_ticks"]["minimum"],
                self._generator["release_interval_ticks"]["maximum"],
            )
            self._next_release_tick = world.tick + interval
            if pending >= self._generator["max_pending_tasks"]:
                break
        world.validate()
        return tuple(released)
