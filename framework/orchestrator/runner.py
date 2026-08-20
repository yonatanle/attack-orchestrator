from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .attacks import Attack
from .device import DeviceState
from .errors import DeviceDisconnectedError, NoCompatibleAttackError
from .extraction import ExtractionSession
from .reconnect import try_reconnect
from .selection import AttackSelector, HighestExpectedSuccessSelector
from .transport.base import DeviceClient

logger = logging.getLogger(__name__)

# init_attack/teardown are arbitrary user-supplied callables, so we can't
# know in advance what they'll legitimately raise to signal their own
# domain failure -- but a TypeError/AttributeError/NameError almost always
# means the hook itself is broken (wrong arity, typo'd attribute, etc.),
# not that it "decided to fail". Those are call-mechanics/programmer
# errors and should surface as real bugs rather than being silently
# recorded as "init_attack failed" / a teardown_error.
_HOOK_PROGRAMMER_ERRORS = (TypeError, AttributeError, NameError)


@dataclass
class AttackAttemptResult:
    attack: Attack
    succeeded: bool
    failed_stage_id: Optional[str] = None
    reason: str = ""
    reconnected: bool = False
    teardown_error: Optional[str] = None


@dataclass
class AttackRunResult:
    device_state: DeviceState
    attempts: List[AttackAttemptResult] = field(default_factory=list)
    session: Optional[ExtractionSession] = None

    @property
    def succeeded(self) -> bool:
        return self.session is not None

    @property
    def successful_attack(self) -> Optional[Attack]:
        for attempt in self.attempts:
            if attempt.succeeded:
                return attempt.attack
        return None


