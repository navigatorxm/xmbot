"""Integration test for commission snapshot service."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ambot.commissions.calculator import HybridCommissionCalculator
from ambot.commissions.snapshot import MonthlySnapshotService
from ambot.commissions.watermark import HighWatermarkTracker
from ambot.config import CommissionConfig
from ambot.core.persistence import Base, Client, CommissionSnapshot
from ambot.exceptions import SnapshotAlreadyExists
from ambot.journal.query import JournalQuery
from ambot.types import ClientId


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    @contextmanager
    def _factory():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # Create test client
    with _factory() as session:
        session.add(Client(id="c1", name="Test", email="t@t.com", tier="t1"))

    return _factory


@pytest.fixture
def service(session_factory):
    cfg = CommissionConfig(monthly_aum_fee_pct=0.01, performance_fee_pct=0.20)
    calculator = HybridCommissionCalculator(cfg)
    hwm_tracker = HighWatermarkTracker(session_factory)
    journal_query = MagicMock(spec=JournalQuery)
    journal_query.get_net_deposits.return_value = Decimal("0")

    pdf_gen = MagicMock()
    pdf_gen.generate = AsyncMock(return_value=None)

    return MonthlySnapshotService(
        session_factory=session_factory,
        calculator=calculator,
        hwm_tracker=hwm_tracker,
        journal_query=journal_query,
        pdf_generator=pdf_gen,
    )


@pytest.fixture
def mock_order_router():
    """Mock router that returns a fixed balance."""
    router = MagicMock()
    from ambot.broker.client import AccountBalance
    router._client.get_account_balance = AsyncMock(return_value=AccountBalance(
        total_usdt=Decimal("12500"),
        available_usdt=Decimal("12500"),
        timestamp=datetime.now(timezone.utc),
    ))
    return router


@pytest.mark.asyncio
async def test_commission_calculated_and_persisted(service, session_factory, mock_order_router):
    result = await service.run_for_client(
        client_id=ClientId("c1"),
        order_router=mock_order_router,
        period_start_balance=Decimal("10000"),
    )

    # Verify commission result
    assert result.monthly_fee == Decimal("100.00")
    assert result.ending_balance == Decimal("12500")
    assert result.performance == Decimal("2500")
    assert result.performance_fee == Decimal("500.00")
    assert result.total_commission == Decimal("600.00")

    # Verify snapshot was persisted (read inside session to avoid DetachedInstanceError)
    with session_factory() as session:
        snapshots = session.query(CommissionSnapshot).filter_by(client_id="c1").all()
        assert len(snapshots) == 1
        total = float(snapshots[0].total_commission)
    assert total == pytest.approx(600.00)


@pytest.mark.asyncio
async def test_duplicate_snapshot_raises(service, mock_order_router):
    """Running the sweep twice for the same period should raise."""
    await service.run_for_client(
        client_id=ClientId("c1"),
        order_router=mock_order_router,
        period_start_balance=Decimal("10000"),
    )
    with pytest.raises(SnapshotAlreadyExists):
        await service.run_for_client(
            client_id=ClientId("c1"),
            order_router=mock_order_router,
            period_start_balance=Decimal("10000"),
        )


@pytest.mark.asyncio
async def test_hwm_updated_after_sweep(service, session_factory, mock_order_router):
    hwm_tracker = HighWatermarkTracker(session_factory)

    await service.run_for_client(
        client_id=ClientId("c1"),
        order_router=mock_order_router,
        period_start_balance=Decimal("10000"),
    )

    # HWM should now be 12500 (ending_balance - net_deposits = 12500 - 0)
    assert hwm_tracker.get(ClientId("c1")) == Decimal("12500")
