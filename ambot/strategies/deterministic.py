"""
DeterministicStrategy — the production trading strategy.

This implements a simple but robust EMA crossover with ATR-based stop-loss,
serving as the default deterministic strategy. The strategy is fully
parameterized so any parameter change requires a version bump.

Signal generation rules
-----------------------
OPEN BUY  : fast EMA crosses above slow EMA (bullish cross)
OPEN SELL : fast EMA crosses below slow EMA (bearish cross)
CLOSE     : opposite cross OR stop-loss breach (stop-loss managed externally via order)

All signals include:
- entry_price  = current close (market order)
- stop_loss    = close ± (atr_multiplier × ATR)
- take_profit  = close ± (tp_multiplier × ATR)
- leverage     = configured leverage
"""
from __future__ import annotations

from collections import deque
from decimal import Decimal

from ambot.strategies.base import AbstractStrategy
from ambot.strategies.signals import MarketSnapshot, Signal
from ambot.types import OrderSide, OrderType, SignalAction, Symbol


def _ema(prices: list[Decimal], period: int) -> Decimal:
    """Compute EMA over the last `period` prices. Returns 0 if insufficient data."""
    if len(prices) < period:
        return Decimal("0")
    k = Decimal("2") / (Decimal(period) + Decimal("1"))
    ema = prices[0]
    for price in prices[1:]:
        ema = price * k + ema * (Decimal("1") - k)
    return ema


class DeterministicStrategy(AbstractStrategy):
    """
    EMA crossover strategy with ATR-based risk management.

    Parameters
    ----------
    symbol:      Trading pair, e.g. "BTCUSDT"
    fast_period: Fast EMA period (default 9)
    slow_period: Slow EMA period (default 21)
    atr_sl_mult: ATR multiplier for stop-loss distance (default 1.5)
    atr_tp_mult: ATR multiplier for take-profit distance (default 3.0)
    leverage:    Order leverage (default 1)
    base_qty:    Reference quantity for 1× equity sizing (scaled per client)
    """

    _name = "ema_cross"
    _version = "1.0.0"

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        fast_period: int = 9,
        slow_period: int = 21,
        atr_sl_mult: float = 1.5,
        atr_tp_mult: float = 3.0,
        leverage: float = 1.0,
        base_qty: float = 0.001,
    ) -> None:
        self._symbol = Symbol(symbol)
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.atr_sl_mult = Decimal(str(atr_sl_mult))
        self.atr_tp_mult = Decimal(str(atr_tp_mult))
        self._leverage = Decimal(str(leverage))
        self._base_qty = Decimal(str(base_qty))

        # Rolling price buffer — only keep what's needed
        self._closes: deque[Decimal] = deque(maxlen=slow_period + 1)
        self._prev_fast_ema: Decimal = Decimal("0")
        self._prev_slow_ema: Decimal = Decimal("0")
        self._in_long: bool = False
        self._in_short: bool = False

    # ── AbstractStrategy ──────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return self._version

    @property
    def symbols(self) -> list[str]:
        return [self._symbol]

    def on_tick(self, snapshot: MarketSnapshot) -> list[Signal]:
        """
        Deterministic: given identical snapshot → identical signals.
        Returns empty list if insufficient data or no cross detected.
        """
        self._closes.append(snapshot.close)
        closes = list(self._closes)

        if len(closes) < self.slow_period:
            return []

        fast_ema = _ema(closes, self.fast_period)
        slow_ema = _ema(closes, self.slow_period)

        signals: list[Signal] = []

        # Bullish cross: fast crosses above slow
        if (
            self._prev_fast_ema <= self._prev_slow_ema
            and fast_ema > slow_ema
            and not self._in_long
        ):
            sl = snapshot.close - self.atr_sl_mult * snapshot.atr
            tp = snapshot.close + self.atr_tp_mult * snapshot.atr

            if self._in_short:
                # Close the short first
                signals.append(self._make_signal(
                    snapshot, SignalAction.CLOSE, OrderSide.BUY
                ))
                self._in_short = False

            signals.append(self._make_signal(
                snapshot, SignalAction.OPEN, OrderSide.BUY,
                stop_loss=sl, take_profit=tp,
            ))
            self._in_long = True

        # Bearish cross: fast crosses below slow
        elif (
            self._prev_fast_ema >= self._prev_slow_ema
            and fast_ema < slow_ema
            and not self._in_short
        ):
            sl = snapshot.close + self.atr_sl_mult * snapshot.atr
            tp = snapshot.close - self.atr_tp_mult * snapshot.atr

            if self._in_long:
                signals.append(self._make_signal(
                    snapshot, SignalAction.CLOSE, OrderSide.SELL
                ))
                self._in_long = False

            signals.append(self._make_signal(
                snapshot, SignalAction.OPEN, OrderSide.SELL,
                stop_loss=sl, take_profit=tp,
            ))
            self._in_short = True

        self._prev_fast_ema = fast_ema
        self._prev_slow_ema = slow_ema
        return signals

    def on_fill(self, fill: object) -> None:
        """Update internal position tracking on confirmed fill."""
        # Position state is primarily managed by StateManager;
        # strategy only needs to know if it's in a position.
        pass

    # ── Private ───────────────────────────────────────────────────────────────

    def _make_signal(
        self,
        snapshot: MarketSnapshot,
        action: SignalAction,
        side: OrderSide,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
    ) -> Signal:
        return Signal(
            strategy_name=self._name,
            strategy_version=self._version,
            symbol=self._symbol,
            action=action,
            side=side,
            order_type=OrderType.MARKET,
            quantity=self._base_qty,
            entry_price=snapshot.close,
            stop_loss=stop_loss,
            take_profit=take_profit,
            leverage=self._leverage,
            timestamp=snapshot.timestamp,
            metadata={
                "bar_index": snapshot.bar_index,
                "atr": str(snapshot.atr),
            },
        )
