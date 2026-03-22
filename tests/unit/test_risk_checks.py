"""Unit tests for per-client risk checks."""
from __future__ import annotations

from decimal import Decimal

import pytest

from ambot.risk.checks import (
    ClientRiskConfig,
    ClientState,
    PositionState,
    check_daily_loss,
    check_leverage,
    check_open_positions,
    check_per_trade_risk,
    check_symbol_allocation,
)
from ambot.risk.per_client import PerClientRiskGuard
from ambot.strategies.signals import Signal
from ambot.types import OrderSide, OrderType, RiskDecision, SignalAction, Symbol


def make_signal(**kwargs) -> Signal:
    defaults = dict(
        symbol=Symbol("BTCUSDT"),
        action=SignalAction.OPEN,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.1"),
        entry_price=Decimal("50000"),
        stop_loss=Decimal("49000"),
        leverage=Decimal("1"),
    )
    defaults.update(kwargs)
    return Signal(**defaults)


def make_state(**kwargs) -> ClientState:
    defaults = dict(
        client_id="c1",
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        daily_loss_pct=Decimal("0"),
        open_positions={},
    )
    defaults.update(kwargs)
    return ClientState(**defaults)


def make_cfg(**kwargs) -> ClientRiskConfig:
    defaults = dict(
        max_daily_loss_pct=0.02,
        max_open_positions=5,
        max_leverage=3.0,
        max_per_trade_risk_pct=0.01,
        max_symbol_allocation_pct=0.20,
    )
    defaults.update(kwargs)
    return ClientRiskConfig(**defaults)


class TestDailyLossCheck:
    def test_allows_when_under_limit(self):
        result = check_daily_loss(make_signal(), make_state(daily_loss_pct=Decimal("0.01")), make_cfg())
        assert result.action == RiskDecision.ALLOW

    def test_blocks_when_at_limit(self):
        result = check_daily_loss(make_signal(), make_state(daily_loss_pct=Decimal("0.02")), make_cfg())
        assert result.action == RiskDecision.BLOCK

    def test_blocks_when_over_limit(self):
        result = check_daily_loss(make_signal(), make_state(daily_loss_pct=Decimal("0.05")), make_cfg())
        assert result.action == RiskDecision.BLOCK


class TestOpenPositionsCheck:
    def test_allows_when_under_limit(self):
        result = check_open_positions(make_signal(), make_state(), make_cfg(max_open_positions=5))
        assert result.action == RiskDecision.ALLOW

    def test_blocks_when_at_limit(self):
        positions = {f"SYM{i}USDT": PositionState(
            symbol=f"SYM{i}USDT", side="long", quantity=Decimal("1"), entry_price=Decimal("100")
        ) for i in range(5)}
        result = check_open_positions(
            make_signal(action=SignalAction.OPEN),
            make_state(open_positions=positions),
            make_cfg(max_open_positions=5),
        )
        assert result.action == RiskDecision.BLOCK

    def test_close_signals_always_pass(self):
        """CLOSE signals bypass the open position count check."""
        positions = {f"SYM{i}USDT": PositionState(
            symbol=f"SYM{i}USDT", side="long", quantity=Decimal("1"), entry_price=Decimal("100")
        ) for i in range(5)}
        result = check_open_positions(
            make_signal(action=SignalAction.CLOSE),
            make_state(open_positions=positions),
            make_cfg(max_open_positions=5),
        )
        assert result.action == RiskDecision.ALLOW


class TestLeverageCheck:
    def test_allows_within_limit(self):
        result = check_leverage(make_signal(leverage=Decimal("2")), make_state(), make_cfg(max_leverage=3.0))
        assert result.action == RiskDecision.ALLOW

    def test_reduces_when_over_limit(self):
        result = check_leverage(
            make_signal(leverage=Decimal("5"), quantity=Decimal("1.0")),
            make_state(),
            make_cfg(max_leverage=3.0),
        )
        assert result.action == RiskDecision.REDUCE
        assert result.adjusted_size is not None
        # 1.0 * (3/5) = 0.6
        assert abs(float(result.adjusted_size) - 0.6) < 0.001


