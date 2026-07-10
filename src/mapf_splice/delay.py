from __future__ import annotations

import hashlib
from dataclasses import dataclass

from mapf_splice.domain import ActionRef


@dataclass(frozen=True, slots=True)
class DeterministicDelaySchedule:
    seed: int
    probability: float
    minimum_extra_ticks: int
    maximum_extra_ticks: int

    def __post_init__(self) -> None:
        if self.seed < 0:
            raise ValueError("delay seed cannot be negative")
        if not 0 <= self.probability <= 1:
            raise ValueError("delay probability must be between zero and one")
        if not 0 <= self.minimum_extra_ticks <= self.maximum_extra_ticks:
            raise ValueError("invalid extra delay tick range")

    def extra_ticks(self, action_ref: ActionRef) -> int:
        value = (
            f"{self.seed}|{action_ref.robot_id}|{action_ref.plan_version}|"
            f"{action_ref.action_index}"
        ).encode()
        digest = hashlib.sha256(value).digest()
        sample = int.from_bytes(digest[:8], "big") / 2**64
        if sample >= self.probability:
            return 0
        width = self.maximum_extra_ticks - self.minimum_extra_ticks + 1
        offset = int.from_bytes(digest[8:16], "big") % width
        return self.minimum_extra_ticks + offset
