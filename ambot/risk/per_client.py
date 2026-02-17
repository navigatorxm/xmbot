"""
Per-client risk guard.
Evaluates all risk checks in order and returns the first non-ALLOW result.
"""
from __future__ import annotations

from ambot.risk.checks import (
    ClientRiskConfig,
    ClientState,
    RiskDecisionResult,
    check_daily_loss,
    check_leverage,
    check_open_positions,
    check_per_trade_risk,
    check_symbol_allocation,
)
from ambot.strategies.signals import Signal
from ambot.types import RiskDecision


class PerClientRiskGuard:
    """
    Stateless risk evaluator for a single client.
    All checks are composable and independently testable.
    """

    def __init__(self, config: ClientRiskConfig) -> None:
        self.cfg = config

    def evaluate(self, signal: Signal, state: ClientState) -> RiskDecisionResult:
        """
        Run all risk checks in priority order.

        Returns the first BLOCK or REDUCE result encountered.
        Returns ALLOW only if every check passes.

        Priority (highest first):
        1. Daily loss limit  — hard block, no workaround
        2. Open positions    — hard block
        3. Leverage          — soft reduce
        4. Per-trade risk    — soft reduce
        5. Symbol allocation — soft reduce

        When multiple REDUCE checks would apply, the most restrictive
        (smallest adjusted_size) takes precedence.
        """
        checks = [
            check_daily_loss(signal, state, self.cfg),
            check_open_positions(signal, state, self.cfg),
            check_leverage(signal, state, self.cfg),
            check_per_trade_risk(signal, state, self.cfg),
            check_symbol_allocation(signal, state, self.cfg),
        ]

        # Return first hard block immediately
        for result in checks:
            if result.action == RiskDecision.BLOCK:
                return result

        # Collect all reductions and pick the most restrictive
        reductions = [r for r in checks if r.action == RiskDecision.REDUCE]
        if reductions:
            most_restrictive = min(reductions, key=lambda r: r.adjusted_size or 0)
            return most_restrictive

        return RiskDecisionResult.allow()
