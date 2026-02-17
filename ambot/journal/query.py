"""Read-side journal helpers for commissions, dashboards, and reconciliation."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Callable

from ambot.types import ClientId


class JournalQuery:
    """Read-only query helpers against the journal DB."""

    def __init__(self, session_factory: Callable) -> None:
        self._session_factory = session_factory

    def get_net_deposits(
        self,
        client_id: ClientId,
        period_start: datetime,
        period_end: datetime,
    ) -> Decimal:
        """
        Sum deposits minus withdrawals for a client over a period.
        Used in commission calculation to adjust for capital movements.
        """
        from ambot.core.persistence import LedgerEntry

        with self._session_factory() as session:
            entries = (
                session.query(LedgerEntry)
                .filter(
                    LedgerEntry.client_id == client_id,
                    LedgerEntry.recorded_at >= period_start,
                    LedgerEntry.recorded_at <= period_end,
                )
                .all()
            )

        net = Decimal("0")
        for e in entries:
            if e.entry_type == "deposit":
                net += Decimal(str(e.amount))
            else:
                net -= Decimal(str(e.amount))
        return net

    def get_trades(
        self,
        client_id: ClientId,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        limit: int = 100,
    ) -> list:
        """Fetch trade records for a client, optionally filtered by period."""
        from ambot.core.persistence import Trade

        with self._session_factory() as session:
            q = session.query(Trade).filter(Trade.client_id == client_id)
            if period_start:
                q = q.filter(Trade.created_at >= period_start)
            if period_end:
                q = q.filter(Trade.created_at <= period_end)
            return q.order_by(Trade.created_at.desc()).limit(limit).all()

    def get_open_positions(self, client_id: ClientId) -> list:
        """Fetch currently open positions for a client from the DB."""
        from ambot.core.persistence import Position

        with self._session_factory() as session:
            return (
                session.query(Position)
                .filter(Position.client_id == client_id, Position.is_open == True)  # noqa: E712
                .all()
            )
