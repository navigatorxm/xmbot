"""
Global kill switch and volatility guard.
These controls apply across ALL clients — a triggered kill switch halts
the entire engine, not just one client.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from ambot.config import RiskConfig
from ambot.exceptions import KillSwitchTriggered
from ambot.strategies.signals import MarketSnapshot

log = logging.getLogger("ambot.risk.global")


class GlobalKillSwitch:
    """
    Process-global emergency stop.

    Can be triggered by:
    - Admin API call (manual)
    - PositionReconciler (mismatch > threshold)
    - Unhandled exception in engine core

    Thread-safe. Once triggered, all signal processing halts immediately.
    Resetting requires an explicit admin action with an authorization token.
    """

    def __init__(self, cfg: RiskConfig | None = None) -> None:
        self._triggered = threading.Event()
        self._reason: str = ""
        self._triggered_at: datetime | None = None
        self.cfg = cfg

        # If config says kill switch is pre-enabled (e.g. maintenance mode)
        if cfg and cfg.global_kill_switch:
            self.trigger("Pre-enabled via config on startup")

    def trigger(self, reason: str) -> None:
        """Trigger the kill switch. Idempotent — safe to call multiple times."""
        if not self._triggered.is_set():
            self._reason = reason
            self._triggered_at = datetime.now(timezone.utc)
            log.critical("KILL SWITCH TRIGGERED: %s", reason)
        self._triggered.set()

    def is_triggered(self) -> bool:
        return self._triggered.is_set()

    def assert_not_triggered(self) -> None:
        """Raise KillSwitchTriggered if the kill switch is active."""
        if self._triggered.is_set():
            raise KillSwitchTriggered(self._reason)

    def reset(self) -> None:
        """
        Reset the kill switch.
        In production this should require an authorization token checked
        by the admin router before calling this method.
        """
        log.warning(
            "Kill switch reset (was triggered at %s for: %s)",
            self._triggered_at,
            self._reason,
        )
        self._triggered.clear()
        self._reason = ""
        self._triggered_at = None

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def triggered_at(self) -> datetime | None:
        return self._triggered_at


class VolatilityGuard:
    """
    Pauses signal processing when market volatility is extreme.

    Uses ATR as a percentage of price (normalised ATR).
    If normalised_atr > threshold / 100, trading is paused.

    Example: threshold=3.0 → pause if ATR > 3% of close price.
    """

    def __init__(self, atr_threshold_pct: float = 3.0) -> None:
        self.atr_threshold_pct = atr_threshold_pct
        self._paused = False
        self._pause_reason: str = ""

    def check(self, snapshot: MarketSnapshot) -> bool:
        """
        Evaluate volatility for the current bar.

        Returns True if trading should be paused (high volatility).
        """
        if snapshot.close <= 0:
            return False

        normalised_atr = float(snapshot.atr / snapshot.close) * 100

        if normalised_atr > self.atr_threshold_pct:
            if not self._paused:
                log.warning(
                    "VolatilityGuard activated: ATR/close=%.2f%% > threshold=%.2f%% [%s]",
                    normalised_atr,
                    self.atr_threshold_pct,
                    snapshot.symbol,
                )
            self._paused = True
            self._pause_reason = (
                f"ATR {normalised_atr:.2f}% > threshold {self.atr_threshold_pct:.2f}%"
            )
        else:
            if self._paused:
                log.info("VolatilityGuard deactivated: ATR normalised to %.2f%%", normalised_atr)
            self._paused = False
            self._pause_reason = ""

        return self._paused

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def pause_reason(self) -> str:
        return self._pause_reason
