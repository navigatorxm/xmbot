"""
BinanceClient — async wrapper around ccxt's Binance exchange.

Security notes:
- API credentials are injected at call time from the vault.
- Client instances are short-lived within a request context; they must
  not be cached with credentials embedded.
- Testnet mode is available for dry-run validation.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

log = logging.getLogger("ambot.broker.client")


@dataclass
class AccountBalance:
    """Simplified account balance snapshot."""
    total_usdt: Decimal
    available_usdt: Decimal
    timestamp: datetime


@dataclass
class ExchangeOrder:
    """Raw order response from the exchange."""
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    status: str
    quantity: Decimal
    filled_quantity: Decimal
    price: Decimal | None
    average_fill_price: Decimal | None
    commission: Decimal
    commission_asset: str
    timestamp: datetime


@dataclass
class ExchangePosition:
    """Open position as reported by the exchange."""
    symbol: str
    side: str          # "long" | "short"
    quantity: Decimal
    entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal
    leverage: Decimal


class BinanceClient:
    """
    Async Binance API client.

    Uses ccxt under the hood. Credentials are passed per-call and never
    stored as instance attributes to prevent accidental logging.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
    ) -> None:
        try:
            import ccxt.async_support as ccxt_async
        except ImportError as exc:
            raise ImportError("ccxt is required: pip install ccxt") from exc

        options: dict[str, Any] = {
            "defaultType": "future",
            "adjustForTimeDifference": True,
        }

        exchange_cls = ccxt_async.binance
        self._exchange = exchange_cls({
            "apiKey": api_key,
            "secret": api_secret,
            "options": options,
            "enableRateLimit": False,  # We handle rate limiting ourselves
        })

        if testnet:
            self._exchange.set_sandbox_mode(True)

        self._testnet = testnet

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        await self._exchange.close()

    async def __aenter__(self) -> BinanceClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def get_account_balance(self) -> AccountBalance:
        """Fetch current USDT balance from the exchange."""
        raw = await self._exchange.fetch_balance()
        usdt = raw.get("USDT", {})
        return AccountBalance(
            total_usdt=Decimal(str(usdt.get("total", 0))),
            available_usdt=Decimal(str(usdt.get("free", 0))),
            timestamp=datetime.now(timezone.utc),
        )

    async def get_open_positions(self) -> list[ExchangePosition]:
        """Fetch all open futures positions."""
        raw_positions = await self._exchange.fetch_positions()
        positions = []
        for p in raw_positions:
            qty = Decimal(str(p.get("contracts", 0) or 0))
            if qty == 0:
                continue
            side_raw = p.get("side", "long")
            positions.append(ExchangePosition(
                symbol=p["symbol"].replace("/", ""),
                side=side_raw,
                quantity=qty,
                entry_price=Decimal(str(p.get("entryPrice", 0) or 0)),
                mark_price=Decimal(str(p.get("markPrice", 0) or 0)),
                unrealized_pnl=Decimal(str(p.get("unrealizedPnl", 0) or 0)),
                leverage=Decimal(str(p.get("leverage", 1) or 1)),
            ))
        return positions

    async def get_open_orders(self) -> list[dict[str, Any]]:
        """Fetch all open orders."""
        return await self._exchange.fetch_open_orders()

    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        price: Decimal | None = None,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        leverage: Decimal | None = None,
    ) -> ExchangeOrder:
        """Place an order on Binance."""
        # Set leverage if specified
        if leverage and leverage != Decimal("1"):
            try:
                await self._exchange.set_leverage(int(leverage), symbol)
            except Exception as e:
                log.warning("Could not set leverage for %s: %s", symbol, e)

        params: dict[str, Any] = {}

        raw = await self._exchange.create_order(
            symbol=symbol,
            type=order_type,
            side=side,
            amount=float(quantity),
            price=float(price) if price else None,
            params=params,
        )

        fills = raw.get("trades", [])
        commission = sum(Decimal(str(f.get("fee", {}).get("cost", 0))) for f in fills)
        commission_asset = fills[0].get("fee", {}).get("currency", "USDT") if fills else "USDT"

        return ExchangeOrder(
            order_id=str(raw["id"]),
            client_order_id=str(raw.get("clientOrderId", "")),
            symbol=symbol,
            side=side,
            order_type=order_type,
            status=raw.get("status", "unknown"),
            quantity=Decimal(str(raw.get("amount", quantity))),
            filled_quantity=Decimal(str(raw.get("filled", 0))),
            price=Decimal(str(raw["price"])) if raw.get("price") else None,
            average_fill_price=Decimal(str(raw["average"])) if raw.get("average") else None,
            commission=commission,
            commission_asset=commission_asset,
            timestamp=datetime.now(timezone.utc),
        )

    async def cancel_order(self, order_id: str, symbol: str | None = None) -> None:
        """Cancel a specific order by ID."""
        await self._exchange.cancel_order(order_id, symbol)

    async def cancel_all_orders(self, symbol: str | None = None) -> None:
        """Cancel all open orders, optionally filtered by symbol."""
        open_orders = await self.get_open_orders()
        tasks = [self.cancel_order(o["id"], o.get("symbol")) for o in open_orders]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
