"""
Composable, stateless risk check functions.
Each check takes a signal + client state and returns a RiskDecisionResult.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ambot.strategies.signals import Signal
from ambot.types import RiskDecision, SignalAction


@dataclass
class ClientRiskConfig:
    """Per-client risk parameters. Overrides global defaults when set."""
    max_daily_loss_pct: float = 0.02
    max_open_positions: int = 5
    max_leverage: float = 3.0
    max_per_trade_risk_pct: float = 0.01
    max_symbol_allocation_pct: float = 0.20


@dataclass
class PositionState:
    symbol: str
    side: str          # "long" | "short"
    quantity: Decimal
    entry_price: Decimal
    unrealized_pnl: Decimal = Decimal("0")
    leverage: Decimal = Decimal("1")


@dataclass
class ClientState:
    """Live in-memory state for a single client."""
    client_id: str
    balance: Decimal
    equity: Decimal
    daily_loss_pct: Decimal = Decimal("0")
    open_positions: dict[str, PositionState] = None  # symbol → PositionState
    period_start_balance: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.open_positions is None:
            self.open_positions = {}


@dataclass
class RiskDecisionResult:
    action: RiskDecision
    reason: str = ""
    adjusted_size: Decimal | None = None

    @classmethod
    def allow(cls) -> RiskDecisionResult:
        return cls(action=RiskDecision.ALLOW)

    @classmethod
    def block(cls, reason: str) -> RiskDecisionResult:
        return cls(action=RiskDecision.BLOCK, reason=reason)

    @classmethod
    def reduce(cls, reason: str, size: Decimal) -> RiskDecisionResult:
        return cls(action=RiskDecision.REDUCE, reason=reason, adjusted_size=size)


# ─── Individual Check Functions ───────────────────────────────────────────────

def check_daily_loss(
    signal: Signal, state: ClientState, cfg: ClientRiskConfig
) -> RiskDecisionResult:
    """Block if the client has already hit their daily loss limit."""
    if state.daily_loss_pct >= Decimal(str(cfg.max_daily_loss_pct)):
        return RiskDecisionResult.block(
            f"Daily loss limit reached: {float(state.daily_loss_pct):.2%} "
            f">= {cfg.max_daily_loss_pct:.2%}"
        )
    return RiskDecisionResult.allow()


def check_open_positions(
    signal: Signal, state: ClientState, cfg: ClientRiskConfig
) -> RiskDecisionResult:
    """Block new entries if max open positions is reached."""
    if signal.action in (SignalAction.OPEN, SignalAction.SCALE):
        if len(state.open_positions) >= cfg.max_open_positions:
            return RiskDecisionResult.block(
                f"Max open positions reached: {len(state.open_positions)}/{cfg.max_open_positions}"
            )
    return RiskDecisionResult.allow()


def check_leverage(
    signal: Signal, state: ClientState, cfg: ClientRiskConfig
) -> RiskDecisionResult:
    """Reduce leverage if signal exceeds the client's max."""
    max_lev = Decimal(str(cfg.max_leverage))
    if signal.leverage > max_lev:
        ratio = max_lev / signal.leverage
        adjusted = signal.quantity * ratio
        return RiskDecisionResult.reduce(
            reason=(
                f"Leverage {float(signal.leverage):.1f}x exceeds max "
                f"{cfg.max_leverage:.1f}x"
            ),
            size=adjusted,
        )
    return RiskDecisionResult.allow()


def check_per_trade_risk(
    signal: Signal, state: ClientState, cfg: ClientRiskConfig
) -> RiskDecisionResult:
    """Reduce size if per-trade risk (entry → stop-loss) exceeds the limit."""
    if signal.stop_loss is None or signal.entry_price is None:
        return RiskDecisionResult.allow()
    if state.equity <= Decimal("0"):
        return RiskDecisionResult.block("Client equity is zero or negative")

    risk_per_unit = abs(signal.entry_price - signal.stop_loss)
    if risk_per_unit == 0:
        return RiskDecisionResult.allow()

    risk_amount = risk_per_unit * signal.quantity
    risk_pct = risk_amount / state.equity

    max_risk_pct = Decimal(str(cfg.max_per_trade_risk_pct))
    if risk_pct > max_risk_pct:
        allowed_risk_amount = state.equity * max_risk_pct
        adjusted_qty = allowed_risk_amount / risk_per_unit
        return RiskDecisionResult.reduce(
            reason=(
                f"Per-trade risk {float(risk_pct):.2%} exceeds "
                f"{cfg.max_per_trade_risk_pct:.2%}"
            ),
            size=adjusted_qty,
        )
    return RiskDecisionResult.allow()


def check_symbol_allocation(
    signal: Signal, state: ClientState, cfg: ClientRiskConfig
) -> RiskDecisionResult:
    """Reduce size if a single symbol would exceed max allocation % of equity."""
    if signal.entry_price is None or signal.entry_price <= 0:
        return RiskDecisionResult.allow()
    if state.equity <= Decimal("0"):
        return RiskDecisionResult.block("Client equity is zero or negative")

    position_value = signal.quantity * signal.entry_price
    allocation_pct = position_value / state.equity
    max_alloc = Decimal(str(cfg.max_symbol_allocation_pct))

    if allocation_pct > max_alloc:
        allowed_value = state.equity * max_alloc
        adjusted_qty = allowed_value / signal.entry_price
        return RiskDecisionResult.reduce(
            reason=(
                f"Symbol allocation {float(allocation_pct):.2%} exceeds "
                f"{cfg.max_symbol_allocation_pct:.2%}"
            ),
            size=adjusted_qty,
        )
    return RiskDecisionResult.allow()
