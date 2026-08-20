from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Optional, Sequence

from .attacks import Attack, AttackCategory
from .device import DeviceState


class AttackSelector(ABC):
    """
    Strategy for picking one attack out of an already-filtered, non-empty
    list of device-compatible candidates. Pulled out as a strategy (rather
    than baked into the orchestrator) so alternative policies -- a fixed
    priority list per device family, a cost/time-based score, category
    weighting, etc. -- can be swapped in without touching AttackOrchestrator.
    """

    @abstractmethod
    def select(self, candidates: Sequence[Attack], state: DeviceState) -> Attack:
        ...


class HighestExpectedSuccessSelector(AttackSelector):
    """
    Default strategy: score each candidate by the product of its stages'
    expected_success_probability (a rough estimate of the whole chain
    landing). Ties broken by fewer stages (faster, less exposure on a real
    device), then by declared priority (higher wins). Category-blind --
    `Attack.category` plays no role here beyond whatever priority a caller
    happens to assign it. See CategoryAwareSelector for a strategy where
    category is a first-class ranking dimension instead.
    """

    def select(self, candidates: Sequence[Attack], state: DeviceState) -> Attack:
        if not candidates:
            raise ValueError("select() requires at least one candidate")
        return max(
            candidates,
            key=lambda a: (a.expected_success_probability, -len(a.stages), a.priority),
        )


# Zero-day ranked highest: prefer it even at somewhat lower odds than a
# cheaper n-day, since it's less likely to already be detected or patched.
# Opt in explicitly via CategoryAwareSelector(category_rank=ZERO_DAY_FIRST_RANK)
# -- COST_CONSCIOUS_RANK below is the selector's bare-constructor default.
ZERO_DAY_FIRST_RANK: Dict[AttackCategory, int] = {
    AttackCategory.ZERO_DAY: 2,
    AttackCategory.N_DAY: 1,
    AttackCategory.OTHER: 0,
}

# Opposite ordering: zero-day ranked lowest, so cheaper n-day/other attacks
# are tried first and the expensive zero-day is only attempted once those
# have each failed. This is CategoryAwareSelector's default ranking.
COST_CONSCIOUS_RANK: Dict[AttackCategory, int] = {
    AttackCategory.OTHER: 2,
    AttackCategory.N_DAY: 1,
    AttackCategory.ZERO_DAY: 0,
}


class CategoryAwareSelector(AttackSelector):
    """
    Ranks candidates by attack category first (default: COST_CONSCIOUS_RANK
    -- other/n-day tried before the more expensive zero-day), then falls
    back to HighestExpectedSuccessSelector's scoring (probability, fewer
    stages, priority) to break ties within the same category.

    Category here *dominates* probability, not just breaks a tie after it --
    e.g. under COST_CONSCIOUS_RANK, a 90%-odds n-day is tried before a
    99%-odds zero-day regardless of the odds gap. That's a real policy
    choice (avoid burning the expensive attack unless the cheap ones failed)
    that's why this is a separate, opt-in strategy from
    HighestExpectedSuccessSelector rather than folded into it.

    Pass a custom `category_rank` (e.g. ZERO_DAY_FIRST_RANK) to change the
    ordering or add categories.
    """

    def __init__(self, category_rank: Optional[Dict[AttackCategory, int]] = None):
        self._category_rank = category_rank or COST_CONSCIOUS_RANK

    def select(self, candidates: Sequence[Attack], state: DeviceState) -> Attack:
        if not candidates:
            raise ValueError("select() requires at least one candidate")
        return max(
            candidates,
            key=lambda a: (
                self._category_rank.get(a.category, 0),
                a.expected_success_probability,
                -len(a.stages),
                a.priority,
            ),
        )
