import pytest

from orchestrator import (
    Attack,
    AttackCategory,
    AttackOrchestrator,
    CategoryAwareSelector,
    DeviceDisconnectedError,
    DeviceRequirement,
    DeviceState,
    NoCompatibleAttackError,
    Stage,
    StageOutcome,
    ZERO_DAY_FIRST_RANK,
)
from orchestrator.transport.fake import FakeDeviceClient


def make_state(model="iPhone12", ios="15.4", battery=80):
    return DeviceState(
        model=model,
        ios_version=DeviceState.parse_ios_version(ios),
        battery_level=battery,
    )


def make_attack(name, stage_ids, priority=0, **requirement_kwargs):
    stages = [Stage(id=sid, name=sid, expected_success_probability=0.9) for sid in stage_ids]
    return Attack(
        name=name,
        stages=stages,
        requirement=DeviceRequirement(**requirement_kwargs),
        priority=priority,
    )
def test_no_compatible_attack_raises():
    attack = make_attack("needs_high_battery", ["s1"], min_battery=90)
    orchestrator = AttackOrchestrator([attack])
    client = FakeDeviceClient(state=make_state(battery=10))
    with pytest.raises(NoCompatibleAttackError):
        orchestrator.run(client)


def test_incompatible_attacks_are_never_attempted():
    wrong_model = make_attack("wrong_model", ["s1"], models={"iPhoneXOther"})
    right_model = make_attack("right_model", ["s2"], models={"iPhone12"})
    orchestrator = AttackOrchestrator([wrong_model, right_model])
    client = FakeDeviceClient(state=make_state(model="iPhone12"))
    result = orchestrator.run(client)
    assert result.succeeded
    assert [a.attack.name for a in result.attempts] == ["right_model"]


def test_orchestrator_uses_injected_selector():
    """
    AttackSelector is a swappable strategy specifically so a caller can
    plug in a different policy -- this proves the plug actually works, not
    just that CategoryAwareSelector's own .select() logic is correct in
    isolation (see test_selection.py). Every other orchestrator test in
    this file omits `selector=`, so without this one, injecting a custom
    selector is never actually exercised end-to-end. Uses ZERO_DAY_FIRST_RANK
    explicitly since CategoryAwareSelector()'s own default is cost-conscious
    (see test_cost_conscious_default_tries_zero_day_only_as_last_resort).
    """
    zero_day = Attack(
        name="zero_day",
        stages=[Stage("zd", "zd", 0.4)],  # worse odds...
        category=AttackCategory.ZERO_DAY,
    )
    n_day = Attack(
        name="n_day",
        stages=[Stage("nd", "nd", 0.9)],  # ...but higher odds
        category=AttackCategory.N_DAY,
    )
    orchestrator = AttackOrchestrator(
        [zero_day, n_day],
        selector=CategoryAwareSelector(category_rank=ZERO_DAY_FIRST_RANK),
    )
    client = FakeDeviceClient(state=make_state())

    result = orchestrator.run(client)

    assert result.succeeded
    assert result.successful_attack.name == "zero_day"  # category dominates probability here


def test_cost_conscious_default_tries_zero_day_only_as_last_resort():
    """
    CategoryAwareSelector()'s bare-constructor default is now
    COST_CONSCIOUS_RANK: the expensive zero-day should only be attempted
    once every cheaper candidate has already failed -- proving the ordering
    end-to-end through the orchestrator's fallback loop, not just via
    CategoryAwareSelector.select() in isolation.
    """
    zero_day = Attack(
        name="zero_day",
        stages=[Stage("zd", "zd", 0.99)],
        category=AttackCategory.ZERO_DAY,
    )
    n_day = Attack(
        name="n_day",
        stages=[Stage("nd_bad", "nd_bad", 0.9)],  # will fail
        category=AttackCategory.N_DAY,
    )
    orchestrator = AttackOrchestrator([zero_day, n_day], selector=CategoryAwareSelector())
    client = FakeDeviceClient(
        state=make_state(),
        stage_outcomes={"nd_bad": StageOutcome(success=False, reason="rejected")},
    )

    result = orchestrator.run(client)

    assert result.succeeded
    assert [a.attack.name for a in result.attempts] == ["n_day", "zero_day"]
    assert result.successful_attack.name == "zero_day"


def test_successful_single_attack_returns_session():
    attack = make_attack("simple", ["s1", "s2"])
    orchestrator = AttackOrchestrator([attack])
    client = FakeDeviceClient(state=make_state())
    result = orchestrator.run(client)
    assert result.succeeded
    assert result.successful_attack.name == "simple"
    assert len(result.attempts) == 1
    assert result.attempts[0].succeeded


def test_successful_chain_calls_unlock():
    attack = make_attack("simple", ["s1", "s2"])
    orchestrator = AttackOrchestrator([attack])
    client = FakeDeviceClient(state=make_state())
    orchestrator.run(client)
    assert client.unlock_called


