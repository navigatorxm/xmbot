"""Commission history and PDF download router."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ambot.core.persistence import CommissionSnapshot
from web.dependencies import DBSession, CurrentClient

router = APIRouter(prefix="/commissions", tags=["commissions"])


class SnapshotResponse(BaseModel):
    id: int
    period_start: str
    period_end: str
    starting_balance: float
    ending_balance: float
    net_deposits: float
    high_watermark_before: float
    high_watermark_after: float
    monthly_fee: float
    performance: float
    performance_fee: float
    total_commission: float
    has_pdf: bool


@router.get("/", response_model=list[SnapshotResponse])
def list_commission_history(current: CurrentClient, db: DBSession) -> list[SnapshotResponse]:
    """Return all commission snapshots for the authenticated client."""
    snapshots = (
        db.query(CommissionSnapshot)
        .filter(CommissionSnapshot.client_id == current.id)
        .order_by(CommissionSnapshot.period_start.desc())
        .all()
    )
    return [
        SnapshotResponse(
            id=s.id,
            period_start=s.period_start.date().isoformat(),
            period_end=s.period_end.date().isoformat(),
            starting_balance=float(s.starting_balance),
            ending_balance=float(s.ending_balance),
            net_deposits=float(s.net_deposits or 0),
            high_watermark_before=float(s.high_watermark_before),
            high_watermark_after=float(s.high_watermark_after),
            monthly_fee=float(s.monthly_fee),
            performance=float(s.performance),
            performance_fee=float(s.performance_fee),
            total_commission=float(s.total_commission),
            has_pdf=bool(s.pdf_path),
        )
        for s in snapshots
    ]


@router.get("/{snapshot_id}/pdf")
def download_statement_pdf(
    snapshot_id: int, current: CurrentClient, db: DBSession
) -> FileResponse:
    """Download the PDF statement for a specific commission snapshot."""
    snapshot = (
        db.query(CommissionSnapshot)
        .filter(
            CommissionSnapshot.id == snapshot_id,
            CommissionSnapshot.client_id == current.id,
        )
        .one_or_none()
    )
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")

    if not snapshot.pdf_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF not available")

    return FileResponse(
        path=snapshot.pdf_path,
        filename=f"commission_{snapshot.period_start.strftime('%Y-%m')}.pdf",
        media_type="application/pdf",
    )
