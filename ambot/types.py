"""
Shared enumerations, type aliases, and NewTypes used across the entire ambot package.
All monetary values use decimal.Decimal for precision.
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import NewType

# ─── NewTypes ─────────────────────────────────────────────────────────────────
ClientId = NewType("ClientId", str)   # UUID string
Symbol = NewType("Symbol", str)       # e.g. "BTCUSDT"
OrderId = NewType("OrderId", str)     # Exchange order ID


# ─── Capital Tier ─────────────────────────────────────────────────────────────
class Tier(str, Enum):
    T1 = "t1"   # $1,000 – $5,000   shared engine, multi-tenant
    T2 = "t2"   # $5,001 – $20,000  logical isolation (sub-instance)
    T3 = "t3"   # $20,001 – $50,000 process isolation (container)


TIER_RANGES: dict[Tier, tuple[Decimal, Decimal]] = {
    Tier.T1: (Decimal("1000"), Decimal("5000")),
    Tier.T2: (Decimal("5001"), Decimal("20000")),
    Tier.T3: (Decimal("20001"), Decimal("50000")),
}


def classify_tier(balance: Decimal) -> Tier:
    """Deterministically assign a client to a capital tier based on balance."""
    if balance <= Decimal("5000"):
        return Tier.T1
    elif balance <= Decimal("20000"):
        return Tier.T2
    return Tier.T3


# ─── Order / Signal ───────────────────────────────────────────────────────────
class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class SignalAction(str, Enum):
    OPEN = "open"    # Enter new position
    CLOSE = "close"  # Exit existing position
    SCALE = "scale"  # Add to existing position


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


# ─── Risk ─────────────────────────────────────────────────────────────────────
class RiskDecision(str, Enum):
    ALLOW = "allow"    # Signal passes all checks
    BLOCK = "block"    # Signal rejected entirely
    REDUCE = "reduce"  # Signal allowed with size reduction


# ─── Engine ───────────────────────────────────────────────────────────────────
class EngineStatus(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    KILLED = "killed"


# ─── Ledger ───────────────────────────────────────────────────────────────────
class LedgerEntryType(str, Enum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
