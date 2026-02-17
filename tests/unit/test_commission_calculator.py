"""Unit tests for hybrid commission calculator and high watermark logic."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ambot.commissions.calculator import HybridCommissionCalculator
from ambot.config import CommissionConfig


@pytest.fixture
def calc():
    return HybridCommissionCalculator(CommissionConfig(
        monthly_aum_fee_pct=0.01,
        performance_fee_pct=0.20,
    ))


def make_result(calc, **kwargs):
    defaults = dict(
        client_id="c1",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        starting_balance=Decimal("10000"),
        ending_balance=Decimal("10000"),
        net_deposits=Decimal("0"),
        high_watermark=Decimal("10000"),
    )
    defaults.update(kwargs)
    return calc.calculate(**defaults)


class TestHybridCommissionCalculator:
    def test_monthly_fee_is_one_percent_of_starting_balance(self, calc):
        result = make_result(calc, starting_balance=Decimal("10000"))
        assert result.monthly_fee == Decimal("100.00")

    def test_no_performance_fee_when_hwm_not_exceeded(self, calc):
        """Ending below HWM → no performance fee."""
        result = make_result(
            calc,
            starting_balance=Decimal("10000"),
            ending_balance=Decimal("10500"),
            net_deposits=Decimal("0"),
            high_watermark=Decimal("11000"),
        )
        assert result.performance_fee == Decimal("0")
        assert result.performance == Decimal("0")

    def test_performance_fee_charged_above_hwm(self, calc):
        result = make_result(
            calc,
            starting_balance=Decimal("10000"),
            ending_balance=Decimal("12500"),
            net_deposits=Decimal("0"),
            high_watermark=Decimal("11000"),
        )
        assert result.performance == Decimal("1500")
        assert result.performance_fee == Decimal("300.00")  # 1500 * 0.20
        assert result.total_commission == Decimal("400.00")  # 100 + 300

    def test_hwm_increases_after_profitable_month(self, calc):
        result = make_result(
            calc,
            ending_balance=Decimal("12000"),
            net_deposits=Decimal("0"),
            high_watermark=Decimal("10000"),
        )
        assert result.high_watermark_after == Decimal("12000")

    def test_hwm_does_not_decrease_after_losing_month(self, calc):
        result = make_result(
            calc,
            starting_balance=Decimal("10000"),
            ending_balance=Decimal("9000"),
            net_deposits=Decimal("0"),
            high_watermark=Decimal("10000"),
        )
        assert result.high_watermark_after == Decimal("10000")
        assert result.performance == Decimal("0")
        assert result.performance_fee == Decimal("0")

    def test_net_deposits_excluded_from_performance(self, calc):
        """A $2,000 deposit should not count as profit."""
        result = make_result(
            calc,
            starting_balance=Decimal("10000"),
            ending_balance=Decimal("12000"),
            net_deposits=Decimal("2000"),
            high_watermark=Decimal("10000"),
        )
        # Adjusted ending = 12000 - 2000 = 10000, same as HWM → no perf fee
        assert result.performance == Decimal("0")
        assert result.performance_fee == Decimal("0")

    def test_withdrawal_increases_reported_performance(self, calc):
        """
        If a client withdrew $1,000 and ending balance is $10,000,
        adjusted ending = $11,000 — performance is real.
        """
        result = make_result(
            calc,
            starting_balance=Decimal("11000"),
            ending_balance=Decimal("10000"),
            net_deposits=Decimal("-1000"),  # Withdrawal
            high_watermark=Decimal("10000"),
        )
        # adjusted_ending = 10000 - (-1000) = 11000
        assert result.performance == Decimal("1000")

    def test_total_is_sum_of_both_fees(self, calc):
        result = make_result(
            calc,
            starting_balance=Decimal("10000"),
            ending_balance=Decimal("15000"),
            net_deposits=Decimal("0"),
            high_watermark=Decimal("10000"),
        )
        assert result.total_commission == result.monthly_fee + result.performance_fee

    def test_zero_balance_produces_zero_fees(self, calc):
        result = make_result(
            calc,
            starting_balance=Decimal("0"),
            ending_balance=Decimal("0"),
            net_deposits=Decimal("0"),
            high_watermark=Decimal("0"),
        )
        assert result.monthly_fee == Decimal("0")
        assert result.performance_fee == Decimal("0")
        assert result.total_commission == Decimal("0")

    def test_summary_string_contains_client_id(self, calc):
        result = make_result(calc, client_id="client_abc")
        assert "client_abc" in result.summary()