class AttackOrchestrator:
    """
    Selects a compatible attack for a device and runs its lifecycle --
    init_attack (if any) -> stages -> teardown (if any) -- falling back to
    the next-best compatible attack on failure rather than giving up
    outright. This is a deliberate default, not an obvious one:

    A failed stage aborts *that* attack's chain immediately (no in-place
    retry -- a real exploit attempt either lands or it doesn't, retrying the
    same stage against the same device risks tripping a lockout). But
    falling back to try a different attack is itself risky against a real
    device (repeated unlock attempts can trigger a wipe), which is exactly
    why `max_attempts` exists as a knob rather than always exhausting every
    candidate.

    init_attack failing is treated the same as a stage failing: the attempt
    is marked failed with that reason and the orchestrator moves on to the
    next candidate. teardown always runs after an attempt that got past
    init_attack -- on success *and* failure -- since it may need to revert
    partial device state either way; a teardown exception is recorded on
    the result (`teardown_error`) but never flips succeeded/failed, since
    teardown is best-effort cleanup, not part of the attack's own outcome.
    """

    def __init__(
        self,
        attacks: Sequence[Attack],
        selector: Optional[AttackSelector] = None,
        max_attempts: Optional[int] = None,
        max_reconnect_attempts: int = 1,
    ):
        if not attacks:
            raise ValueError("orchestrator needs at least one attack")
        self._attacks = list(attacks)
        self._selector = selector or HighestExpectedSuccessSelector()
        self._max_attempts = max_attempts  # None = try every compatible attack
        self._max_reconnect_attempts = max_reconnect_attempts

    def run(self, device_client: DeviceClient) -> AttackRunResult:
        state = device_client.get_state()
        logger.info(
            "queried device: %s, iOS %s, battery=%d%%",
            state.model,
            state.ios_version_str,
            state.battery_level,
        )
        candidates = [a for a in self._attacks if a.is_compatible(state)]
        if not candidates:
            raise NoCompatibleAttackError(state)
        logger.info("%d compatible attack(s) found", len(candidates))

        attempts: List[AttackAttemptResult] = []
        remaining = candidates
        attempt_limit = self._max_attempts or len(candidates)

        while remaining and len(attempts) < attempt_limit:
            chosen = self._selector.select(remaining, state)
            remaining = [a for a in remaining if a is not chosen]
            if attempts:
                logger.info("falling back to %r", chosen.name)
            else:
                logger.info(
                    "selected %r (score=%.2f)", chosen.name, chosen.expected_success_probability
                )

            result = self._run_one_attack(device_client, chosen, state)
            attempts.append(result)
            if result.succeeded:
                logger.info("chain completed, sending UNLOCK")
                self._unlock_device(device_client)
                return AttackRunResult(
                    device_state=state,
                    attempts=attempts,
                    session=ExtractionSession(
                        device_client, max_reconnect_attempts=self._max_reconnect_attempts
                    ),
                )

        return AttackRunResult(device_state=state, attempts=attempts, session=None)

    def _unlock_device(self, device_client: DeviceClient) -> None:
        """
        Tells the device a chain actually completed, so its own LIST/READ
        gate (see simulator/src/protocol.c's `unlocked` flag) reflects a
        real completed chain rather than approximating it from any single
        successful stage -- the device has no concept of "chains" on its
        own, so the framework, which does, has to say so explicitly. Gets
        the same bounded reconnect-and-retry treatment as stage execution,
        since the connection can drop here just as easily as mid-chain.
        """
        try:
            device_client.unlock()
        except DeviceDisconnectedError:
            if not self._try_reconnect(device_client):
                raise
            device_client.unlock()

    def _run_one_attack(
        self, device_client: DeviceClient, attack: Attack, state: DeviceState
    ) -> AttackAttemptResult:
        if attack.init_attack is not None:
            try:
                attack.init_attack(device_client, state)
            except _HOOK_PROGRAMMER_ERRORS:
                raise
            except Exception as exc:  # init failing just means "try the next attack"
                return AttackAttemptResult(
                    attack=attack,
                    succeeded=False,
                    reason=f"init_attack failed: {exc}",
                )

        result = self._run_stages(device_client, attack)

        if attack.teardown is not None:
            try:
                attack.teardown(device_client, state, result)
            except _HOOK_PROGRAMMER_ERRORS:
                raise
            except Exception as exc:  # teardown is best-effort, doesn't change the outcome
                result.teardown_error = str(exc)

        return result

    def _run_stages(self, device_client: DeviceClient, attack: Attack) -> AttackAttemptResult:
        # Tracks whether *any* stage in this attempt needed a reconnect, so a
        # chain that recovers from a mid-chain drop and then succeeds still
        # reports that it had to recover -- not just chains that ultimately fail.
        reconnected = False
        for stage in attack.stages:
            started = time.monotonic()
            try:
                outcome = device_client.run_stage(stage.id)
            except DeviceDisconnectedError:
                logger.info("stage %r ... connection lost, reconnecting...", stage.id)
                if not self._try_reconnect(device_client):
                    logger.info("stage %r ... reconnect failed", stage.id)
                    return AttackAttemptResult(
                        attack=attack,
                        succeeded=False,
                        failed_stage_id=stage.id,
                        reason="connection dropped and reconnect failed",
                        reconnected=reconnected,
                    )
                reconnected = True
                logger.info("stage %r ... reconnected, retrying", stage.id)
                try:
                    outcome = device_client.run_stage(stage.id)
                except DeviceDisconnectedError:
                    logger.info("stage %r ... connection lost again after reconnect", stage.id)
                    return AttackAttemptResult(
                        attack=attack,
                        succeeded=False,
                        failed_stage_id=stage.id,
                        reason="connection dropped again after reconnect",
                        reconnected=True,
                    )

            elapsed = time.monotonic() - started
            if not outcome.success:
                logger.info(
                    "stage %r ... FAIL: %s (%.1fs)", stage.id, outcome.reason or "stage failed", elapsed
                )
                return AttackAttemptResult(
                    attack=attack,
                    succeeded=False,
                    failed_stage_id=stage.id,
                    reason=outcome.reason or "stage failed",
                    reconnected=reconnected,
                )
            logger.info("stage %r ... OK (%.1fs)", stage.id, elapsed)

        return AttackAttemptResult(attack=attack, succeeded=True, reconnected=reconnected)

    def _try_reconnect(self, device_client: DeviceClient) -> bool:
        return try_reconnect(device_client, self._max_reconnect_attempts)
