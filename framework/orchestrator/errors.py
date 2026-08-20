class OrchestratorError(Exception):
    """Base class for all orchestrator-raised errors."""


class NoCompatibleAttackError(OrchestratorError):
    def __init__(self, device_state):
        self.device_state = device_state
        super().__init__(f"no compatible attack for device state: {device_state}")


class DeviceDisconnectedError(OrchestratorError):
    """Raised by a DeviceClient when the connection drops or can't be established."""


class DeviceProtocolError(OrchestratorError):
    """Raised when the device sends a malformed or unexpected response."""

class DeviceNotUnlockedError(OrchestratorError):
    """Raised when list_dir/read_file is attempted before any stage has
    succeeded on the device -- the simulator enforces this server-side, not
    just as a client-side convention."""
