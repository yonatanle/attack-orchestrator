from __future__ import annotations

from typing import List, Protocol, Tuple, runtime_checkable

from ..device import DeviceState
from ..stages import StageOutcome


@runtime_checkable
class DeviceClient(Protocol):
    """
    The seam that keeps the orchestrator device-agnostic. AttackOrchestrator
    and ExtractionSession only ever talk to this interface -- they cannot
    tell whether they're holding a FakeDeviceClient (in-memory, Part 1) or a
    TCPDeviceClient (real socket to the C simulator, Part 2).
    """

    def get_state(self) -> DeviceState: ...

    def run_stage(self, stage_id: str) -> StageOutcome: ...

    def unlock(self) -> None:
        """Tells the device a full chain has completed, granting read_file/list_dir
        access. Called by AttackOrchestrator exactly once, right when a chain
        actually succeeds -- never inferred from a single stage passing."""
        ...

    def read_file(self, path: str) -> bytes: ...

    def list_dir(self, path: str) -> List[Tuple[str, bool]]:
        """Return (name, is_dir) pairs for entries directly under path."""
        ...

    def reconnect(self) -> None: ...

    def close(self) -> None: ...
