import pytest

from orchestrator import DeviceDisconnectedError, DeviceState, ExtractionSession
from orchestrator.transport.fake import FakeDeviceClient


def make_state():
    return DeviceState(model="iPhone12", ios_version=(15, 4), battery_level=80)


def make_session(files, dirs):
    client = FakeDeviceClient(state=make_state(), files=files, dirs=dirs)
    return ExtractionSession(client)


def test_read_file_passthrough():
    session = make_session(files={"/contacts.db": b"alice,bob"}, dirs={})
    assert session.read_file("/contacts.db") == b"alice,bob"


def test_extract_all_mirrors_nested_directories(tmp_path):
    files = {
        "/contacts.db": b"contacts-content",
        "/photos/img1.jpg": b"img1-bytes",
        "/photos/img2.jpg": b"img2-bytes",
    }
    dirs = {
        "/": [("contacts.db", False), ("photos", True)],
        "/photos": [("img1.jpg", False), ("img2.jpg", False)],
    }
    session = make_session(files, dirs)

    report = session.extract_all(tmp_path, root="/")

    assert report.succeeded
    assert (tmp_path / "contacts.db").read_bytes() == b"contacts-content"
    assert (tmp_path / "photos" / "img1.jpg").read_bytes() == b"img1-bytes"
    assert (tmp_path / "photos" / "img2.jpg").read_bytes() == b"img2-bytes"
    assert sorted(report.files_written) == [
        "/contacts.db",
        "/photos/img1.jpg",
        "/photos/img2.jpg",
    ]


def test_extract_all_tolerates_a_single_bad_file(tmp_path):
    files = {
        "/contacts.db": b"contacts-content",
        # "/broken.db" deliberately missing from `files` -- list_dir claims
        # it exists but read_file will raise FileNotFoundError for it.
    }
    dirs = {"/": [("contacts.db", False), ("broken.db", False)]}
    session = make_session(files, dirs)

    report = session.extract_all(tmp_path, root="/")

    assert not report.succeeded
    assert "/broken.db" in report.errors
    assert report.files_written == ["/contacts.db"]
    assert (tmp_path / "contacts.db").read_bytes() == b"contacts-content"


def test_extract_all_records_error_for_unreadable_directory(tmp_path):
    session = make_session(files={}, dirs={})  # "/" itself is not a known dir
    report = session.extract_all(tmp_path, root="/")
    assert not report.succeeded
    assert "/" in report.errors


def test_extract_all_recovers_from_a_single_dropped_connection(tmp_path):
    files = {"/contacts.db": b"contacts-content"}
    dirs = {"/": [("contacts.db", False)]}
    client = FakeDeviceClient(state=make_state(), files=files, dirs=dirs)

    real_read_file = client.read_file
    calls = {"n": 0}

    def flaky_read_file(path):
        calls["n"] += 1
        if calls["n"] == 1:
            client.simulate_disconnect()
        return real_read_file(path)

    client.read_file = flaky_read_file
    session = ExtractionSession(client)

    report = session.extract_all(tmp_path, root="/")

    assert report.succeeded
    assert (tmp_path / "contacts.db").read_bytes() == b"contacts-content"


def test_extract_all_records_error_when_reconnect_fails_after_drop(tmp_path):
    files = {"/contacts.db": b"contacts-content"}
    dirs = {"/": [("contacts.db", False)]}
    client = FakeDeviceClient(state=make_state(), files=files, dirs=dirs)

    def broken_reconnect():
        raise DeviceDisconnectedError("device gone for good")

    def always_disconnected_read_file(path):
        raise DeviceDisconnectedError("connection dropped")

    client.reconnect = broken_reconnect
    client.read_file = always_disconnected_read_file
    session = ExtractionSession(client)

    report = session.extract_all(tmp_path, root="/")

    assert not report.succeeded
    assert "/contacts.db" in report.errors
    assert report.files_written == []


def test_extract_all_propagates_a_framework_bug_instead_of_recording_it(tmp_path):
    # A TypeError from a broken transport implementation is a real bug, not
    # a per-file failure that extraction should tolerate and record.
    files = {"/contacts.db": b"contacts-content"}
    dirs = {"/": [("contacts.db", False)]}
    client = FakeDeviceClient(state=make_state(), files=files, dirs=dirs)

    def broken_read_file(path):
        raise TypeError("read_file() missing 1 required positional argument")

    client.read_file = broken_read_file
    session = ExtractionSession(client)

    with pytest.raises(TypeError):
        session.extract_all(tmp_path, root="/")