def test_failed_chain_never_calls_unlock():
    failing = make_attack("failing", ["bad_stage"])
    orchestrator = AttackOrchestrator([failing])
    client = FakeDeviceClient(
        state=make_state(),
        stage_outcomes={"bad_stage": StageOutcome(success=False, reason="nope")},
    )
    orchestrator.run(client)
    assert not client.unlock_called


def test_failed_stage_falls_back_to_next_attack():
    failing = make_attack("failing", ["bad_stage"], priority=10)  # scored/tried first
    working = make_attack("working", ["good_stage"])
    orchestrator = AttackOrchestrator([failing, working])
    client = FakeDeviceClient(
        state=make_state(),
        stage_outcomes={"bad_stage": StageOutcome(success=False, reason="nope")},
    )
    result = orchestrator.run(client)
    assert result.succeeded
    assert result.successful_attack.name == "working"
    assert len(result.attempts) == 2
    assert result.attempts[0].attack.name == "failing"
    assert not result.attempts[0].succeeded
    assert result.attempts[0].failed_stage_id == "bad_stage"
    assert result.attempts[0].reason == "nope"


def test_multi_stage_chain_aborts_at_the_failing_stage_not_the_first():
    """
    The central "multi-stage" mechanic: a chain with real progress before
    the failure, not just a single-stage attack whose one stage fails
    immediately (which is what every other failure test in this file uses).
    Proves stage 1 actually ran and succeeded, stage 2 then failed and
    aborted the chain, and stage 3 was never attempted at all.
    """
    attack = make_attack("multi", ["s1", "s2", "s3"])
    orchestrator = AttackOrchestrator([attack])
    client = FakeDeviceClient(
        state=make_state(),
        stage_outcomes={
            "s1": StageOutcome(success=True),
            "s2": StageOutcome(success=False, reason="bypass_lock rejected"),
            # s3 deliberately left unscripted: if the chain incorrectly
            # kept going past the s2 failure, FakeDeviceClient's default
            # (always succeed) would silently mask that bug.
        },
    )

    real_run_stage = client.run_stage
    calls = []

    def tracking_run_stage(stage_id):
        calls.append(stage_id)
        return real_run_stage(stage_id)

    client.run_stage = tracking_run_stage

    result = orchestrator.run(client)

    assert calls == ["s1", "s2"]  # ran in order, stopped at the failure, never reached s3
    assert not result.succeeded
    assert result.attempts[0].failed_stage_id == "s2"
    assert result.attempts[0].reason == "bypass_lock rejected"


def test_all_attacks_failing_returns_unsuccessful_result():
    a = make_attack("a", ["s1"])
    b = make_attack("b", ["s2"])
    orchestrator = AttackOrchestrator([a, b])
    client = FakeDeviceClient(
        state=make_state(),
        stage_outcomes={
            "s1": StageOutcome(success=False),
            "s2": StageOutcome(success=False),
        },
    )
    result = orchestrator.run(client)
    assert not result.succeeded
    assert result.session is None
    assert result.successful_attack is None
    assert len(result.attempts) == 2


def test_max_attempts_limits_fallback():
    a = make_attack("a", ["s1"], priority=10)  # tried first
    b = make_attack("b", ["s2"])
    orchestrator = AttackOrchestrator([a, b], max_attempts=1)
    client = FakeDeviceClient(
        state=make_state(),
        stage_outcomes={"s1": StageOutcome(success=False), "s2": StageOutcome(success=True)},
    )
    result = orchestrator.run(client)
    assert not result.succeeded
    assert len(result.attempts) == 1  # never got to try "b", even though it would have worked


def test_disconnect_mid_chain_reconnects_and_continues():
    attack = make_attack("simple", ["s1", "s2"])
    orchestrator = AttackOrchestrator([attack])
    client = FakeDeviceClient(state=make_state())

    real_run_stage = client.run_stage
    calls = {"n": 0}

    def flaky_run_stage(stage_id):
        calls["n"] += 1
        if calls["n"] == 1:
            client.simulate_disconnect()
        return real_run_stage(stage_id)

    client.run_stage = flaky_run_stage

    result = orchestrator.run(client)
    assert result.succeeded
    assert result.attempts[0].reconnected


class _ScriptedClient:
    """
    Minimal hand-written DeviceClient (not FakeDeviceClient) for exercising
    the disconnect -> failed-reconnect -> fallback path: stage "s1" always
    drops the connection, reconnecting never works, and stage "s2" (from a
    different, fallback attack) always succeeds once we get there.
    """

    def __init__(self, state):
        self._state = state

    def get_state(self):
        return self._state

    def run_stage(self, stage_id):
        if stage_id == "s1":
            raise DeviceDisconnectedError("connection dropped mid-stage")
        return StageOutcome(success=True)

    def unlock(self):
        pass

    def read_file(self, path):
        raise NotImplementedError

    def list_dir(self, path):
        raise NotImplementedError

    def reconnect(self):
        raise DeviceDisconnectedError("device gone for good")

    def close(self):
        pass


