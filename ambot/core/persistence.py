"""
SQLAlchemy ORM models and session factory.
All monetary columns use Numeric(20, 8) for precision.
All primary keys are UUID strings.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Generator

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from ambot.config import get_config


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


# ─── Client ───────────────────────────────────────────────────────────────────
class Client(Base):
    __tablename__ = "clients"

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    tier = Column(String(4), nullable=False)          # "t1" | "t2" | "t3"
    is_active = Column(Boolean, default=True, nullable=False)
    reference_equity = Column(Numeric(20, 8), default=10000)  # Base size for signal scaling
    created_at = Column(DateTime(timezone=True), default=_now)

    # Per-client risk config (overrides global defaults when not null)
    max_daily_loss_pct = Column(Numeric(7, 4), nullable=True)
    max_open_positions = Column(Integer, nullable=True)
    max_leverage = Column(Numeric(5, 2), nullable=True)
    max_per_trade_risk_pct = Column(Numeric(7, 4), nullable=True)
    max_symbol_allocation_pct = Column(Numeric(5, 4), nullable=True)

    # Relationships
    encrypted_keys = relationship("EncryptedKeyRecord", back_populates="client", uselist=False)
    trades = relationship("Trade", back_populates="client")
    positions = relationship("Position", back_populates="client")
    commission_snapshots = relationship("CommissionSnapshot", back_populates="client")
    hwm = relationship("ClientHWM", back_populates="client", uselist=False)
    ledger_entries = relationship("LedgerEntry", back_populates="client")


# ─── Encrypted Key Record ─────────────────────────────────────────────────────
class EncryptedKeyRecord(Base):
    __tablename__ = "encrypted_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(36), ForeignKey("clients.id"), unique=True, nullable=False)
    encrypted_api_key = Column(Text, nullable=False)    # base64(nonce || ciphertext)
    encrypted_api_secret = Column(Text, nullable=False)
    key_label = Column(String(100), nullable=True)      # Human-readable label only, never key value
    allowed_ips = Column(Text, nullable=True)            # JSON array of allowed IPs
    created_at = Column(DateTime(timezone=True), default=_now)
    rotated_at = Column(DateTime(timezone=True), nullable=True)

    client = relationship("Client", back_populates="encrypted_keys")


# ─── Trade ────────────────────────────────────────────────────────────────────
class Trade(Base):
    __tablename__ = "trades"

    id = Column(String(36), primary_key=True, default=_uuid)
    client_id = Column(String(36), ForeignKey("clients.id"), nullable=False, index=True)
    signal_id = Column(String(36), nullable=True, index=True)
    symbol = Column(String(20), nullable=False)
    side = Column(String(4), nullable=False)            # "buy" | "sell"
    action = Column(String(6), nullable=False)          # "open" | "close" | "scale"
    order_type = Column(String(8), nullable=False)      # "market" | "limit" | "stop"
    quantity = Column(Numeric(20, 8), nullable=False)
    entry_price = Column(Numeric(20, 8), nullable=True)
    filled_price = Column(Numeric(20, 8), nullable=True)
    stop_loss = Column(Numeric(20, 8), nullable=True)
    take_profit = Column(Numeric(20, 8), nullable=True)
    leverage = Column(Numeric(5, 2), default=1.0)
    exchange_order_id = Column(String(50), nullable=True)
    status = Column(String(12), default="pending")
    commission_paid = Column(Numeric(20, 8), default=0)
    strategy_name = Column(String(100), nullable=True)
    strategy_version = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    filled_at = Column(DateTime(timezone=True), nullable=True)

    client = relationship("Client", back_populates="trades")


# ─── Position ─────────────────────────────────────────────────────────────────
class Position(Base):
    __tablename__ = "positions"

    id = Column(String(36), primary_key=True, default=_uuid)
    client_id = Column(String(36), ForeignKey("clients.id"), nullable=False, index=True)
    symbol = Column(String(20), nullable=False)
    side = Column(String(5), nullable=False)            # "long" | "short"
    quantity = Column(Numeric(20, 8), nullable=False)
    entry_price = Column(Numeric(20, 8), nullable=False)
    current_price = Column(Numeric(20, 8), nullable=True)
    unrealized_pnl = Column(Numeric(20, 8), default=0)
    leverage = Column(Numeric(5, 2), default=1.0)
    is_open = Column(Boolean, default=True)
    opened_at = Column(DateTime(timezone=True), default=_now)
    closed_at = Column(DateTime(timezone=True), nullable=True)

    client = relationship("Client", back_populates="positions")


# ─── Commission Snapshot (immutable monthly record) ───────────────────────────
class CommissionSnapshot(Base):
    __tablename__ = "commission_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(36), ForeignKey("clients.id"), nullable=False, index=True)
    period_start = Column(DateTime(timezone=True), nullable=False)
    period_end = Column(DateTime(timezone=True), nullable=False)
    starting_balance = Column(Numeric(20, 8), nullable=False)
    ending_balance = Column(Numeric(20, 8), nullable=False)
    net_deposits = Column(Numeric(20, 8), default=0)
    high_watermark_before = Column(Numeric(20, 8), nullable=False)
    high_watermark_after = Column(Numeric(20, 8), nullable=False)
    monthly_fee = Column(Numeric(20, 8), nullable=False)
    performance = Column(Numeric(20, 8), nullable=False)
    performance_fee = Column(Numeric(20, 8), nullable=False)
    total_commission = Column(Numeric(20, 8), nullable=False)
    pdf_path = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    client = relationship("Client", back_populates="commission_snapshots")


# ─── High Watermark ───────────────────────────────────────────────────────────
class ClientHWM(Base):
    __tablename__ = "client_hwm"

    client_id = Column(String(36), ForeignKey("clients.id"), primary_key=True)
    hwm = Column(Numeric(20, 8), nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    client = relationship("Client", back_populates="hwm")


# ─── Ledger (deposits / withdrawals) ──────────────────────────────────────────
class LedgerEntry(Base):
    __tablename__ = "ledger"

    id = Column(String(36), primary_key=True, default=_uuid)
    client_id = Column(String(36), ForeignKey("clients.id"), nullable=False, index=True)
    entry_type = Column(String(12), nullable=False)     # "deposit" | "withdrawal"
    amount = Column(Numeric(20, 8), nullable=False)
    transaction_id = Column(String(100), unique=True, nullable=False)
    recorded_at = Column(DateTime(timezone=True), default=_now)

    client = relationship("Client", back_populates="ledger_entries")


# ─── Session Factory ──────────────────────────────────────────────────────────
def make_engine(db_url: str | None = None):
    url = db_url or get_config().db_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, echo=False)


def make_session_factory(db_url: str | None = None) -> sessionmaker:
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_db_session(session_factory: sessionmaker) -> Generator[Session, None, None]:
    """Dependency-injection compatible session generator."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
