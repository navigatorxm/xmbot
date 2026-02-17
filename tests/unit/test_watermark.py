"""Unit tests for HighWatermarkTracker."""
from __future__ import annotations

from decimal import Decimal

import pytest

from ambot.commissions.watermark import HighWatermarkTracker
from ambot.core.persistence import Base, Client
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager


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

    # Create a test client
    with _factory() as session:
        client = Client(id="client-hwm-test", name="Test", email="hwm@test.com", tier="t1")
        session.add(client)

    return _factory


class TestHighWatermarkTracker:
    def test_get_returns_zero_for_new_client(self, session_factory):
        tracker = HighWatermarkTracker(session_factory)
        hwm = tracker.get("client-hwm-test")
        assert hwm == Decimal("0")

    def test_initialise_sets_hwm(self, session_factory):
        tracker = HighWatermarkTracker(session_factory)
        tracker.initialise("client-hwm-test", Decimal("10000"))
        assert tracker.get("client-hwm-test") == Decimal("10000")

    def test_update_increases_hwm(self, session_factory):
        tracker = HighWatermarkTracker(session_factory)
        tracker.initialise("client-hwm-test", Decimal("10000"))
        tracker.update("client-hwm-test", Decimal("12000"))
        assert tracker.get("client-hwm-test") == Decimal("12000")

    def test_update_does_not_decrease_hwm(self, session_factory):
        tracker = HighWatermarkTracker(session_factory)
        tracker.initialise("client-hwm-test", Decimal("10000"))
        tracker.update("client-hwm-test", Decimal("12000"))
        # Attempt to lower it — should be rejected
        tracker.update("client-hwm-test", Decimal("9000"))
        assert tracker.get("client-hwm-test") == Decimal("12000")

    def test_initialise_is_idempotent(self, session_factory):
        tracker = HighWatermarkTracker(session_factory)
        tracker.initialise("client-hwm-test", Decimal("10000"))
        tracker.initialise("client-hwm-test", Decimal("5000"))  # Second call — should not overwrite
        assert tracker.get("client-hwm-test") == Decimal("10000")
