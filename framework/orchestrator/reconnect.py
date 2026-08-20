from __future__ import annotations

from .errors import DeviceDisconnectedError
from .transport.base import DeviceClient


def try_reconnect(device_client: DeviceClient, max_attempts: int) -> bool:
    """
    Attempts device_client.reconnect() up to max_attempts times. Returns True
    on the first successful attempt, False if every attempt raised
    DeviceDisconnectedError. Shared by AttackOrchestrator (reconnecting mid
    stage-chain) and ExtractionSession (reconnecting mid file-walk) so both
    get identical bounded-retry behavior instead of two copies of the same
    loop drifting apart.
    """
    for _ in range(max_attempts):
        try:
            device_client.reconnect()
            return True
        except DeviceDisconnectedError:
            continue
    return False
