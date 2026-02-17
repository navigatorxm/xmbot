"""
OrderRouter — translates Signals into exchange orders with retry logic.

Responsibilities:
- Enforce per-client rate limits before submission
- Map Signal → BinanceClient.create_order() parameters
- Retry on transient errors (up to MAX_RETRIES)
- Return a FilledOrder for journalling
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from ambot.broker.client import BinanceClient, ExchangeOrder
from ambot.broker.rate_limiter import PerClientRateLimiter
from ambot.exceptions import BrokerPermanentError, BrokerTemporaryError, RateLimitExceeded
from ambot.strategies.signals import Signal
from ambot.types import ClientId, OrderStatus

log = logging.getLogger("ambot.broker.router")

# Errors ccxt raises that are worth retrying
_RETRYABLE_PATTERNS = (
    "NetworkError",
    "RequestTimeout",
    "ExchangeNotAvailable",
    "DDoSProtection",
)


def _is_retryable(exc: Exception) -> bool:
    return any(pattern in type(exc).__name__ for pattern in _RETRYABLE_PATTERNS)


@dataclass
class FilledOrder:
    """Normalised representation of a completed (or attempted) order."""
    order_id: str
    client_id: ClientId
    signal_id: str
    symbol: str
    side: str
    action: str
    quantity: Decimal
    filled_quantity: Decimal
    filled_price: Decimal | None
    stop_loss: Decimal | None
    take_profit: Decimal | None
    leverage: Decimal
    status: OrderStatus
    commission: Decimal
    strategy_name: str
    strategy_version: str
    exchange_order_id: str | None
    timestamp: datetime


class OrderRouter:
    """
    Routes signals to Binance with rate limiting and retry logic.
    One instance per client (holds the client's BinanceClient).
    """

    MAX_RETRIES = 3
    BASE_RETRY_DELAY = 1.0  # seconds (doubles each retry)

    def __init__(
        self,
        binance_client: BinanceClient,
        rate_limiter: PerClientRateLimiter,
        client_id: ClientId,
    ) -> None:
        self._client = binance_client
        self._limiter = rate_limiter
        self._client_id = client_id

    async def submit(self, signal: Signal) -> FilledOrder:
        """
        Submit a signal as an order to Binance.

        Raises
        ------
        RateLimitExceeded   If the per-client bucket is depleted.
        BrokerPermanentError  For non-retryable exchange errors.
        BrokerTemporaryError  If all retries are exhausted.
        """
        if not self._limiter.allow(self._client_id):
            raise RateLimitExceeded(
                f"Rate limit hit for client {self._client_id} on {signal.symbol}"
            )

        last_exc: Exception | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                raw = await self._client.create_order(
                    symbol=signal.symbol,
                    side=signal.side.value,
                    order_type=signal.order_type.value,
                    quantity=signal.quantity,
                    price=signal.entry_price,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    leverage=signal.leverage,
                )
                return self._to_filled(raw, signal)

            except Exception as exc:
                if not _is_retryable(exc):
                    log.error(
                        "Permanent broker error for client=%s signal=%s: %s",
                        self._client_id, signal.id, exc,
                    )
                    raise BrokerPermanentError(str(exc)) from exc

                last_exc = exc
                delay = self.BASE_RETRY_DELAY * (2 ** attempt)
                log.warning(
                    "Transient broker error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, self.MAX_RETRIES, delay, exc,
                )
                await asyncio.sleep(delay)

        raise BrokerTemporaryError(
            f"All {self.MAX_RETRIES} retries exhausted for client={self._client_id}: {last_exc}"
        )

    async def cancel_all_open_orders(self) -> None:
        """Cancel all open orders for this client. Called during graceful shutdown."""
        try:
            await self._client.cancel_all_orders()
            log.info("Cancelled all open orders for client=%s", self._client_id)
        except Exception as exc:
            log.error("Failed to cancel open orders for client=%s: %s", self._client_id, exc)

    def _to_filled(self, raw: ExchangeOrder, signal: Signal) -> FilledOrder:
        return FilledOrder(
            order_id=str(id(raw)),  # Internal tracking ID
            client_id=self._client_id,
            signal_id=signal.id,
            symbol=signal.symbol,
            side=signal.side.value,
            action=signal.action.value,
            quantity=signal.quantity,
            filled_quantity=raw.filled_quantity,
            filled_price=raw.average_fill_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            leverage=signal.leverage,
            status=OrderStatus.FILLED if raw.filled_quantity > 0 else OrderStatus.PENDING,
            commission=raw.commission,
            strategy_name=signal.strategy_name,
            strategy_version=signal.strategy_version,
            exchange_order_id=raw.order_id,
            timestamp=datetime.now(timezone.utc),
        )
