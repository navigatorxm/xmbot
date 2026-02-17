"""
BotEngine — the central orchestrator.

One engine instance per process:
  T1: one process, all T1 clients share this engine
  T2: one process, engine manages one logical sub-instance per client
  T3: one process per container, engine manages a single T3 client

Startup sequence:
  1. Load all active clients from DB
  2. Hydrate state manager from broker balances
  3. Register scheduler jobs (reconciler, daily reset, monthly commission)
  4. Start signal processing loop

Signal processing:
  strategy.on_tick() → signals → GlobalKillSwitch + VolatilityGuard →
  DeterministicReplicator → per-client: RateLimiter → RiskGuard → OrderRouter → Journal
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

from ambot.broker.order_router import FilledOrder, OrderRouter
from ambot.broker.rate_limiter import PerClientRateLimiter
from ambot.broker.vault import KeyVault
from ambot.config import AppConfig
from ambot.core.persistence import Client as ClientModel
from ambot.core.scheduler import BotScheduler
from ambot.core.state import StateManager
from ambot.exceptions import KillSwitchTriggered
from ambot.journal.writer import JournalWriter
from ambot.risk.checks import ClientRiskConfig, ClientState
from ambot.risk.global_guard import GlobalKillSwitch, VolatilityGuard
from ambot.risk.per_client import PerClientRiskGuard
from ambot.strategies.base import AbstractStrategy
from ambot.strategies.signals import MarketSnapshot, Signal
from ambot.types import ClientId, EngineStatus, RiskDecision

log = logging.getLogger("ambot.engine")


@dataclass
class ClientContext:
    """All runtime objects needed to process signals for one client."""
    client_id: ClientId
    tier: str
    is_active: bool
    risk_guard: PerClientRiskGuard
    order_router: OrderRouter
    reference_equity: float    # Used by replicator for proportional sizing


class BotEngine:
    """
    Top-level execution orchestrator.

    Parameters
    ----------
    config:              Application configuration
    session_factory:     SQLAlchemy sessionmaker
    strategy:            The deterministic strategy instance (shared across all clients)
    vault:               Key vault for decrypting client credentials
    """

    def __init__(
        self,
        config: AppConfig,
        session_factory: Callable,
        strategy: AbstractStrategy,
        vault: KeyVault,
    ) -> None:
        self.config = config
        self._session_factory = session_factory
        self._strategy = strategy
        self._vault = vault

        self._state = StateManager()
        self._scheduler = BotScheduler()
        self._kill_switch = GlobalKillSwitch(config.risk)
        self._volatility_guard = VolatilityGuard(config.risk.volatility_guard_atr_threshold)
        self._rate_limiter = PerClientRateLimiter()
        self._journal = JournalWriter(session_factory)

        self._clients: dict[ClientId, ClientContext] = {}
        self._status = EngineStatus.RUNNING

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Boot sequence: load clients, start journal, register jobs."""
        await self._journal.start()
        await self._load_clients()

        # Reconciler — every 60 seconds
        from ambot.reconciliation.reconciler import PositionReconciler
        self._reconciler = PositionReconciler(
            session_factory=self._session_factory,
            cfg=self.config.risk,
            kill_switch=self._kill_switch,
            state_manager=self._state,
            client_contexts=self._clients,
        )
        self._scheduler.add_interval_job(
            self._reconciler.run_cycle,
            seconds=60,
            job_id="reconciler",
        )

        # Daily loss reset — midnight UTC
        self._scheduler.add_cron_job(
            self._reset_daily_losses,
            hour=0,
            minute=0,
            job_id="daily_reset",
        )

        # Monthly commission sweep — 1st of each month at 00:05 UTC
        self._scheduler.add_cron_job(
            self._monthly_commission_sweep,
            day=1,
            hour=0,
            minute=5,
            job_id="monthly_commission",
        )

        await self._scheduler.start()
        log.info(
            "BotEngine started: %d active clients, strategy=%s v%s",
            len(self._clients),
            self._strategy.name,
            self._strategy.version,
        )

    async def stop(self, reason: str = "manual shutdown") -> None:
        """Graceful shutdown: cancel orders, flush journal, stop scheduler."""
        self._status = EngineStatus.KILLED
        log.info("BotEngine stopping: %s", reason)

        # Cancel all open orders
        cancel_tasks = [
            ctx.order_router.cancel_all_open_orders()
            for ctx in self._clients.values()
        ]
        await asyncio.gather(*cancel_tasks, return_exceptions=True)

        await self._scheduler.stop()
        await self._journal.stop()
        log.info("BotEngine stopped")

    # ── Signal Processing ────────────────────────────────────────────────────

    async def on_tick(self, snapshot: MarketSnapshot) -> None:
        """
        Main entry point called by the market data feed on each new bar.
        1. Passes snapshot to strategy → signals
        2. Guards against kill switch and volatility
        3. Fans out to all active clients
        """
        if self._status != EngineStatus.RUNNING:
            return

        if self._kill_switch.is_triggered():
            return

        if self._volatility_guard.check(snapshot):
            log.debug("VolatilityGuard active, skipping tick: %s", snapshot.symbol)
            return

        signals = self._strategy.on_tick(snapshot)
        if not signals:
            return

        # Fan out each signal to all active clients concurrently
        tasks = []
        for signal in signals:
            for ctx in self._clients.values():
                if ctx.is_active:
                    tasks.append(self._process_client_signal(ctx, signal, snapshot))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _process_client_signal(
        self,
        ctx: ClientContext,
        signal: Signal,
        snapshot: MarketSnapshot,
    ) -> None:
        """Run one signal through risk → execution → journal for one client."""
        try:
            # Get client state
            state = self._state.get(ctx.client_id)
            if state is None:
                log.warning("No state for client %s, skipping signal", ctx.client_id)
                return

            # Scale signal quantity proportionally to client equity
            from ambot.social.replicator import DeterministicReplicator
            scaled_signal = DeterministicReplicator.scale_signal(
                signal, state.equity, ctx.reference_equity
            )

            # Risk evaluation
            decision = ctx.risk_guard.evaluate(scaled_signal, state)

            if decision.action == RiskDecision.BLOCK:
                log.info(
                    "Signal blocked for client=%s: %s",
                    ctx.client_id, decision.reason,
                )
                return

            if decision.action == RiskDecision.REDUCE and decision.adjusted_size is not None:
                scaled_signal = scaled_signal.with_size(decision.adjusted_size)

            # Order submission
            filled = await ctx.order_router.submit(scaled_signal)

            # State update
            if filled.action == "open":
                self._state.open_position(
                    client_id=ctx.client_id,
                    symbol=filled.symbol,
                    side="long" if filled.side == "buy" else "short",
                    quantity=filled.filled_quantity,
                    entry_price=filled.filled_price or signal.entry_price or snapshot.close,
                    leverage=filled.leverage,
                )
            elif filled.action == "close":
                self._state.close_position(ctx.client_id, filled.symbol)

            # Journal
            await self._journal.write_trade(filled)
            log.debug(
                "Trade executed: client=%s symbol=%s side=%s qty=%s",
                ctx.client_id, filled.symbol, filled.side, filled.quantity,
            )

        except KillSwitchTriggered:
            raise
        except Exception as exc:
            log.error(
                "Error processing signal for client=%s: %s",
                ctx.client_id, exc, exc_info=True,
            )

    # ── Scheduled Tasks ──────────────────────────────────────────────────────

    async def _reset_daily_losses(self) -> None:
        """Reset daily loss tracking for all clients at midnight UTC."""
        for client_id in list(self._clients.keys()):
            self._state.reset_daily_loss(client_id)
        log.info("Daily loss counters reset for %d clients", len(self._clients))

    async def _monthly_commission_sweep(self) -> None:
        """Run commission calculation for all clients on the 1st of the month."""
        from ambot.commissions.snapshot import MonthlySnapshotService
        from ambot.commissions.calculator import HybridCommissionCalculator
        from ambot.commissions.watermark import HighWatermarkTracker
        from ambot.commissions.statement import PDFStatementGenerator
        from ambot.journal.query import JournalQuery

        calc = HybridCommissionCalculator(self.config.commissions)
        hwm_tracker = HighWatermarkTracker(self._session_factory)
        journal_query = JournalQuery(self._session_factory)
        pdf_gen = PDFStatementGenerator(self.config.pdf_output_dir)
        service = MonthlySnapshotService(
            session_factory=self._session_factory,
            calculator=calc,
            hwm_tracker=hwm_tracker,
            journal_query=journal_query,
            pdf_generator=pdf_gen,
        )

        for client_id, ctx in self._clients.items():
            state = self._state.get(client_id)
            if state is None:
                continue
            try:
                await service.run_for_client(
                    client_id=client_id,
                    order_router=ctx.order_router,
                    period_start_balance=state.period_start_balance,
                )
            except Exception as exc:
                log.error("Commission sweep failed for client=%s: %s", client_id, exc)

    # ── Client Loading ───────────────────────────────────────────────────────

    async def _load_clients(self) -> None:
        """Hydrate all active clients from DB and initialise their contexts."""
        with self._session_factory() as session:
            clients: list[ClientModel] = (
                session.query(ClientModel)
                .filter(ClientModel.is_active == True)  # noqa: E712
                .all()
            )

        for client in clients:
            await self._register_client(client)

        log.info("Loaded %d active clients", len(self._clients))

    async def _register_client(self, client: ClientModel) -> None:
        """Build a ClientContext for one client and hydrate their state."""
        from ambot.broker.client import BinanceClient

        cid = ClientId(client.id)

        # Decrypt credentials — kept in memory only, never stored
        if not client.encrypted_keys:
            log.warning("Client %s has no API keys — skipping", cid)
            return

        api_key, api_secret = self._vault.decrypt_keypair(
            client.encrypted_keys.encrypted_api_key,
            client.encrypted_keys.encrypted_api_secret,
        )

        binance = BinanceClient(
            api_key=api_key,
            api_secret=api_secret,
            testnet=self.config.broker.use_testnet,
        )

        # Immediately delete plaintext credentials from local scope
        del api_key, api_secret

        router = OrderRouter(
            binance_client=binance,
            rate_limiter=self._rate_limiter,
            client_id=cid,
        )

        risk_cfg = ClientRiskConfig(
            max_daily_loss_pct=float(client.max_daily_loss_pct or self.config.risk.default_max_daily_loss_pct),
            max_open_positions=int(client.max_open_positions or self.config.risk.default_max_open_positions),
            max_leverage=float(client.max_leverage or self.config.risk.default_max_leverage),
            max_per_trade_risk_pct=float(client.max_per_trade_risk_pct or self.config.risk.default_max_per_trade_risk_pct),
            max_symbol_allocation_pct=float(client.max_symbol_allocation_pct or self.config.risk.default_max_symbol_allocation_pct),
        )

        self._clients[cid] = ClientContext(
            client_id=cid,
            tier=client.tier,
            is_active=client.is_active,
            risk_guard=PerClientRiskGuard(risk_cfg),
            order_router=router,
            reference_equity=float(client.reference_equity or 10000),
        )

        # Hydrate state from broker
        try:
            balance = await router._client.get_account_balance()
            from decimal import Decimal
            self._state.hydrate(
                client_id=cid,
                balance=balance.total_usdt,
                equity=balance.total_usdt,
            )
        except Exception as exc:
            log.warning("Could not fetch balance for client %s: %s — using zero", cid, exc)
            from decimal import Decimal
            self._state.hydrate(cid, Decimal("0"), Decimal("0"))

    # ── Public Accessors ─────────────────────────────────────────────────────

    @property
    def kill_switch(self) -> GlobalKillSwitch:
        return self._kill_switch

    @property
    def status(self) -> EngineStatus:
        return self._status

    @property
    def client_count(self) -> int:
        return len(self._clients)
