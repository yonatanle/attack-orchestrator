"""
Fixtures for tests that exercise the real C simulator over a real TCP
socket, rather than FakeDeviceClient. These require `gcc`/`make` (build the
simulator under WSL/Linux -- see repo README) and are skipped otherwise.
"""

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

SIMULATOR_DIR = Path(__file__).resolve().parents[2] / "simulator"
SIMULATOR_BIN = SIMULATOR_DIR / "simulator"


def _build_simulator() -> None:
    if not SIMULATOR_BIN.exists() or not os.access(SIMULATOR_BIN, os.X_OK):
        if shutil.which("make") is None or shutil.which("gcc") is None:
            pytest.skip("simulator needs `make`/`gcc` on PATH (build it under WSL/Linux)")
        result = subprocess.run(
            ["make"], cwd=str(SIMULATOR_DIR), capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            pytest.fail(f"failed to build simulator:\n{result.stdout}\n{result.stderr}")
    os.chmod(str(SIMULATOR_BIN), 0o755)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.05)
    raise TimeoutError(f"simulator never opened port {port}: {last_error}")


class SimulatorHandle:
    def __init__(self, host: str, port: int, process: subprocess.Popen):
        self.host = host
        self.port = port
        self._process = process

    def stop(self) -> None:
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)


@pytest.fixture(scope="session", autouse=True)
def _build_simulator_once():
    _build_simulator()


@pytest.fixture
def simulator(request):
    """
    A freshly-started simulator process on its own ephemeral port, torn down
    after the test. Extra CLI args (e.g. to force a stage outcome or a
    mid-chain connection drop) are supplied via:

        @pytest.mark.simulator_args("--force", "pair=success")
    """
    marker = request.node.get_closest_marker("simulator_args")
    extra_args = list(marker.args) if marker else []

    port = _free_port()
    process = subprocess.Popen(
        [str(SIMULATOR_BIN), "--port", str(port), *extra_args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    handle = SimulatorHandle("127.0.0.1", port, process)
    try:
        _wait_for_port(handle.host, handle.port)
        yield handle
    finally:
        handle.stop()
