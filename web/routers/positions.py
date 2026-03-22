"""Live position and performance dashboard router."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from decimal import Decimal

from ambot.journal.query import JournalQuery
from web.dependencies import DBSession, CurrentClient, _get_session_factory

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class PositionResponse(BaseModel):
    symbol: str
    side: str
    quantity: float
    entry_price: float
    current_price: float | None
    unrealized_pnl: float


class TradeResponse(BaseModel):
    id: str
    symbol: str
    side: str
    quantity: float
    filled_price: float | None
    status: str
    strategy_name: str | None
    created_at: str


class DashboardResponse(BaseModel):
    client_id: str
    tier: str
    open_positions: list[PositionResponse]
    recent_trades: list[TradeResponse]


@router.get("/", response_model=DashboardResponse)
def get_dashboard(current: CurrentClient, db: DBSession) -> DashboardResponse:
    """Return live positions and recent trades for the authenticated client."""
    query = JournalQuery(_get_session_factory())

    open_positions = query.get_open_positions(current.id)
    recent_trades = query.get_trades(current.id, limit=50)

    return DashboardResponse(
        client_id=current.id,
        tier=current.tier,
        open_positions=[
            PositionResponse(
                symbol=p.symbol,
                side=p.side,
                quantity=float(p.quantity),
                entry_price=float(p.entry_price),
                current_price=float(p.current_price) if p.current_price else None,
                unrealized_pnl=float(p.unrealized_pnl or 0),
            )
            for p in open_positions
        ],
        recent_trades=[
            TradeResponse(
                id=t.id,
                symbol=t.symbol,
                side=t.side,
                quantity=float(t.quantity),
                filled_price=float(t.filled_price) if t.filled_price else None,
                status=t.status,
                strategy_name=t.strategy_name,
                created_at=t.created_at.isoformat() if t.created_at else "",
            )
            for t in recent_trades
        ],
    )
