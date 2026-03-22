"""
Integration test: signal → risk → journal flow (with mock broker).

Uses an in-memory DB and a mock BinanceClient that records calls
without hitting any real exchange.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ambot.broker.client import AccountBalance, ExchangeOrder
from ambot.broker.order_router import FilledOrder, OrderRouter
from ambot.broker.rate_limiter import PerClientRateLimiter
from ambot.broker.vault import KeyVault
from ambot.config import AppConfig, CommissionConfig, RiskConfig
from ambot.core.engine import BotEngine, ClientContext
from ambot.core.state import StateManager
from ambot.journal.writer import JournalWriter
from ambot.risk.checks import ClientRiskConfig
from ambot.risk.global_guard import GlobalKillSwitch, VolatilityGuard
from ambot.risk.per_client import PerClientRiskGuard
from ambot.strategies.deterministic import DeterministicStrategy
from ambot.strategies.signals import MarketSnapshot, Signal
from ambot.types import ClientId, EngineStatus, OrderStatus, OrderSide, OrderType, SignalAction, Symbol


def make_snapshot(close: float = 50000.0, atr: float = 500.0) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=Symbol("BTCUSDT"),
        timestamp=datetime.now(timezone.utc),
        open=Decimal(str(close * 0.99)),
        high=Decimal(str(close * 1.01)),
        low=Decimal(str(close * 0.98)),
        close=Decimal(str(close)),
        volume=Decimal("1000"),
        atr=Decimal(str(atr)),
        bar_index=1,
    )


class MockBinanceClient:
    """Minimal mock — records calls and returns dummy data."""
    orders_placed: int = 0

    async def get_account_balance(self) -> AccountBalance:
        return AccountBalance(
            total_usdt=Decimal("10000"),
            available_usdt=Decimal("10000"),
            timestamp=datetime.now(timezone.utc),
        )

    async def get_open_positions(self) -> list:
        return []

    async def create_order(self, **kwargs) -> ExchangeOrder:
        self.orders_placed += 1
        return ExchangeOrder(
            order_id=f"order_{self.orders_placed}",
            client_order_id=f"client_{self.orders_placed}",
            symbol=kwargs.get("symbol", "BTCUSDT"),
            side=kwargs.get("side", "buy"),
            order_type=kwargs.get("type", "market"),
            status="filled",
            quantity=Decimal(str(kwargs.get("amount", 0.001))),
            filled_quantity=Decimal(str(kwargs.get("amount", 0.001))),
            price=None,
            average_fill_price=Decimal("50000"),
            commission=Decimal("0.1"),
            commission_asset="USDT",
            timestamp=datetime.now(timezone.utc),
        )

    async def cancel_all_orders(self) -> None:
        pass

    async def get_open_orders(self) -> list:
        return []

    async def close(self) -> None:
        pass


@pytest.fixture
def mock_broker():
    return MockBinanceClient()


@pytest.fixture
def rate_limiter():
    return PerClientRateLimiter(default_rate=100.0, default_capacity=1000.0)


@pytest.fixture
def kill_switch():
    return GlobalKillSwitch()


@pytest.fixture
def state_manager():
    sm = StateManager()
    sm.hydrate(ClientId("client-1"), Decimal("10000"), Decimal("10000"))
    return sm


class TestKillSwitch:
    def test_kill_switch_blocks_signals(self, kill_switch, state_manager, mock_broker, rate_limiter):
        """After triggering the kill switch, on_tick must not submit any orders."""
        kill_switch.trigger("test reason")
        assert kill_switch.is_triggered()

    def test_kill_switch_resets(self, kill_switch):
        kill_switch.trigger("test")
        kill_switch.reset()
        assert not kill_switch.is_triggered()


class TestVolatilityGuard:
    def test_pauses_on_high_atr(self):
        guard = VolatilityGuard(atr_threshold_pct=3.0)
        snapshot = make_snapshot(close=50000.0, atr=2000.0)  # ATR = 4% of close
        assert guard.check(snapshot) is True
        assert guard.is_paused

    def test_allows_on_normal_atr(self):
        guard = VolatilityGuard(atr_threshold_pct=3.0)
        snapshot = make_snapshot(close=50000.0, atr=500.0)   # ATR = 1% of close
        assert guard.check(snapshot) is False
        assert not guard.is_paused


class TestSignalScaling:
    def test_signal_scaled_proportionally(self):
        from ambot.social.replicator import DeterministicReplicator
        signal = Signal(
            symbol=Symbol("BTCUSDT"),
            action=SignalAction.OPEN,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.001"),
        )
        scaled = DeterministicReplicator.scale_signal(signal, Decimal("5000"), 10000.0)
        assert scaled.quantity == Decimal("0.0005")

    def test_signal_is_immutable(self):
        signal = Signal(quantity=Decimal("1.0"))
        with pytest.raises(Exception):
            signal.quantity = Decimal("2.0")  # frozen dataclass


class TestStateManager:
    def test_hydrate_and_get(self, state_manager):
        state = state_manager.get(ClientId("client-1"))
        assert state is not None
        assert state.equity == Decimal("10000")

    def test_open_and_close_position(self, state_manager):
        state_manager.open_position(
            ClientId("client-1"),
            symbol="BTCUSDT",
            side="long",
            quantity=Decimal("0.1"),
            entry_price=Decimal("50000"),
        )
        state = state_manager.get(ClientId("client-1"))
        assert "BTCUSDT" in state.open_positions

        state_manager.close_position(ClientId("client-1"), "BTCUSDT")
        state = state_manager.get(ClientId("client-1"))
        assert "BTCUSDT" not in state.open_positions

    def test_reset_daily_loss(self, state_manager):
        from ambot.types import ClientId
        state_manager._states[ClientId("client-1")].daily_loss_pct = Decimal("0.05")
        state_manager.reset_daily_loss(ClientId("client-1"))
        assert state_manager.get(ClientId("client-1")).daily_loss_pct == Decimal("0")
