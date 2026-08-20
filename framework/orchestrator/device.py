from __future__ import annotations

from dataclasses import dataclass
from itertools import zip_longest
from typing import FrozenSet, Optional, Tuple


def _version_cmp(a: Tuple[int, ...], b: Tuple[int, ...]) -> int:
    """Compare version tuples of possibly different lengths, e.g. (14,) vs (14, 4, 1)."""
    for x, y in zip_longest(a, b, fillvalue=0):
        if x != y:
            return -1 if x < y else 1
    return 0


@dataclass(frozen=True)
class DeviceState:
    model: str
    ios_version: Tuple[int, ...]
    battery_level: int  # 0-100

    def __post_init__(self) -> None:
        if not 0 <= self.battery_level <= 100:
            raise ValueError(f"battery_level must be in [0, 100], got {self.battery_level}")
        if not self.model:
            raise ValueError("model must not be empty")

    @staticmethod
    def parse_ios_version(text: str) -> Tuple[int, ...]:
        return tuple(int(p) for p in text.split("."))

    @property
    def ios_version_str(self) -> str:
        return ".".join(str(p) for p in self.ios_version)


@dataclass(frozen=True)
class DeviceRequirement:
    """
    Declarative precondition an Attack checks against a device before it's
    considered a candidate. Kept declarative (rather than an arbitrary
    predicate callable) so requirements are independently testable and easy
    to describe in a README/demo without reading code.
    """

    models: Optional[FrozenSet[str]] = None  # None = any model
    min_ios: Optional[Tuple[int, ...]] = None
    max_ios: Optional[Tuple[int, ...]] = None
    min_battery: int = 0

    def __post_init__(self) -> None:
        if self.models is not None and not isinstance(self.models, frozenset):
            object.__setattr__(self, "models", frozenset(self.models))

    def matches(self, state: DeviceState) -> bool:
        if self.models is not None and state.model not in self.models:
            return False
        if self.min_ios is not None and _version_cmp(state.ios_version, self.min_ios) < 0:
            return False
        if self.max_ios is not None and _version_cmp(state.ios_version, self.max_ios) > 0:
            return False
        if state.battery_level < self.min_battery:
            return False
        return True