def test_disconnect_with_failed_reconnect_falls_back():
    failing_first = make_attack("failing_first", ["s1"], priority=10)  # tried first
    working = make_attack("working", ["s2"])
    orchestrator = AttackOrchestrator([failing_first, working])
    client = _ScriptedClient(make_state())

    result = orchestrator.run(client)

    assert result.succeeded
    assert result.successful_attack.name == "working"
    assert len(result.attempts) == 2
    assert not result.attempts[0].succeeded
    assert "reconnect failed" in result.attempts[0].reason


# --- attack lifecycle: init_attack / teardown ---


def test_init_attack_runs_before_stages_and_receives_state():
    calls = []

    def init_hook(device_client, state):
        calls.append(("init", state.model))

    attack = Attack(
        name="with_init",
        stages=[Stage("s1", "s1", 0.9)],
        init_attack=init_hook,
    )
    orchestrator = AttackOrchestrator([attack])
    client = FakeDeviceClient(state=make_state(model="iPhone12"))

    result = orchestrator.run(client)

    assert result.succeeded
    assert calls == [("init", "iPhone12")]


def test_init_attack_failure_marks_attempt_failed_and_falls_back():
    def broken_init(device_client, state):
        raise RuntimeError("setup blew up")

    failing = Attack(
        name="failing",
        stages=[Stage("s1", "s1", 0.9)],
        init_attack=broken_init,
        priority=10,  # tried first
    )
    working = Attack(name="working", stages=[Stage("s2", "s2", 0.9)])
    orchestrator = AttackOrchestrator([failing, working])
    client = FakeDeviceClient(state=make_state())

    result = orchestrator.run(client)

    assert result.succeeded
    assert result.successful_attack.name == "working"
    assert not result.attempts[0].succeeded
    assert "setup blew up" in result.attempts[0].reason
    assert result.attempts[0].failed_stage_id is None  # failed in init, never reached a stage


def test_teardown_runs_after_success_and_receives_result():
    seen = []

    def teardown_hook(device_client, state, result):
        seen.append(result.succeeded)

    attack = Attack(name="simple", stages=[Stage("s1", "s1", 0.9)], teardown=teardown_hook)
    orchestrator = AttackOrchestrator([attack])
    client = FakeDeviceClient(state=make_state())

    result = orchestrator.run(client)

    assert result.succeeded
    assert seen == [True]


def test_teardown_runs_after_failure_too():
    seen = []

    def teardown_hook(device_client, state, result):
        seen.append(result.succeeded)

    attack = Attack(
        name="always_fails",
        stages=[Stage("bad", "bad", 0.9)],
        teardown=teardown_hook,
    )
    orchestrator = AttackOrchestrator([attack])
    client = FakeDeviceClient(
        state=make_state(),
        stage_outcomes={"bad": StageOutcome(success=False)},
    )

    result = orchestrator.run(client)

    assert not result.succeeded
    assert seen == [False]


def test_teardown_does_not_run_when_init_attack_fails():
    seen = []

    def broken_init(device_client, state):
        raise RuntimeError("no setup, so nothing to tear down")

    def teardown_hook(device_client, state, result):
        seen.append(True)

    attack = Attack(
        name="broken_init",
        stages=[Stage("s1", "s1", 0.9)],
        init_attack=broken_init,
        teardown=teardown_hook,
    )
    orchestrator = AttackOrchestrator([attack])
    client = FakeDeviceClient(state=make_state())

    orchestrator.run(client)

    assert seen == []


def test_teardown_exception_recorded_but_does_not_flip_success():
    def broken_teardown(device_client, state, result):
        raise RuntimeError("cleanup blew up")

    attack = Attack(name="simple", stages=[Stage("s1", "s1", 0.9)], teardown=broken_teardown)
    orchestrator = AttackOrchestrator([attack])
    client = FakeDeviceClient(state=make_state())

    result = orchestrator.run(client)

    assert result.succeeded  # teardown failing doesn't invalidate an already-successful unlock
    assert "cleanup blew up" in result.attempts[0].teardown_error


def test_init_attack_bad_arity_propagates_instead_of_being_swallowed():
    # A TypeError from a wrong-arity hook is a bug in the hook itself, not
    # the hook "deciding to fail" -- it should surface, not be recorded as
    # an ordinary init_attack failure.
    def wrong_arity_init(device_client):  # missing the required `state` param
        pass

    attack = Attack(name="broken", stages=[Stage("s1", "s1", 0.9)], init_attack=wrong_arity_init)
    orchestrator = AttackOrchestrator([attack])
    client = FakeDeviceClient(state=make_state())

    with pytest.raises(TypeError):
        orchestrator.run(client)


def test_teardown_bad_arity_propagates_instead_of_being_swallowed():
    def wrong_arity_teardown(device_client, state):  # missing the required `result` param
        pass

    attack = Attack(name="broken", stages=[Stage("s1", "s1", 0.9)], teardown=wrong_arity_teardown)
    orchestrator = AttackOrchestrator([attack])
    client = FakeDeviceClient(state=make_state())

    with pytest.raises(TypeError):
        orchestrator.run(client)
