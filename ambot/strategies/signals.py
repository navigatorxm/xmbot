"""
Immutable signal and market snapshot dataclasses.

Signals are frozen dataclasses — once created they cannot be mutated.
This enforces the determinism contract: the same MarketSnapshot always
produces the same Signal(s) from a given strategy version.

The only permitted "mutation" is Signal.with_size(), which returns a NEW
Signal instance with a new UUID, preserving the full audit trail.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from ambot.types import OrderSide, OrderType, SignalAction, Symbol


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class MarketSnapshot:
    """
    Immutable market data snapshot passed to strategy.on_tick().

    All strategies receive the SAME snapshot at each tick.
    The snapshot must include a pre-computed ATR value used by the risk layer.
    """

    symbol: Symbol
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    atr: Decimal          # Average True Range — used by volatility guard
    bar_index: int = 0    # Monotonically increasing bar counter for ordering


@dataclass(frozen=True)
class Signal:
    """
    Immutable trading signal emitted by a strategy.

    frozen=True guarantees that signals cannot be silently mutated between
    strategy emission and order submission.  Any size adjustment by the risk
    layer must call .with_size() which returns a new Signal with its own id.
    """

    # Identity
    id: str = field(default_factory=_new_uuid)
    strategy_name: str = ""
    strategy_version: str = ""

    # Order parameters
    symbol: Symbol = Symbol("")
    action: SignalAction = SignalAction.OPEN
    side: OrderSide = OrderSide.BUY
    order_type: OrderType = OrderType.MARKET
    quantity: Decimal = Decimal("0")

    # Optional price levels
    entry_price: Decimal | None = None    # None → market order
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None

    leverage: Decimal = Decimal("1")
    timestamp: datetime = field(default_factory=_now_utc)

    # Arbitrary extra context (e.g. signal strength, indicator values)
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_size(self, new_quantity: Decimal) -> Signal:
        """
        Return a new Signal with adjusted quantity and a fresh UUID.
        Used by the risk layer when reducing position size.
        """
        return replace(self, quantity=new_quantity, id=_new_uuid())

    def with_leverage(self, new_leverage: Decimal) -> Signal:
        """Return a new Signal with adjusted leverage and a fresh UUID."""
        return replace(self, leverage=new_leverage, id=_new_uuid())

    @property
    def is_entry(self) -> bool:
        return self.action in (SignalAction.OPEN, SignalAction.SCALE)

    @property
    def is_exit(self) -> bool:
        return self.action == SignalAction.CLOSE

    @property
    def risk_per_unit(self) -> Decimal | None:
        """
        Distance from entry to stop-loss per unit.
        Returns None if stop_loss or entry_price is not set.
        """
        if self.stop_loss is None or self.entry_price is None:
            return None
        return abs(self.entry_price - self.stop_loss)
