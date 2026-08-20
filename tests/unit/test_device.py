import pytest

from orchestrator import DeviceRequirement, DeviceState


def make_state(model="iPhone12", ios="15.4", battery=80):
    return DeviceState(
        model=model,
        ios_version=DeviceState.parse_ios_version(ios),
        battery_level=battery,
    )


def test_requirement_matches_any_model_by_default():
    assert DeviceRequirement().matches(make_state(model="anything"))


def test_requirement_rejects_wrong_model():
    req = DeviceRequirement(models={"iPhone12"})
    assert not req.matches(make_state(model="iPhone8"))
    assert req.matches(make_state(model="iPhone12"))


def test_requirement_min_ios():
    req = DeviceRequirement(min_ios=(15, 0))
    assert not req.matches(make_state(ios="14.8"))
    assert req.matches(make_state(ios="15.0"))
    assert req.matches(make_state(ios="15.4.1"))


def test_requirement_max_ios():
    req = DeviceRequirement(max_ios=(14, 8))
    assert req.matches(make_state(ios="14.8"))
    assert not req.matches(make_state(ios="14.8.1"))
    assert not req.matches(make_state(ios="15.0"))


def test_requirement_min_battery():
    req = DeviceRequirement(min_battery=50)
    assert not req.matches(make_state(battery=49))
    assert req.matches(make_state(battery=50))


def test_version_comparison_pads_shorter_tuple():
    # (14,) should behave like (14, 0, 0) against a longer version tuple.
    req = DeviceRequirement(min_ios=(14,))
    assert req.matches(make_state(ios="14.0"))
    assert req.matches(make_state(ios="14.1"))
    assert not req.matches(make_state(ios="13.9"))


def test_device_state_rejects_invalid_battery():
    with pytest.raises(ValueError):
        make_state(battery=150)


def test_device_state_rejects_empty_model():
    with pytest.raises(ValueError):
        make_state(model="")
