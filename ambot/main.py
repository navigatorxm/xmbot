"""
ambot entry point.

Usage:
  python -m ambot.main              # Start the engine
  python -m ambot.main --testnet   # Start in testnet mode
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from ambot.config import get_config
from ambot.core.persistence import make_session_factory
from ambot.broker.vault import KeyVault
from ambot.core.engine import BotEngine
from ambot.strategies.deterministic import DeterministicStrategy


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("logs/ambot.log"),
        ],
    )


async def run() -> None:
    cfg = get_config()
    setup_logging(cfg.log_level)
    log = logging.getLogger("ambot.main")
    log.info("Starting ambot v0.1.0 (env=%s)", cfg.env)

    if not cfg.vault_master_key_hex:
        log.critical("VAULT_MASTER_KEY_HEX is not set — cannot decrypt API keys")
        sys.exit(1)

    session_factory = make_session_factory(cfg.db_url)
    vault = KeyVault(cfg.vault_master_key_hex)
    strategy = DeterministicStrategy()

    engine = BotEngine(
        config=cfg,
        session_factory=session_factory,
        strategy=strategy,
        vault=vault,
    )

    # Graceful shutdown on SIGINT / SIGTERM
    loop = asyncio.get_running_loop()

    def _shutdown(sig: signal.Signals) -> None:
        log.info("Received %s — initiating graceful shutdown", sig.name)
        loop.create_task(engine.stop(reason=sig.name))

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown, sig)

    await engine.start()

    # Keep running until stopped
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass

    log.info("ambot shutdown complete")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
