"""Unit tests for the deterministic signal replicator."""
from __future__ import annotations

from decimal import Decimal

import pytest

from ambot.social.replicator import DeterministicReplicator
from ambot.strategies.signals import Signal
from ambot.types import OrderSide, OrderType, SignalAction, Symbol


def make_signal(qty: float = 1.0, entry: float = 50000.0) -> Signal:
    return Signal(
        symbol=Symbol("BTCUSDT"),
        action=SignalAction.OPEN,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal(str(qty)),
        entry_price=Decimal(str(entry)),
        stop_loss=Decimal(str(entry * 0.98)),
        take_profit=Decimal(str(entry * 1.06)),
    )


class TestDeterministicReplicator:
    def test_scale_1x_reference_equity_unchanged(self):
        signal = make_signal(qty=1.0)
        scaled = DeterministicReplicator.scale_signal(
            signal, client_equity=Decimal("10000"), reference_equity=10000.0
        )
        assert scaled.quantity == Decimal("1.0")

    def test_scale_half_equity(self):
        signal = make_signal(qty=1.0)
        scaled = DeterministicReplicator.scale_signal(
            signal, client_equity=Decimal("5000"), reference_equity=10000.0
        )
        assert scaled.quantity == Decimal("0.5")

    def test_scale_double_equity(self):
        signal = make_signal(qty=1.0)
        scaled = DeterministicReplicator.scale_signal(
            signal, client_equity=Decimal("20000"), reference_equity=10000.0
        )
        assert scaled.quantity == Decimal("2.0")

    def test_scaled_signal_has_new_id(self):
        signal = make_signal()
        scaled = DeterministicReplicator.scale_signal(
            signal, client_equity=Decimal("5000"), reference_equity=10000.0
        )
        assert scaled.id != signal.id

    def test_direction_and_levels_preserved(self):
        signal = make_signal(qty=1.0, entry=50000.0)
        scaled = DeterministicReplicator.scale_signal(
            signal, client_equity=Decimal("5000"), reference_equity=10000.0
        )
        # Only quantity changes — all other fields preserved
        assert scaled.side == signal.side
        assert scaled.action == signal.action
        assert scaled.stop_loss == signal.stop_loss
        assert scaled.take_profit == signal.take_profit
        assert scaled.entry_price == signal.entry_price

    def test_zero_reference_equity_returns_original(self):
        signal = make_signal(qty=1.0)
        scaled = DeterministicReplicator.scale_signal(
            signal, client_equity=Decimal("5000"), reference_equity=0.0
        )
        assert scaled is signal

    def test_zero_client_equity_returns_original(self):
        signal = make_signal(qty=1.0)
        scaled = DeterministicReplicator.scale_signal(
            signal, client_equity=Decimal("0"), reference_equity=10000.0
        )
        assert scaled is signal

    def test_sl_pct_preserved_after_scaling(self):
        """SL distance as % of entry must be identical before and after scaling."""
        signal = make_signal(qty=1.0, entry=50000.0)
        scaled = DeterministicReplicator.scale_signal(
            signal, client_equity=Decimal("5000"), reference_equity=10000.0
        )
        original_sl_pct = DeterministicReplicator.scale_stop_loss_pct(signal)
        scaled_sl_pct = DeterministicReplicator.scale_stop_loss_pct(scaled)
        assert original_sl_pct == scaled_sl_pct
