import pytest

from orchestrator import protocol


def test_format_command_no_args():
    assert protocol.format_command("STATE") == "STATE\n"


def test_format_command_with_arg():
    assert protocol.format_command("RUN_STAGE", "bypass_lock") == "RUN_STAGE bypass_lock\n"


def test_state_round_trip():
    info = protocol.parse_state_response("STATE model=iPhone12 ios=15.4 battery=80\n")
    assert info.model == "iPhone12"
    assert info.ios_version == (15, 4)
    assert info.battery_level == 80


def test_state_malformed_raises():
    with pytest.raises(ValueError):
        protocol.parse_state_response("NOT_STATE foo\n")
    with pytest.raises(ValueError):
        protocol.parse_state_response("STATE model=iPhone12\n")  # missing ios/battery


def test_run_stage_ok():
    result = protocol.parse_run_stage_response("OK\n")
    assert result.success
    assert result.reason == ""


def test_run_stage_fail_with_reason():
    result = protocol.parse_run_stage_response("FAIL stage_did_not_land\n")
    assert not result.success
    assert result.reason == "stage_did_not_land"


def test_run_stage_malformed_raises():
    with pytest.raises(ValueError):
        protocol.parse_run_stage_response("MAYBE\n")


def test_list_header_and_entries():
    assert protocol.parse_list_header("OK 2\n") == 2
    assert protocol.parse_list_entry("F contacts.db\n") == ("contacts.db", False)
    assert protocol.parse_list_entry("D photos\n") == ("photos", True)


def test_list_entry_malformed_raises():
    with pytest.raises(ValueError):
        protocol.parse_list_entry("X weird\n")


def test_read_header():
    assert protocol.parse_read_header("OK 1234\n") == 1234


def test_err_helpers():
    assert protocol.is_err("ERR not_found\n")
    assert not protocol.is_err("OK\n")
    assert protocol.parse_err("ERR not_found\n") == "not_found"