class TestPerTradeRiskCheck:
    def test_allows_when_risk_within_limit(self):
        # Qty=0.1, entry=50000, sl=49000 → risk=100 USDT, 1% of 10000
        result = check_per_trade_risk(
            make_signal(quantity=Decimal("0.1"), entry_price=Decimal("50000"), stop_loss=Decimal("49000")),
            make_state(equity=Decimal("10000")),
            make_cfg(max_per_trade_risk_pct=0.01),
        )
        assert result.action == RiskDecision.ALLOW

    def test_reduces_when_risk_over_limit(self):
        # Qty=1.0, entry=50000, sl=49000 → risk=1000 USDT = 10% of 10000
        result = check_per_trade_risk(
            make_signal(quantity=Decimal("1.0"), entry_price=Decimal("50000"), stop_loss=Decimal("49000")),
            make_state(equity=Decimal("10000")),
            make_cfg(max_per_trade_risk_pct=0.01),
        )
        assert result.action == RiskDecision.REDUCE
        # Allowed risk = 100 USDT, allowed qty = 100 / 1000 = 0.1
        assert abs(float(result.adjusted_size) - 0.1) < 0.001

    def test_no_stop_loss_always_passes(self):
        result = check_per_trade_risk(
            make_signal(stop_loss=None),
            make_state(),
            make_cfg(),
        )
        assert result.action == RiskDecision.ALLOW


class TestSymbolAllocationCheck:
    def test_allows_within_limit(self):
        # 0.1 BTC × $50,000 = $5,000 = 50% → exceeds 20%
        # Use smaller qty: 0.02 BTC = $1,000 = 10%
        result = check_symbol_allocation(
            make_signal(quantity=Decimal("0.02"), entry_price=Decimal("50000")),
            make_state(equity=Decimal("10000")),
            make_cfg(max_symbol_allocation_pct=0.20),
        )
        assert result.action == RiskDecision.ALLOW

    def test_reduces_when_over_limit(self):
        # 0.1 BTC × $50,000 = $5,000 = 50% of $10,000 equity
        result = check_symbol_allocation(
            make_signal(quantity=Decimal("0.1"), entry_price=Decimal("50000")),
            make_state(equity=Decimal("10000")),
            make_cfg(max_symbol_allocation_pct=0.20),
        )
        assert result.action == RiskDecision.REDUCE
        # Allowed = 10000 × 0.20 / 50000 = 0.04
        assert abs(float(result.adjusted_size) - 0.04) < 0.001


class TestPerClientRiskGuard:
    def test_most_restrictive_reduction_wins(self):
        """When multiple REDUCE checks apply, smallest adjusted_size wins."""
        guard = PerClientRiskGuard(ClientRiskConfig(
            max_leverage=2.0,
            max_symbol_allocation_pct=0.05,
        ))
        signal = make_signal(
            leverage=Decimal("4"),
            quantity=Decimal("0.2"),
            entry_price=Decimal("50000"),
        )
        state = make_state(equity=Decimal("10000"))
        result = guard.evaluate(signal, state)
        assert result.action == RiskDecision.REDUCE
        # Sym alloc: 0.05 × 10000 / 50000 = 0.01
        # Leverage: 0.2 × (2/4) = 0.1
        # Most restrictive = 0.01
        assert float(result.adjusted_size) <= 0.1

    def test_block_takes_priority_over_reduce(self):
        guard = PerClientRiskGuard(ClientRiskConfig(max_daily_loss_pct=0.01))
        state = make_state(daily_loss_pct=Decimal("0.05"))
        result = guard.evaluate(make_signal(), state)
        assert result.action == RiskDecision.BLOCK
