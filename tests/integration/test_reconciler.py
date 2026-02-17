"""Integration tests for the PositionReconciler."""
from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timezone

import pytest

from ambot.broker.client import AccountBalance, ExchangePosition
from ambot.config import RiskConfig
from ambot.core.state import StateManager
from ambot.exceptions import PositionMismatch
from ambot.reconciliation.reconciler import PositionReconciler
from ambot.risk.global_guard import GlobalKillSwitch
from ambot.types import ClientId


class MockBrokerClient:
    """Mock broker for reconciler tests."""
    def __init__(self, positions: list[ExchangePosition] = None):
        self._positions = positions or []

    async def get_open_positions(self) -> list[ExchangePosition]:
        return self._positions

    async def get_account_balance(self) -> AccountBalance:
        return AccountBalance(
            total_usdt=Decimal("10000"),
            available_usdt=Decimal("10000"),
            timestamp=datetime.now(timezone.utc),
        )


class MockOrderRouter:
    def __init__(self, broker):
        self._client = broker


class MockClientContext:
    def __init__(self, client_id: str, broker: MockBrokerClient):
        self.client_id = client_id
        self.is_active = True
        self.order_router = MockOrderRouter(broker)


@pytest.fixture
def kill_switch():
    return GlobalKillSwitch()


@pytest.fixture
def state():
    sm = StateManager()
    sm.hydrate(ClientId("c1"), Decimal("10000"), Decimal("10000"))
    return sm


@pytest.fixture
def risk_cfg():
    return RiskConfig(reconciliation_mismatch_pct=0.02)


@pytest.mark.asyncio
async def test_clean_reconciliation_no_trigger(kill_switch, state, risk_cfg):
    """When broker and internal state match, kill switch stays off."""
    broker = MockBrokerClient(positions=[])
    ctx = MockClientContext("c1", broker)

    reconciler = PositionReconciler(
        session_factory=None,
        cfg=risk_cfg,
        kill_switch=kill_switch,
        state_manager=state,
        client_contexts={"c1": ctx},
    )
    await reconciler.run_cycle()
    assert not kill_switch.is_triggered()


@pytest.mark.asyncio
async def test_ghost_position_injected(kill_switch, state, risk_cfg):
    """A position on the broker but not internally → injected, no kill switch."""
    broker = MockBrokerClient(positions=[
        ExchangePosition(
            symbol="BTCUSDT", side="long",
            quantity=Decimal("0.1"), entry_price=Decimal("50000"),
            mark_price=Decimal("50000"), unrealized_pnl=Decimal("0"),
            leverage=Decimal("1"),
        )
    ])
    ctx = MockClientContext("c1", broker)

    reconciler = PositionReconciler(
        session_factory=None,
        cfg=risk_cfg,
        kill_switch=kill_switch,
        state_manager=state,
        client_contexts={"c1": ctx},
    )
    await reconciler.run_cycle()
    assert not kill_switch.is_triggered()

    # Ghost position should have been injected
    internal_state = state.get(ClientId("c1"))
    assert "BTCUSDT" in internal_state.open_positions


@pytest.mark.asyncio
async def test_large_mismatch_triggers_kill_switch(kill_switch, state, risk_cfg):
    """A > 2% mismatch between broker qty and internal qty triggers kill switch."""
    # Open a position internally
    state.open_position(ClientId("c1"), "BTCUSDT", "long", Decimal("1.0"), Decimal("50000"))

    # Broker reports 0 (position was closed externally)
    broker = MockBrokerClient(positions=[
        ExchangePosition(
            symbol="BTCUSDT", side="long",
            quantity=Decimal("0.5"),  # 50% less than internal 1.0
            entry_price=Decimal("50000"), mark_price=Decimal("50000"),
            unrealized_pnl=Decimal("0"), leverage=Decimal("1"),
        )
    ])
    ctx = MockClientContext("c1", broker)

    reconciler = PositionReconciler(
        session_factory=None,
        cfg=risk_cfg,
        kill_switch=kill_switch,
        state_manager=state,
        client_contexts={"c1": ctx},
    )
    await reconciler.run_cycle()
    assert kill_switch.is_triggered()
