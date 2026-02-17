"""All custom exceptions for the ambot execution engine."""
from __future__ import annotations


# ─── Base ─────────────────────────────────────────────────────────────────────
class AmbotError(Exception):
    """Base exception for all ambot errors."""


# ─── Vault ────────────────────────────────────────────────────────────────────
class VaultError(AmbotError):
    """Raised when key vault operations fail."""


class KeyDecryptionError(VaultError):
    """Raised when decryption of an API key fails (tampered data or wrong master key)."""


class KeyNotFoundError(VaultError):
    """Raised when no encrypted key record exists for a client."""


# ─── Risk ─────────────────────────────────────────────────────────────────────
class RiskError(AmbotError):
    """Base risk exception."""


class KillSwitchTriggered(RiskError):
    """Raised when the global kill switch is active and execution is attempted."""

    def __init__(self, reason: str = "Kill switch is active") -> None:
        self.reason = reason
        super().__init__(reason)


class DailyLossLimitExceeded(RiskError):
    """Raised when a client has hit their daily loss limit."""


# ─── Broker / Order ───────────────────────────────────────────────────────────
class BrokerError(AmbotError):
    """Base exception for broker/exchange interactions."""


class RateLimitExceeded(BrokerError):
    """Raised when per-client rate limit is hit."""


class BrokerTemporaryError(BrokerError):
    """Transient errors that can be retried (network timeouts, 5xx responses)."""


class BrokerPermanentError(BrokerError):
    """Permanent errors that must not be retried (invalid symbol, insufficient balance)."""


class OrderRejected(BrokerError):
    """The exchange rejected the order."""

    def __init__(self, reason: str, exchange_code: str | None = None) -> None:
        self.reason = reason
        self.exchange_code = exchange_code
        super().__init__(f"Order rejected: {reason} (code={exchange_code})")


# ─── Engine ───────────────────────────────────────────────────────────────────
class EngineError(AmbotError):
    """Base engine exception."""


class ClientNotFound(EngineError):
    """Raised when a ClientId does not exist in the engine."""


class DuplicateTradeProtection(EngineError):
    """Raised when a signal would result in a duplicate trade."""


# ─── Reconciliation ───────────────────────────────────────────────────────────
class ReconciliationError(AmbotError):
    """Base reconciliation exception."""


class PositionMismatch(ReconciliationError):
    """Raised when broker positions diverge beyond tolerance from internal state."""

    def __init__(self, client_id: str, symbol: str, mismatch_pct: float) -> None:
        self.client_id = client_id
        self.symbol = symbol
        self.mismatch_pct = mismatch_pct
        super().__init__(
            f"Position mismatch for {client_id}/{symbol}: {mismatch_pct:.2%}"
        )


# ─── Commission ───────────────────────────────────────────────────────────────
class CommissionError(AmbotError):
    """Base commission exception."""


class SnapshotAlreadyExists(CommissionError):
    """Raised when a monthly snapshot has already been recorded for this period."""
