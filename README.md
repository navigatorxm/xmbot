# xmbot

Deterministic crypto trading bot management platform with multi-client support, AES-256 key vault, hybrid commission engine, and social trade replication.

## Architecture

```
xmbot/
├── ambot/          # VPS execution engine (strategy, risk, broker, commissions)
├── web/            # mbot.online FastAPI web layer (onboarding, dashboard)
├── tests/          # Unit + integration test suite
└── infra/          # Docker, supervisord, nginx configs
```

## Capital Tiers

| Tier | Capital Range | Isolation |
|------|--------------|-----------|
| T1 | $1,000 – $5,000 | Shared engine |
| T2 | $5,001 – $20,000 | Logical isolation |
| T3 | $20,001 – $50,000 | Dedicated container |

## Quick Start (Development)

```bash
# 1. Create virtualenv
python -m venv venv && source venv/bin/activate

# 2. Install dependencies
pip install -r requirements/dev.txt

# 3. Configure environment
cp .env.example .env
# Edit .env — set VAULT_MASTER_KEY_HEX and JWT_SECRET_KEY

# 4. Run database migrations
alembic upgrade head

# 5. Run tests
pytest

# 6. Start the execution engine
python -m ambot.main
```

## Production Deployment

```bash
# Build and start all services
docker-compose -f infra/docker-compose.yml up -d
```

## Commission Model

Hybrid fee structure:
- **1% monthly** of starting balance (AUM fee)
- **20% performance** on profits above high-water mark

```
total_fee = (starting_balance × 0.01) + max(0, adjusted_ending − hwm) × 0.20
```

## Security

- Client Binance API keys encrypted at rest (AES-256-GCM)
- Keys decrypted only in memory during execution — never logged or persisted in plaintext
- No withdrawal permissions on client API keys
- VPS IP restriction enforced per client

## License

Proprietary — All rights reserved.
