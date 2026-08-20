from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StageOutcome:
    success: bool
    reason: str = ""


@dataclass(frozen=True)
class Stage:
    """
    One step of an attack. `expected_success_probability` is the framework's
    prior belief about this stage, used two ways:

    - AttackSelector scores candidate attacks by it (see selection.py).
    - FakeDeviceClient can roll against it directly, so a Part-1-only demo
      (no simulator involved) still genuinely exercises "stages that
      probabilistically succeed or fail."

    Once a real device (or the TCP simulator) is in the loop, *it* decides
    the actual outcome of run_stage() — this field stops being ground truth
    and goes back to being just the framework's estimate for selection.
    """

    id: str
    name: str
    expected_success_probability: float

    def __post_init__(self) -> None:
        if not (0 < self.expected_success_probability <= 1):
            raise ValueError(
                f"stage {self.id!r}: expected_success_probability must be in (0, 1], "
                f"got {self.expected_success_probability}"
            )
