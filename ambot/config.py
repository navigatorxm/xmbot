"""
Pydantic-settings based configuration.
All values can be overridden via environment variables or a .env file.
Nested models use double-underscore delimiter: RISK__DEFAULT_MAX_LEVERAGE=5.0
"""
from __future__ import annotations

from decimal import Decimal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BrokerConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BROKER__", extra="ignore")

    binance_api_url: str = "https://api.binance.com"
    binance_testnet_url: str = "https://testnet.binance.vision"
    use_testnet: bool = False


class RiskConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RISK__", extra="ignore")

    default_max_daily_loss_pct: float = 0.02       # 2%
    default_max_open_positions: int = 5
    default_max_leverage: float = 3.0
    default_max_per_trade_risk_pct: float = 0.01   # 1%
    default_max_symbol_allocation_pct: float = 0.20

    volatility_guard_atr_threshold: float = 3.0    # ATR multiplier
    reconciliation_mismatch_pct: float = 0.02      # 2% triggers kill switch
    global_kill_switch: bool = False


class CommissionConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COMMISSIONS__", extra="ignore")

    monthly_aum_fee_pct: float = 0.01   # 1%
    performance_fee_pct: float = 0.20   # 20%


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "development"
    db_url: str = "sqlite:///data/db/ambot.db"

    # Master key for AES-256-GCM — 32 bytes hex encoded (64 hex chars)
    vault_master_key_hex: str = Field(
        default="",
        description="64-char hex string (32 bytes). Generate: python -c \"import secrets; print(secrets.token_hex(32))\"",
    )

    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    log_level: str = "INFO"
    pdf_output_dir: str = "data/statements"

    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    commissions: CommissionConfig = Field(default_factory=CommissionConfig)

    @field_validator("vault_master_key_hex")
    @classmethod
    def validate_master_key(cls, v: str) -> str:
        if v and len(v) != 64:
            raise ValueError(
                "vault_master_key_hex must be exactly 64 hex characters (32 bytes). "
                "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return v


# Module-level singleton (lazy)
_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = AppConfig()
    return _config
