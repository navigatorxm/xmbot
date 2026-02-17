"""Admin router — kill switch, engine health, system status."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Header, status
from pydantic import BaseModel

from ambot.config import get_config

router = APIRouter(prefix="/admin", tags=["admin"])

# Module-level engine reference (set by main.py on startup)
_engine = None


def set_engine(engine) -> None:
    global _engine
    _engine = engine


def _require_admin_key(x_admin_key: str | None = Header(default=None)) -> None:
    """Simple API key check for admin endpoints. Replace with proper RBAC in production."""
    cfg = get_config()
    expected = cfg.jwt_secret_key[:16]  # Use first 16 chars of JWT secret as admin key
    if x_admin_key != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


class KillSwitchRequest(BaseModel):
    reason: str


class HealthResponse(BaseModel):
    status: str
    engine_status: str | None
    client_count: int | None
    kill_switch_triggered: bool
    kill_switch_reason: str


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    """Public health check endpoint."""
    ks_triggered = False
    ks_reason = ""
    engine_status = None
    client_count = None

    if _engine is not None:
        ks_triggered = _engine.kill_switch.is_triggered()
        ks_reason = _engine.kill_switch.reason
        engine_status = _engine.status.value
        client_count = _engine.client_count

    return HealthResponse(
        status="ok" if not ks_triggered else "halted",
        engine_status=engine_status,
        client_count=client_count,
        kill_switch_triggered=ks_triggered,
        kill_switch_reason=ks_reason,
    )


@router.post("/kill-switch/trigger")
def trigger_kill_switch(
    body: KillSwitchRequest,
    x_admin_key: str | None = Header(default=None),
) -> dict:
    """Trigger the global kill switch. Halts all signal processing immediately."""
    _require_admin_key(x_admin_key)
    if _engine is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Engine not running")
    _engine.kill_switch.trigger(f"Manual trigger: {body.reason}")
    return {"message": "Kill switch triggered", "reason": body.reason}


@router.post("/kill-switch/reset")
def reset_kill_switch(x_admin_key: str | None = Header(default=None)) -> dict:
    """Reset the kill switch. Engine will resume signal processing."""
    _require_admin_key(x_admin_key)
    if _engine is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Engine not running")
    _engine.kill_switch.reset()
    return {"message": "Kill switch reset — engine will resume on next tick"}
