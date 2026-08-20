from __future__ import annotations

import socket
from typing import List, Tuple

from ..device import DeviceState
from ..errors import DeviceDisconnectedError, DeviceNotUnlockedError, DeviceProtocolError
from .. import protocol
from ..stages import StageOutcome
from .base import DeviceClient


class TCPDeviceClient(DeviceClient):
    def __init__(self, host: str, port: int, timeout: float = 5.0):
        self._host = host
        self._port = port
        self._timeout = timeout
        self._link = self._connect()

    def _connect(self) -> protocol.LineLink:
        try:
            sock = socket.create_connection((self._host, self._port), timeout=self._timeout)
            return protocol.LineLink(sock)
        except (socket.error, OSError) as exc:
            raise DeviceDisconnectedError(f"could not connect to device: {exc}") from exc

    def reconnect(self) -> None:
        try:
            if self._link:
                try:
                    self._link.close()
                except OSError:
                    pass
            self._link = self._connect()
        except OSError as exc:
            raise DeviceDisconnectedError(f"reconnect failed: {exc}") from exc

    def get_state(self) -> DeviceState:
        try:
            self._link.send_line(protocol.format_command("STATE"))
            line = self._link.recv_line()
        except DeviceDisconnectedError:
            raise
        except OSError as exc:
            raise DeviceDisconnectedError(f"connection lost during get_state: {exc}") from exc

        if protocol.is_err(line):
            raise DeviceProtocolError(protocol.parse_err(line))
        return protocol.parse_state_response(line)

    def run_stage(self, stage_id: str) -> StageOutcome:
        try:
            self._link.send_line(protocol.format_command("RUN_STAGE", stage_id))
            line = self._link.recv_line()
        except DeviceDisconnectedError:
            raise
        except OSError as exc:
            raise DeviceDisconnectedError(f"connection lost during run_stage: {exc}") from exc

        if protocol.is_err(line):
            raise DeviceProtocolError(protocol.parse_err(line))
        return protocol.parse_run_stage_response(line)

    def unlock(self) -> None:
        try:
            self._link.send_line(protocol.format_command("UNLOCK"))
            line = self._link.recv_line()
        except DeviceDisconnectedError:
            raise
        except OSError as exc:
            raise DeviceDisconnectedError(f"connection lost during unlock: {exc}") from exc

        if protocol.is_err(line):
            raise DeviceProtocolError(protocol.parse_err(line))

    def list_dir(self, path: str) -> List[Tuple[str, bool]]:
        try:
            self._link.send_line(protocol.format_command("LIST", path))
            header = self._link.recv_line()
        except DeviceDisconnectedError:
            raise
        except OSError as exc:
            raise DeviceDisconnectedError(f"connection lost during list_dir: {exc}") from exc

        if protocol.is_err(header):
            reason = protocol.parse_err(header)
            if reason == "not_unlocked":
                raise DeviceNotUnlockedError(reason)
            raise FileNotFoundError(reason)

        count = protocol.parse_list_header(header)
        entries = []
        for _ in range(count):
            try:
                line = self._link.recv_line()
            except OSError as exc:
                raise DeviceDisconnectedError(f"connection lost reading list entry: {exc}") from exc
            entries.append(protocol.parse_list_entry(line))
        return entries

    def read_file(self, path: str) -> bytes:
        try:
            self._link.send_line(protocol.format_command("READ", path))
            header = self._link.recv_line()
        except DeviceDisconnectedError:
            raise
        except OSError as exc:
            raise DeviceDisconnectedError(f"connection lost during read_file: {exc}") from exc

        if protocol.is_err(header):
            reason = protocol.parse_err(header)
            if reason == "not_unlocked":
                raise DeviceNotUnlockedError(reason)
            raise FileNotFoundError(reason)

        length = protocol.parse_read_header(header)
        try:
            data = self._link.recv_exact(length)
            trailing = self._link.recv_exact(1)
        except OSError as exc:
            raise DeviceDisconnectedError(f"connection lost reading file payload: {exc}") from exc

        if trailing != b"\n":
            raise DeviceProtocolError("expected trailing newline after READ payload")
        return data

    def close(self) -> None:
        try:
            self._link.send_line(protocol.format_command("QUIT"))
        except OSError:
            pass
        finally:
            self._link.close()
