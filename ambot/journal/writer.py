"""
JournalWriter — async-safe trade and ledger recorder.

Uses a single-writer coroutine pattern backed by an asyncio.Queue.
All call sites enqueue entries non-blocking; the writer loop drains
the queue to the DB serially, preventing write contention on SQLite
in T1 multi-tenant mode.

In production with PostgreSQL, connection pooling handles concurrency
automatically, but the queue pattern is still useful for buffering.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

from ambot.broker.order_router import FilledOrder
from ambot.strategies.signals import Signal
from ambot.types import ClientId, LedgerEntryType

log = logging.getLogger("ambot.journal")


@dataclass
class TradeEntry:
    client_id: ClientId
    signal_id: str | None
    symbol: str
    side: str
    action: str
    order_type: str
    quantity: Decimal
    filled_price: Decimal | None
    stop_loss: Decimal | None
    take_profit: Decimal | None
    leverage: Decimal
    exchange_order_id: str | None
    status: str
    commission_paid: Decimal
    strategy_name: str
    strategy_version: str
    filled_at: datetime | None

    @classmethod
    def from_fill(cls, order: FilledOrder) -> TradeEntry:
        return cls(
            client_id=order.client_id,
            signal_id=order.signal_id,
            symbol=order.symbol,
            side=order.side,
            action=order.action,
            order_type="market",
            quantity=order.quantity,
            filled_price=order.filled_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            leverage=order.leverage,
            exchange_order_id=order.exchange_order_id,
            status=order.status.value,
            commission_paid=order.commission,
            strategy_name=order.strategy_name,
            strategy_version=order.strategy_version,
            filled_at=order.timestamp,
        )


@dataclass
class LedgerRecord:
    client_id: ClientId
    entry_type: LedgerEntryType
    amount: Decimal
    transaction_id: str
    recorded_at: datetime


class JournalWriter:
    """
    Non-blocking journal writer backed by an asyncio.Queue.
    Callers enqueue; the internal loop writes to the DB.
    """

    def __init__(self, session_factory: Callable) -> None:
        self._session_factory = session_factory
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start the background writer loop."""
        self._running = True
        self._task = asyncio.create_task(self._writer_loop(), name="journal_writer")
        log.info("JournalWriter started")

    async def stop(self) -> None:
        """Drain the queue and stop the writer loop."""
        self._running = False
        await self._queue.join()  # Wait for all queued items to be processed
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("JournalWriter stopped")

    async def write_trade(self, order: FilledOrder) -> None:
        """Enqueue a trade for async DB write. Non-blocking."""
        entry = TradeEntry.from_fill(order)
        await self._queue.put(("trade", entry))

    async def write_deposit(
        self,
        client_id: ClientId,
        amount: Decimal,
        transaction_id: str,
    ) -> None:
        """Enqueue a deposit ledger entry."""
        record = LedgerRecord(
            client_id=client_id,
            entry_type=LedgerEntryType.DEPOSIT,
            amount=amount,
            transaction_id=transaction_id,
            recorded_at=datetime.now(timezone.utc),
        )
        await self._queue.put(("ledger", record))

    async def write_withdrawal(
        self,
        client_id: ClientId,
        amount: Decimal,
        transaction_id: str,
    ) -> None:
        """Enqueue a withdrawal ledger entry."""
        record = LedgerRecord(
            client_id=client_id,
            entry_type=LedgerEntryType.WITHDRAWAL,
            amount=amount,
            transaction_id=transaction_id,
            recorded_at=datetime.now(timezone.utc),
        )
        await self._queue.put(("ledger", record))

    # ── Private ───────────────────────────────────────────────────────────────

    async def _writer_loop(self) -> None:
        """Drain the queue and persist items to the DB."""
        while self._running or not self._queue.empty():
            try:
                item_type, entry = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
                try:
                    if item_type == "trade":
                        await asyncio.get_event_loop().run_in_executor(
                            None, self._persist_trade, entry
                        )
                    elif item_type == "ledger":
                        await asyncio.get_event_loop().run_in_executor(
                            None, self._persist_ledger, entry
                        )
                except Exception as exc:
                    log.error("Journal write failed (%s): %s", item_type, exc)
                finally:
                    self._queue.task_done()
            except asyncio.TimeoutError:
                continue

    def _persist_trade(self, entry: TradeEntry) -> None:
        from ambot.core.persistence import Trade

        with self._session_factory() as session:
            trade = Trade(
                client_id=entry.client_id,
                signal_id=entry.signal_id,
                symbol=entry.symbol,
                side=entry.side,
                action=entry.action,
                order_type=entry.order_type,
                quantity=entry.quantity,
                filled_price=entry.filled_price,
                stop_loss=entry.stop_loss,
                take_profit=entry.take_profit,
                leverage=entry.leverage,
                exchange_order_id=entry.exchange_order_id,
                status=entry.status,
                commission_paid=entry.commission_paid,
                strategy_name=entry.strategy_name,
                strategy_version=entry.strategy_version,
                filled_at=entry.filled_at,
            )
            session.add(trade)
            session.commit()

    def _persist_ledger(self, record: LedgerRecord) -> None:
        from ambot.core.persistence import LedgerEntry

        with self._session_factory() as session:
            entry = LedgerEntry(
                client_id=record.client_id,
                entry_type=record.entry_type.value,
                amount=record.amount,
                transaction_id=record.transaction_id,
                recorded_at=record.recorded_at,
            )
            session.add(entry)
            session.commit()
