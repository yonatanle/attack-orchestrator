"""
Tests that talk to the real, compiled C simulator over a real TCP socket --
not FakeDeviceClient. Each simulator instance is fresh per test (see
conftest.py) and started with `--force`/`--drop-after` flags where a test
needs a specific, deterministic outcome rather than the simulator's normal
randomized stage rolls.
"""

import pytest

from orchestrator import (
    Attack,
    AttackOrchestrator,
    DeviceNotUnlockedError,
    DeviceRequirement,
    Stage,
)
from orchestrator.transport.tcp import TCPDeviceClient

pytestmark = pytest.mark.integration


def make_attack(name, stage_ids, priority=0, **requirement_kwargs):
    stages = [Stage(id=sid, name=sid, expected_success_probability=0.9) for sid in stage_ids]
    return Attack(
        name=name,
        stages=stages,
        requirement=DeviceRequirement(**requirement_kwargs),
        priority=priority,
    )
def test_state_reports_fixture_device(simulator):
    client = TCPDeviceClient(simulator.host, simulator.port)
    try:
        state = client.get_state()
    finally:
        client.close()

    assert state.model == "iPhone12"
    assert state.ios_version == (15, 4)
    assert state.battery_level == 80


def test_read_rejected_before_any_successful_stage(simulator):
    """
    The simulator gates LIST/READ behind an explicit UNLOCK -- not just a
    client-side convention (ExtractionSession only ever being handed out
    after a successful run()), but enforced by the device itself. See
    test_list_rejected_before_any_successful_stage below for LIST's own
    direct coverage of the same gate.
    """
    client = TCPDeviceClient(simulator.host, simulator.port)
    try:
        with pytest.raises(DeviceNotUnlockedError):
            client.read_file("/contacts.db")
    finally:
        client.close()


def test_list_rejected_before_any_successful_stage(simulator):
    """LIST is gated by the same not_unlocked check as READ (tcp.py's
    list_dir has its own `if reason == "not_unlocked"` branch, separate
    from read_file's) -- exercised directly rather than only inferred."""
    client = TCPDeviceClient(simulator.host, simulator.port)
    try:
        with pytest.raises(DeviceNotUnlockedError):
            client.list_dir("/")
    finally:
        client.close()


@pytest.mark.simulator_args("--force", "pair=success")
def test_read_still_rejected_after_stage_success_without_unlock(simulator):
    """
    The gate is keyed on an explicit UNLOCK, not on any single successful
    RUN_STAGE -- this is the point of the whole design: a stage succeeding
    doesn't mean a *chain* completed, since the device has no concept of
    which stages belong together. Only AttackOrchestrator, which does know,
    sends UNLOCK, and only once a full chain actually succeeds.
    """
    client = TCPDeviceClient(simulator.host, simulator.port)
    try:
        outcome = client.run_stage("pair")
        assert outcome.success  # the stage really did land...
        with pytest.raises(DeviceNotUnlockedError):
            client.read_file("/contacts.db")  # ...but that alone isn't enough
    finally:
        client.close()


@pytest.mark.simulator_args("--force", "pair=success")
def test_read_missing_file_raises(simulator):
    client = TCPDeviceClient(simulator.host, simulator.port)
    try:
        client.run_stage("pair")
        client.unlock()  # so "not found" -- not "not unlocked" -- is what's under test
        with pytest.raises(FileNotFoundError):
            client.read_file("/does/not/exist")
    finally:
        client.close()


@pytest.mark.simulator_args("--force", "pair=success")
def test_successful_attack_and_extract_all(simulator, tmp_path):
    attack = make_attack("unlock", ["pair"])
    orchestrator = AttackOrchestrator([attack])
    client = TCPDeviceClient(simulator.host, simulator.port)
    try:
        result = orchestrator.run(client)
        assert result.succeeded

        report = result.session.extract_all(tmp_path, root="/")
        assert report.succeeded
        assert (tmp_path / "contacts.db").exists()
        assert (tmp_path / "photos" / "img1.jpg").read_bytes() == b"FAKEJPEGDATA-img1"
        assert (tmp_path / "photos" / "img2.jpg").read_bytes() == b"FAKEJPEGDATA-img2"
    finally:
        client.close()


@pytest.mark.simulator_args(
    "--force", "pair=success",
    "--force", "bypass_lock=success",
    "--force", "elevate=success",
)
def test_multi_stage_attack_runs_all_stages_against_real_simulator(simulator, tmp_path):
    """
    Every other integration test uses a single-stage attack -- this is the
    one that actually exercises a genuine multi-stage chain (three real,
    sequential RUN_STAGE round-trips) against the real simulator, not a
    fake. Proves the chain runs in order and extraction still works once
    all three stages have actually completed.
    """
    attack = make_attack("full_bypass", ["pair", "bypass_lock", "elevate"])
    assert len(attack.stages) == 3  # sanity: this really is a multi-stage attack
    orchestrator = AttackOrchestrator([attack])
    client = TCPDeviceClient(simulator.host, simulator.port)
    try:
        result = orchestrator.run(client)
        assert result.succeeded

        report = result.session.extract_all(tmp_path, root="/")
        assert report.succeeded
        assert (tmp_path / "contacts.db").exists()
    finally:
        client.close()


