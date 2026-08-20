import pytest

from orchestrator import Attack, Stage


def test_stage_rejects_probability_of_zero():
    with pytest.raises(ValueError):
        Stage(id="s", name="s", expected_success_probability=0.0)


def test_stage_rejects_probability_above_one():
    with pytest.raises(ValueError):
        Stage(id="s", name="s", expected_success_probability=1.5)


def test_stage_accepts_probability_of_exactly_one():
    stage = Stage(id="s", name="s", expected_success_probability=1.0)
    assert stage.expected_success_probability == 1.0


def test_attack_rejects_empty_stage_list():
    with pytest.raises(ValueError):
        Attack(name="empty", stages=[])
