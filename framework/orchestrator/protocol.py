from __future__ import annotations

import socket
from typing import Dict, Any, Optional, Tuple
from .device import DeviceState
from .stages import StageOutcome

class LineLink:
    """
    Handles line-based reading and writing over a socket connection.

    Reads (both line-based and exact-byte-count) all go through one shared
    internal buffer fed by raw socket.recv() calls -- deliberately not
    socket.makefile(), whose buffered wrapper reads ahead into its own
    private buffer that raw .recv() calls on the same socket can't see.
    Mixing the two (a buffered readline() alongside a raw recv() for file
    payloads) silently glues the tail of one response onto the start of the
    next whenever the OS happens to deliver them in the same read.
    """

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buffer = b""

    def _recv_raw(self, n: int) -> bytes:
        return self._sock.recv(n)

    def readline(self) -> str:
        while b"\n" not in self._buffer:
            chunk = self._recv_raw(4096)
            if not chunk:
                raise ConnectionError("Connection closed by peer")
            self._buffer += chunk
        line, self._buffer = self._buffer.split(b"\n", 1)
        return line.decode("utf-8").rstrip("\r")

    def send_line(self, line: str) -> None:
        # `line` is expected to already be newline-terminated (format_command
        # always appends one).
        self._sock.sendall(line.encode("utf-8"))

    def recv_line(self) -> str:
        return self.readline()

    def recv_exact(self, n: int) -> bytes:
        while len(self._buffer) < n:
            chunk = self._recv_raw(4096)
            if not chunk:
                break
            self._buffer += chunk
        data, self._buffer = self._buffer[:n], self._buffer[n:]
        return data

    def read(self, n: int) -> bytes:
        return self.recv_exact(n)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

def format_command(cmd: str, arg: Optional[str] = None) -> str:
    if arg is not None:
        return f"{cmd} {arg}\n"
    return f"{cmd}\n"

def parse_state_response(line: str) -> DeviceState:
    stripped = line.strip()
    if not stripped.startswith("STATE"):
        raise ValueError(f"Invalid STATE response: {line!r}")

    parts = stripped.split()
    info: Dict[str, Any] = {}
    for part in parts[1:]:
        if "=" in part:
            k, v = part.split("=", 1)
            info[k] = v

    if "model" not in info or "ios" not in info or "battery" not in info:
        raise ValueError(f"Missing required fields in STATE response: {line!r}")

    return DeviceState(
        model=info["model"],
        ios_version=DeviceState.parse_ios_version(info["ios"]),
        battery_level=int(info["battery"]),
    )

def parse_run_stage_response(line: str) -> StageOutcome:
    stripped = line.strip()
    if stripped == "OK":
        return StageOutcome(success=True, reason="")
    if stripped.startswith("FAIL"):
        parts = stripped.split(" ", 1)
        reason = parts[1] if len(parts) > 1 else "unknown"
        return StageOutcome(success=False, reason=reason)
    raise ValueError(f"Invalid RUN response: {line!r}")

def parse_list_header(line: str) -> int:
    stripped = line.strip()
    if stripped.startswith("OK "):
        try:
            return int(stripped.split(" ", 1)[1])
        except ValueError:
            pass
    raise ValueError(f"Invalid LIST header: {line!r}")

def parse_list_entry(line: str) -> Tuple[str, bool]:
    stripped = line.strip()
    parts = stripped.split(" ", 1)
    if len(parts) != 2 or parts[0] not in ("F", "D"):
        raise ValueError(f"Invalid LIST entry: {line!r}")
    is_dir = (parts[0] == "D")
    return parts[1], is_dir

def parse_read_header(line: str) -> int:
    stripped = line.strip()
    if stripped.startswith("OK "):
        try:
            return int(stripped.split(" ", 1)[1])
        except ValueError:
            pass
    raise ValueError(f"Invalid READ header: {line!r}")

def is_err(line: str) -> bool:
    return line.strip().startswith("ERR")

def parse_err(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("ERR "):
        return stripped.split(" ", 1)[1]
    raise ValueError(f"Invalid ERR response: {line!r}")
