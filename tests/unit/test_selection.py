from orchestrator import (
    Attack,
    AttackCategory,
    CategoryAwareSelector,
    COST_CONSCIOUS_RANK,
    DeviceRequirement,
    DeviceState,
    HighestExpectedSuccessSelector,
    Stage,
    ZERO_DAY_FIRST_RANK,
)


def make_state(model="iPhone12", ios="15.4", battery=80):
    return DeviceState(
        model=model,
        ios_version=DeviceState.parse_ios_version(ios),
        battery_level=battery,
    )


def make_attack(name, stage_probs, priority=0, **requirement_kwargs):
    category = requirement_kwargs.pop("category", AttackCategory.OTHER)
    stages = [
        Stage(id=f"{name}_{i}", name=f"{name}_{i}", expected_success_probability=p)
        for i, p in enumerate(stage_probs)
    ]
    return Attack(
        name=name,
        stages=stages,
        requirement=DeviceRequirement(**requirement_kwargs),
        category=category,
        priority=priority,
    )


def test_picks_highest_expected_probability():
    weak = make_attack("weak", [0.5])
    strong = make_attack("strong", [0.9, 0.9])  # 0.81 > 0.5
    chosen = HighestExpectedSuccessSelector().select([weak, strong], make_state())
    assert chosen.name == "strong"


def test_tie_break_prefers_fewer_stages():
    # Both chains multiply out to exactly 0.25 (exact in binary floating point).
    two_stage = make_attack("two", [0.5, 0.5])
    one_stage = make_attack("one", [0.25])
    chosen = HighestExpectedSuccessSelector().select([two_stage, one_stage], make_state())
    assert chosen.name == "one"


def test_tie_break_falls_back_to_priority():
    a = Attack(name="a", stages=[Stage("s", "s", 0.9)], priority=1)
    b = Attack(name="b", stages=[Stage("s", "s", 0.9)], priority=5)
    chosen = HighestExpectedSuccessSelector().select([a, b], make_state())
    assert chosen.name == "b"


def test_category_aware_selector_default_prefers_n_day_over_zero_day():
    # Bare CategoryAwareSelector() defaults to COST_CONSCIOUS_RANK: the
    # expensive zero-day loses to a cheaper n-day even at lower odds.
    zero_day = make_attack("zero_day", [0.99], category=AttackCategory.ZERO_DAY)
    n_day = make_attack("n_day", [0.4], category=AttackCategory.N_DAY)
    chosen = CategoryAwareSelector().select([zero_day, n_day], make_state())
    assert chosen.name == "n_day"


def test_category_aware_selector_falls_back_to_zero_day_when_it_is_the_only_candidate():
    zero_day = make_attack("zero_day", [0.99], category=AttackCategory.ZERO_DAY)
    chosen = CategoryAwareSelector().select([zero_day], make_state())
    assert chosen.name == "zero_day"


def test_category_aware_selector_falls_back_to_probability_within_same_category():
    weak = make_attack("weak", [0.4], category=AttackCategory.N_DAY)
    strong = make_attack("strong", [0.9], category=AttackCategory.N_DAY)
    chosen = CategoryAwareSelector().select([weak, strong], make_state())
    assert chosen.name == "strong"


def test_category_aware_selector_defaults_uncategorized_attacks_above_n_day():
    # Under the default COST_CONSCIOUS_RANK, an attack with no category set
    # explicitly (AttackCategory.OTHER) outranks a declared n-day, since
    # OTHER ranks highest (cheapest/safest) in that ordering.
    uncategorized = make_attack("uncategorized", [0.5])
    n_day = make_attack("n_day", [0.95], category=AttackCategory.N_DAY)
    chosen = CategoryAwareSelector().select([uncategorized, n_day], make_state())
    assert chosen.name == "uncategorized"


def test_category_aware_selector_custom_rank():
    zero_day = make_attack("zero_day", [0.9], category=AttackCategory.ZERO_DAY)
    n_day = make_attack("n_day", [0.9], category=AttackCategory.N_DAY)
    chosen = CategoryAwareSelector(category_rank=ZERO_DAY_FIRST_RANK).select(
        [zero_day, n_day], make_state()
    )
    assert chosen.name == "zero_day"


def test_zero_day_first_rank_prefers_zero_day_over_higher_probability_n_day():
    zero_day = make_attack("zero_day", [0.4], category=AttackCategory.ZERO_DAY)
    n_day = make_attack("n_day", [0.9], category=AttackCategory.N_DAY)
    chosen = CategoryAwareSelector(category_rank=ZERO_DAY_FIRST_RANK).select(
        [zero_day, n_day], make_state()
    )
    assert chosen.name == "zero_day"


def test_cost_conscious_rank_matches_the_default():
    zero_day = make_attack("zero_day", [0.99], category=AttackCategory.ZERO_DAY)
    n_day = make_attack("n_day", [0.5], category=AttackCategory.N_DAY)
    chosen = CategoryAwareSelector(category_rank=COST_CONSCIOUS_RANK).select(
        [zero_day, n_day], make_state()
    )
    assert chosen.name == "n_day"
