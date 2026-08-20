from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable, Optional, Sequence

from .device import DeviceRequirement, DeviceState
from .stages import Stage

if TYPE_CHECKING:
    from .runner import AttackAttemptResult
    from .transport.base import DeviceClient


class AttackCategory(Enum):
    """
    Broad class an attack belongs to, for selection strategies that want to
    weigh this independently of raw success probability (see
    selection.CategoryAwareSelector) -- e.g. preferring a zero-day over an
    n-day even at comparable or slightly lower odds, since it's less likely
    to already be detected or patched. Defaults to OTHER so existing attacks
    that don't care about this dimension aren't forced to pick a side.
    """

    ZERO_DAY = "zero_day"
    N_DAY = "n_day"
    OTHER = "other"


# Runs once, right after this attack is selected and before its first stage.
# Raising (any exception) marks the attempt failed with that reason and lets
# the orchestrator fall back to the next candidate, same as a failed stage.
InitHook = Callable[["DeviceClient", DeviceState], None]

# Runs once after the attack finishes -- on both the success and failure
# path -- so it can revert partial device state on failure or finalize on
# success. Only invoked if init_attack didn't itself fail (nothing to tear
# down otherwise). A teardown exception is recorded on the result but does
# not flip succeeded/failed -- teardown is best-effort cleanup, not part of
# the attack's own outcome.
TeardownHook = Callable[["DeviceClient", DeviceState, "AttackAttemptResult"], None]


@dataclass(frozen=True)
class Attack:
    name: str
    stages: Sequence[Stage]
    requirement: DeviceRequirement = field(default_factory=DeviceRequirement)
    priority: int = 0  # tie-breaker when selector scores are equal; higher wins
    category: AttackCategory = AttackCategory.OTHER

    init_attack: Optional[InitHook] = None
    teardown: Optional[TeardownHook] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "stages", tuple(self.stages))
        if not self.stages:
            raise ValueError(f"attack {self.name!r} must have at least one stage")

    def is_compatible(self, state: DeviceState) -> bool:
        return self.requirement.matches(state)

    @property
    def expected_success_probability(self) -> float:
        """Product of stage probabilities: the framework's rough estimate this chain lands."""
        probability = 1.0
        for stage in self.stages:
            probability *= stage.expected_success_probability
        return probability
