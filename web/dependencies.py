"""
FastAPI dependency-injection providers.
"""
from __future__ import annotations

from typing import Annotated, Generator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from ambot.config import get_config
from ambot.core.persistence import Client, make_session_factory

# Module-level session factory (initialised on first import)
_session_factory = None


def _get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = make_session_factory()
    return _session_factory


def get_db() -> Generator[Session, None, None]:
    """Provide a DB session per request."""
    factory = _get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DBSession = Annotated[Session, Depends(get_db)]

# ── Auth ─────────────────────────────────────────────────────────────────────
security = HTTPBearer()


def get_current_client(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    db: DBSession,
) -> Client:
    """Validate JWT and return the associated Client record."""
    cfg = get_config()
    token = credentials.credentials
    try:
        payload = jwt.decode(token, cfg.jwt_secret_key, algorithms=[cfg.jwt_algorithm])
        client_id: str | None = payload.get("sub")
        if client_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    client = db.query(Client).filter(Client.id == client_id).one_or_none()
    if client is None or not client.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Client not found")
    return client


CurrentClient = Annotated[Client, Depends(get_current_client)]
