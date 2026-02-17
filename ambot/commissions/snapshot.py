"""
MonthlySnapshotService — orchestrates the month-end commission sweep.

On the 1st of each month at 00:05 UTC:
  1. Fetch ending balance from Binance
  2. Sum net deposits/withdrawals from the journal
  3. Retrieve current HWM
  4. Calculate commission via HybridCommissionCalculator
  5. Persist CommissionSnapshot (immutable monthly record)
  6. Update HWM
  7. Generate PDF statement
"""
from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, TYPE_CHECKING

from ambot.commissions.calculator import CommissionResult, HybridCommissionCalculator
from ambot.commissions.watermark import HighWatermarkTracker
from ambot.exceptions import SnapshotAlreadyExists
from ambot.journal.query import JournalQuery
from ambot.types import ClientId

if TYPE_CHECKING:
    from ambot.broker.order_router import OrderRouter
    from ambot.commissions.statement import PDFStatementGenerator

log = logging.getLogger("ambot.commissions.snapshot")


def _period_bounds() -> tuple[date, date]:
    """Return (start, end) dates for the previous calendar month."""
    today = date.today()
    first_of_this_month = today.replace(day=1)
    last_of_last_month = first_of_this_month - timedelta(days=1)
    first_of_last_month = last_of_last_month.replace(day=1)
    return first_of_last_month, last_of_last_month


class MonthlySnapshotService:
    def __init__(
        self,
        session_factory: Callable,
        calculator: HybridCommissionCalculator,
        hwm_tracker: HighWatermarkTracker,
        journal_query: JournalQuery,
        pdf_generator: "PDFStatementGenerator",
    ) -> None:
        self._session_factory = session_factory
        self._calculator = calculator
        self._hwm_tracker = hwm_tracker
        self._journal_query = journal_query
        self._pdf_gen = pdf_generator

    async def run_for_client(
        self,
        client_id: ClientId,
        order_router: "OrderRouter",
        period_start_balance: Decimal,
    ) -> CommissionResult:
        """
        Run the full month-end sweep for one client.

        Parameters
        ----------
        client_id:             The client being processed.
        order_router:          Used to fetch the ending balance from Binance.
        period_start_balance:  Balance at the start of the period (recorded at midnight on day 1).
        """
        period_start, period_end = _period_bounds()

        # Guard: prevent duplicate snapshots
        if self._snapshot_exists(client_id, period_start):
            raise SnapshotAlreadyExists(
                f"Snapshot already exists for client={client_id} period={period_start}"
            )

        # Fetch ending balance from broker
        balance = await order_router._client.get_account_balance()
        ending_balance = balance.total_usdt

        # Net deposits during the period
        period_start_dt = datetime.combine(period_start, datetime.min.time()).replace(tzinfo=timezone.utc)
        period_end_dt = datetime.combine(period_end, datetime.max.time()).replace(tzinfo=timezone.utc)
        net_deposits = self._journal_query.get_net_deposits(
            client_id, period_start_dt, period_end_dt
        )

        # Current HWM — initialise to starting_balance on first month (no prior HWM)
        hwm = self._hwm_tracker.get(client_id)
        if hwm == Decimal("0"):
            self._hwm_tracker.initialise(client_id, period_start_balance)
            hwm = period_start_balance

        # Calculate
        result = self._calculator.calculate(
            client_id=client_id,
            period_start=period_start,
            period_end=period_end,
            starting_balance=period_start_balance,
            ending_balance=ending_balance,
            net_deposits=net_deposits,
            high_watermark=hwm,
        )

        # Persist snapshot
        pdf_path = await self._pdf_gen.generate(result)
        self._persist_snapshot(result, pdf_path)

        # Update HWM
        self._hwm_tracker.update(client_id, result.high_watermark_after)

        log.info(result.summary())
        return result

    def _snapshot_exists(self, client_id: ClientId, period_start: date) -> bool:
        from ambot.core.persistence import CommissionSnapshot
        from datetime import datetime

        period_start_dt = datetime.combine(period_start, datetime.min.time()).replace(tzinfo=timezone.utc)

        with self._session_factory() as session:
            existing = (
                session.query(CommissionSnapshot)
                .filter(
                    CommissionSnapshot.client_id == client_id,
                    CommissionSnapshot.period_start == period_start_dt,
                )
                .one_or_none()
            )
        return existing is not None

    def _persist_snapshot(self, result: CommissionResult, pdf_path: str | None) -> None:
        from ambot.core.persistence import CommissionSnapshot
        from datetime import datetime

        period_start_dt = datetime.combine(result.period_start, datetime.min.time()).replace(tzinfo=timezone.utc)
        period_end_dt = datetime.combine(result.period_end, datetime.max.time()).replace(tzinfo=timezone.utc)

        with self._session_factory() as session:
            snapshot = CommissionSnapshot(
                client_id=result.client_id,
                period_start=period_start_dt,
                period_end=period_end_dt,
                starting_balance=result.starting_balance,
                ending_balance=result.ending_balance,
                net_deposits=result.net_deposits,
                high_watermark_before=result.high_watermark_before,
                high_watermark_after=result.high_watermark_after,
                monthly_fee=result.monthly_fee,
                performance=result.performance,
                performance_fee=result.performance_fee,
                total_commission=result.total_commission,
                pdf_path=pdf_path,
            )
            session.add(snapshot)
            session.commit()
