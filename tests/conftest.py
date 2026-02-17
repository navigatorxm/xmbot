"""Shared pytest fixtures."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager

from ambot.core.persistence import Base
from ambot.config import AppConfig, CommissionConfig, RiskConfig


# ── In-memory SQLite for tests ─────────────────────────────────────────────────

@pytest.fixture(scope="session")
def test_engine():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session_factory(test_engine):
    factory = sessionmaker(bind=test_engine)

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

    return _factory


@pytest.fixture
def test_config():
    return AppConfig(
        env="test",
        db_url="sqlite:///:memory:",
        vault_master_key_hex="a" * 64,   # 32 bytes, valid for tests
        jwt_secret_key="test_secret_key_for_jwt",
        risk=RiskConfig(
            default_max_daily_loss_pct=0.02,
            default_max_open_positions=5,
            default_max_leverage=3.0,
            default_max_per_trade_risk_pct=0.01,
            default_max_symbol_allocation_pct=0.20,
            reconciliation_mismatch_pct=0.02,
        ),
        commissions=CommissionConfig(
            monthly_aum_fee_pct=0.01,
            performance_fee_pct=0.20,
        ),
    )