@pytest.mark.simulator_args("--force", "pair=fail", "--force", "fast_pair=success")
def test_forced_stage_failure_drives_fallback_to_next_attack(simulator):
    failing = make_attack("failing", ["pair"], priority=10)  # scored/tried first
    working = make_attack("working", ["fast_pair"])
    orchestrator = AttackOrchestrator([failing, working])
    client = TCPDeviceClient(simulator.host, simulator.port)
    try:
        result = orchestrator.run(client)
    finally:
        client.close()

    assert result.succeeded
    assert result.successful_attack.name == "working"
    assert len(result.attempts) == 2
    assert not result.attempts[0].succeeded
    assert result.attempts[0].failed_stage_id == "pair"


@pytest.mark.simulator_args("--model", "iPhone8", "--force", "pair=success")
def test_model_mismatch_is_filtered_and_compatible_attack_runs(simulator, tmp_path):
    """
    End-to-end proof that DeviceRequirement.models filters against a real
    device's real reported model, not just against synthetic DeviceState
    objects in unit tests -- the simulator here genuinely reports itself as
    iPhone8, via --model, not the usual hardcoded iPhone12 default.
    """
    iphone12_only = make_attack("iphone12_only", ["pair"], models={"iPhone12"})
    iphone8_compatible = make_attack("iphone8_compatible", ["pair"], models={"iPhone8"})
    orchestrator = AttackOrchestrator([iphone12_only, iphone8_compatible])
    client = TCPDeviceClient(simulator.host, simulator.port)
    try:
        result = orchestrator.run(client)
        assert result.device_state.model == "iPhone8"

        # the iPhone12-only attack was filtered out before selection ever
        # saw it -- not attempted and failed, just never a candidate
        assert [a.attack.name for a in result.attempts] == ["iphone8_compatible"]
        assert result.succeeded
        assert result.successful_attack.name == "iphone8_compatible"

        report = result.session.extract_all(tmp_path, root="/")
        assert report.succeeded
        assert (tmp_path / "contacts.db").exists()
    finally:
        client.close()


@pytest.mark.simulator_args("--drop-after", "2", "--force", "pair=success")
def test_mid_chain_disconnect_recovers_via_reconnect(simulator):
    """
    `--drop-after 2` makes the simulator silently close the connection right
    as it receives the 2nd command on it. The orchestrator's first two
    commands are STATE (1) then RUN_STAGE pair (2) -- so this drops the
    connection exactly while the stage attempt is in flight, forcing a real
    socket-level DeviceDisconnectedError, not a mocked one.
    """
    attack = make_attack("unlock", ["pair"])
    orchestrator = AttackOrchestrator([attack])
    client = TCPDeviceClient(simulator.host, simulator.port)
    try:
        result = orchestrator.run(client)
    finally:
        client.close()

    assert result.succeeded
    assert result.attempts[0].reconnected


# --- raw protocol edge cases -----------------------------------------------
#
# These reach past TCPDeviceClient's normal command methods (which always
# build well-formed requests) into client._link directly, to send exactly
# the malformed input the simulator's C parser has to defend against. No
# legitimate framework usage would ever trigger these -- this is testing
# protocol.c's robustness, not the framework's use of the protocol.


def test_empty_line_returns_unknown_command(simulator):
    client = TCPDeviceClient(simulator.host, simulator.port)
    try:
        client._link.send_line("\n")
        response = client._link.recv_line()
        assert response.strip() == "ERR unknown_command"
    finally:
        client.close()


def test_run_stage_without_argument_returns_error(simulator):
    client = TCPDeviceClient(simulator.host, simulator.port)
    try:
        client._link.send_line("RUN_STAGE\n")
        response = client._link.recv_line()
        assert response.strip() == "ERR missing_stage_id"
    finally:
        client.close()


@pytest.mark.simulator_args("--force", "pair=success")
def test_list_without_argument_returns_error(simulator):
    client = TCPDeviceClient(simulator.host, simulator.port)
    try:
        client.run_stage("pair")
        client.unlock()  # otherwise "not_unlocked" masks "missing_path"
        client._link.send_line("LIST\n")
        response = client._link.recv_line()
        assert response.strip() == "ERR missing_path"
    finally:
        client.close()


def test_oversized_line_does_not_crash_the_server(simulator):
    """
    protocol.c's line buffer is fixed-size (2048 bytes) and silently
    truncates input beyond that rather than erroring or overflowing. A
    RUN_STAGE with a huge argument gets truncated into a stage id that
    matches nothing -- the point isn't the exact truncated content, it's
    that the server survives the oversized input and the connection is
    still usable afterward.
    """
    client = TCPDeviceClient(simulator.host, simulator.port)
    try:
        client._link.send_line("RUN_STAGE " + "x" * 5000 + "\n")
        response = client._link.recv_line()
        assert response.strip() == "ERR unknown_stage"

        # connection wasn't corrupted by the oversized line -- still usable
        state = client.get_state()
        assert state.model == "iPhone12"
    finally:
        client.close()
