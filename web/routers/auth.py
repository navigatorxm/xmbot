"""Auth router — JWT login and token refresh."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status
from passlib.context import CryptContext
from jose import jwt
from pydantic import BaseModel

from ambot.config import get_config
from ambot.core.persistence import Client
from web.dependencies import DBSession

router = APIRouter(prefix="/auth", tags=["auth"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


def _create_token(client_id: str) -> str:
    cfg = get_config()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=cfg.jwt_expire_minutes)
    payload = {"sub": client_id, "iat": now, "exp": expire}
    return jwt.encode(payload, cfg.jwt_secret_key, algorithm=cfg.jwt_algorithm)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: DBSession) -> TokenResponse:
    client = db.query(Client).filter(Client.email == body.email).one_or_none()
    if client is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    cfg = get_config()
    token = _create_token(client.id)
    return TokenResponse(access_token=token, expires_in=cfg.jwt_expire_minutes * 60)
