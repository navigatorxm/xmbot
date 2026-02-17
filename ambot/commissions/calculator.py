"""
HybridCommissionCalculator

Implements the hybrid fee structure:
  monthly_fee      = starting_balance × 0.01   (1% AUM fee)
  adjusted_ending  = ending_balance − net_deposits
  performance      = max(0, adjusted_ending − high_watermark)
  performance_fee  = performance × 0.20         (20% of profits above HWM)
  total            = monthly_fee + performance_fee
  new_hwm          = max(old_hwm, adjusted_ending)

Key properties:
  - Net deposits are excluded from performance calculation (depositing money
    doesn't count as "profit")
  - The high watermark only moves up, never down — clients pay performance fees
    only on new equity highs
  - All arithmetic uses decimal.Decimal to prevent floating-point errors
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from ambot.config import CommissionConfig


@dataclass(frozen=True)
class CommissionResult:
    """Immutable result of one commission calculation."""
    client_id: str
    period_start: date
    period_end: date
    starting_balance: Decimal
    ending_balance: Decimal
    net_deposits: Decimal
    high_watermark_before: Decimal
    high_watermark_after: Decimal
    monthly_fee: Decimal
    performance: Decimal            # Amount above HWM (profit subject to perf fee)
    performance_fee: Decimal
    total_commission: Decimal

    def summary(self) -> str:
        return (
            f"Client={self.client_id} Period={self.period_start}→{self.period_end} | "
            f"Start=${self.starting_balance:,.2f} End=${self.ending_balance:,.2f} "
            f"NetDep=${self.net_deposits:,.2f} HWM={self.high_watermark_before:,.2f}→{self.high_watermark_after:,.2f} | "
            f"MonthlyFee=${self.monthly_fee:,.2f} PerfFee=${self.performance_fee:,.2f} "
            f"Total=${self.total_commission:,.2f}"
        )


class HybridCommissionCalculator:
    """
    Stateless commission calculator.
    Accepts a CommissionConfig and produces CommissionResult objects.
    """

    def __init__(self, cfg: CommissionConfig) -> None:
        self.monthly_fee_pct = Decimal(str(cfg.monthly_aum_fee_pct))
        self.performance_fee_pct = Decimal(str(cfg.performance_fee_pct))
        self._precision = Decimal("0.01")

    def calculate(
        self,
        client_id: str,
        period_start: date,
        period_end: date,
        starting_balance: Decimal,
        ending_balance: Decimal,
        net_deposits: Decimal,
        high_watermark: Decimal,
    ) -> CommissionResult:
        """
        Calculate commissions for one client over one period.

        Parameters
        ----------
        client_id:        Client identifier.
        period_start:     First day of the period (inclusive).
        period_end:       Last day of the period (inclusive).
        starting_balance: Equity at the start of the period (before any trades this month).
        ending_balance:   Equity at the end of the period.
        net_deposits:     deposits − withdrawals during the period.
                          Positive = client deposited. Negative = client withdrew.
        high_watermark:   Previous HWM — the highest equity ever achieved (adjusted for flows).
        """
        # 1% monthly AUM fee on starting balance
        monthly_fee = (starting_balance * self.monthly_fee_pct).quantize(
            self._precision, rounding=ROUND_HALF_UP
        )

        # Remove net deposits to get organic performance
        adjusted_ending = ending_balance - net_deposits

        # Performance is only the portion above the HWM
        performance = max(Decimal("0"), adjusted_ending - high_watermark)

        # 20% of profits above HWM
        performance_fee = (performance * self.performance_fee_pct).quantize(
            self._precision, rounding=ROUND_HALF_UP
        )

        total = monthly_fee + performance_fee

        # HWM only moves up
        new_hwm = max(high_watermark, adjusted_ending)

        return CommissionResult(
            client_id=client_id,
            period_start=period_start,
            period_end=period_end,
            starting_balance=starting_balance,
            ending_balance=ending_balance,
            net_deposits=net_deposits,
            high_watermark_before=high_watermark,
            high_watermark_after=new_hwm,
            monthly_fee=monthly_fee,
            performance=performance,
            performance_fee=performance_fee,
            total_commission=total,
        )
