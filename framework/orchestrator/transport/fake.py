from __future__ import annotations

import random
from typing import Dict, List, Optional, Tuple

from ..device import DeviceState
from ..errors import DeviceDisconnectedError
from ..stages import StageOutcome


class FakeDeviceClient:
    """
    In-memory DeviceClient. Lets Part 1 be built and demoed with zero
    network code, and keeps unit tests fast and deterministic.

    Two outcome modes per stage, chosen per stage_id:
      - scripted (stage_outcomes): a fixed StageOutcome, for deterministic
        unit tests that need to force a specific stage to fail.
      - probabilistic (stage_probabilities): rolls against a probability,
        so a plain demo -- no simulator, no C code -- still genuinely
        exercises "stages that probabilistically succeed or fail" per the
        Part 1 requirement. Typically seeded with the same
        expected_success_probability as the matching Stage.

    If a stage_id has neither, it defaults to always succeeding.
    """

    def __init__(
        self,
        state: DeviceState,
        files: Optional[Dict[str, bytes]] = None,
        dirs: Optional[Dict[str, List[Tuple[str, bool]]]] = None,
        stage_outcomes: Optional[Dict[str, StageOutcome]] = None,
        stage_probabilities: Optional[Dict[str, float]] = None,
        rng: Optional[random.Random] = None,
    ):
        self._state = state
        self._files = dict(files or {})
        self._dirs = dict(dirs or {})
        self._stage_outcomes = dict(stage_outcomes or {})
        self._stage_probabilities = dict(stage_probabilities or {})
        self._rng = rng or random.Random()
        self._connected = True
        self.unlock_called = False

    def get_state(self) -> DeviceState:
        self._require_connected()
        return self._state

    def run_stage(self, stage_id: str) -> StageOutcome:
        self._require_connected()
        if stage_id in self._stage_outcomes:
            return self._stage_outcomes[stage_id]
        probability = self._stage_probabilities.get(stage_id, 1.0)
        success = self._rng.random() < probability
        return StageOutcome(success=success, reason="" if success else "simulated failure")

    def unlock(self) -> None:
        # FakeDeviceClient never gated read_file/list_dir behind an unlock
        # state in the first place (that server-side enforcement only lives
        # in the real simulator) -- this exists purely to satisfy the
        # DeviceClient interface so AttackOrchestrator can call it
        # unconditionally regardless of which transport it's holding.
        # unlock_called is a test hook: it lets a unit test assert the
        # orchestrator actually called unlock() before handing back a
        # session, without needing the real simulator's server-side gate.
        self._require_connected()
        self.unlock_called = True

    def read_file(self, path: str) -> bytes:
        self._require_connected()
        if path not in self._files:
            raise FileNotFoundError(path)
        return self._files[path]

    def list_dir(self, path: str) -> List[Tuple[str, bool]]:
        self._require_connected()
        if path not in self._dirs:
            raise FileNotFoundError(path)
        return list(self._dirs[path])

    def reconnect(self) -> None:
        self._connected = True

    def close(self) -> None:
        self._connected = False

    def simulate_disconnect(self) -> None:
        """Test hook: makes the next call raise DeviceDisconnectedError, as a real dropped
        TCP connection would."""
        self._connected = False

    def _require_connected(self) -> None:
        if not self._connected:
            raise DeviceDisconnectedError("fake device connection is closed")
