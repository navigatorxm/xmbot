"""Client onboarding and management router."""
from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr

from ambot.broker.vault import KeyVault
from ambot.config import get_config
from ambot.core.persistence import Client, EncryptedKeyRecord
from ambot.types import classify_tier
from decimal import Decimal
from web.dependencies import DBSession, CurrentClient

router = APIRouter(prefix="/clients", tags=["clients"])


class OnboardRequest(BaseModel):
    name: str
    email: EmailStr
    api_key: str
    api_secret: str
    capital_usdt: float
    allowed_ips: list[str] = []


class ClientResponse(BaseModel):
    id: str
    name: str
    email: str
    tier: str
    is_active: bool
    reference_equity: float


class ApiKeySubmission(BaseModel):
    api_key: str
    api_secret: str
    allowed_ips: list[str] = []


@router.post("/onboard", response_model=ClientResponse, status_code=status.HTTP_201_CREATED)
def onboard_client(body: OnboardRequest, db: DBSession) -> ClientResponse:
    """
    Register a new client and store their Binance API credentials encrypted.

    Flow:
    1. Validate email uniqueness
    2. Classify tier based on capital
    3. Encrypt API credentials with AES-256-GCM
    4. Persist client + encrypted key record
    """
    cfg = get_config()

    # Check duplicate
    existing = db.query(Client).filter(Client.email == body.email).one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Client with email {body.email} already exists",
        )

    # Classify tier
    tier = classify_tier(Decimal(str(body.capital_usdt)))

    # Create client record
    client = Client(
        name=body.name,
        email=body.email,
        tier=tier.value,
        reference_equity=Decimal(str(body.capital_usdt)),
    )
    db.add(client)
    db.flush()  # Get the generated ID

    # Encrypt credentials
    vault = KeyVault(cfg.vault_master_key_hex)
    enc_key, enc_secret = vault.encrypt_keypair(body.api_key, body.api_secret)

    # Security: clear plaintext from memory
    del body.api_key, body.api_secret

    key_record = EncryptedKeyRecord(
        client_id=client.id,
        encrypted_api_key=enc_key,
        encrypted_api_secret=enc_secret,
        key_label=f"{body.name} primary key",
        allowed_ips=json.dumps(body.allowed_ips) if body.allowed_ips else None,
    )
    db.add(key_record)
    db.commit()
    db.refresh(client)

    return ClientResponse(
        id=client.id,
        name=client.name,
        email=client.email,
        tier=client.tier,
        is_active=client.is_active,
        reference_equity=float(client.reference_equity),
    )


@router.get("/me", response_model=ClientResponse)
def get_my_profile(current: CurrentClient) -> ClientResponse:
    return ClientResponse(
        id=current.id,
        name=current.name,
        email=current.email,
        tier=current.tier,
        is_active=current.is_active,
        reference_equity=float(current.reference_equity or 0),
    )


@router.put("/me/api-keys")
def update_api_keys(body: ApiKeySubmission, current: CurrentClient, db: DBSession) -> dict:
    """Replace API credentials for the authenticated client."""
    cfg = get_config()
    vault = KeyVault(cfg.vault_master_key_hex)
    enc_key, enc_secret = vault.encrypt_keypair(body.api_key, body.api_secret)
    del body.api_key, body.api_secret

    record = current.encrypted_keys
    if record is None:
        record = EncryptedKeyRecord(
            client_id=current.id,
            encrypted_api_key=enc_key,
            encrypted_api_secret=enc_secret,
            allowed_ips=json.dumps(body.allowed_ips) if body.allowed_ips else None,
        )
        db.add(record)
    else:
        from datetime import datetime, timezone
        record.encrypted_api_key = enc_key
        record.encrypted_api_secret = enc_secret
        record.allowed_ips = json.dumps(body.allowed_ips) if body.allowed_ips else None
        record.rotated_at = datetime.now(timezone.utc)

    db.commit()
    return {"message": "API keys updated successfully"}
