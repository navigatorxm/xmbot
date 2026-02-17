"""
PositionReconciler — 60-second reconciliation loop.

On each cycle, for every active client:
  1. Fetch actual positions from Binance
  2. Compare with internal StateManager positions
  3. If mismatch > threshold → trigger global kill switch
  4. If minor/ghost position → correct internal state
  5. Log any discrepancies for audit

This prevents state drift caused by:
  - Network errors during order submission
  - Manual trades on the Binance account
  - Exchange-side partial fills
  - Process restarts
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Callable

from ambot.config import RiskConfig
from ambot.core.state import StateManager
from ambot.exceptions import PositionMismatch
from ambot.risk.checks import PositionState
from ambot.risk.global_guard import GlobalKillSwitch

if TYPE_CHECKING:
    from ambot.core.engine import ClientContext

log = logging.getLogger("ambot.reconciler")


class PositionReconciler:
    """
    Runs every 60 seconds (scheduled by BotScheduler).

    Compares broker positions against internal state and triggers
    the global kill switch if the divergence exceeds the configured threshold.
    """

    def __init__(
        self,
        session_factory: Callable,
        cfg: RiskConfig,
        kill_switch: GlobalKillSwitch,
        state_manager: StateManager,
        client_contexts: dict,  # ClientId → ClientContext
    ) -> None:
        self._session_factory = session_factory
        self._cfg = cfg
        self._ks = kill_switch
        self._state = state_manager
        self._contexts = client_contexts

    async def run_cycle(self) -> None:
        """Run one reconciliation cycle across all active clients."""
        if self._ks.is_triggered():
            return  # Don't reconcile if engine is already halted

        for client_id, ctx in list(self._contexts.items()):
            if not ctx.is_active:
                continue
            try:
                await self._reconcile_client(client_id, ctx)
            except PositionMismatch as exc:
                self._ks.trigger(str(exc))
                log.critical("Kill switch triggered by reconciler: %s", exc)
                return  # Halt all further reconciliation this cycle
            except Exception as exc:
                log.error("Reconciliation error for client=%s: %s", client_id, exc)

    async def _reconcile_client(self, client_id: str, ctx: "ClientContext") -> None:
        """Compare broker positions vs internal state for one client."""
        state = self._state.get(client_id)
        if state is None:
            return

        # Fetch live positions from exchange
        try:
            broker_positions = await ctx.order_router._client.get_open_positions()
        except Exception as exc:
            log.warning(
                "Cannot fetch positions for client=%s (broker unavailable): %s",
                client_id, exc,
            )
            return

        broker_map: dict[str, Decimal] = {
            p.symbol: p.quantity for p in broker_positions
        }
        internal_map: dict[str, PositionState] = state.open_positions

        # Check broker positions against internal state
        for symbol, broker_qty in broker_map.items():
            internal_pos = internal_map.get(symbol)

            if internal_pos is None:
                # Ghost position — exists on broker but not internally
                log.warning(
                    "Ghost position detected: client=%s symbol=%s qty=%s — injecting",
                    client_id, symbol, broker_qty,
                )
                ghost = PositionState(
                    symbol=symbol,
                    side="long",  # Side unknown — conservative assumption
                    quantity=broker_qty,
                    entry_price=Decimal("0"),
                )
                self._state.inject_position(client_id, symbol, ghost)
                continue

            # Calculate mismatch percentage
            if broker_qty > 0:
                mismatch_pct = abs(broker_qty - internal_pos.quantity) / broker_qty
                threshold = Decimal(str(self._cfg.reconciliation_mismatch_pct))

                if mismatch_pct > threshold:
                    raise PositionMismatch(client_id, symbol, float(mismatch_pct))

                if mismatch_pct > 0:
                    log.debug(
                        "Minor mismatch corrected: client=%s symbol=%s "
                        "broker=%s internal=%s (%.2f%%)",
                        client_id, symbol, broker_qty,
                        internal_pos.quantity, float(mismatch_pct) * 100,
                    )
                    # Correct internal state to match broker
                    internal_pos.quantity = broker_qty

        # Check for internal positions that no longer exist on the broker
        for symbol in set(internal_map.keys()) - set(broker_map.keys()):
            log.warning(
                "Internal position not on broker: client=%s symbol=%s — closing internally",
                client_id, symbol,
            )
            self._state.close_position(client_id, symbol)

        # Update balance from broker
        try:
            balance = await ctx.order_router._client.get_account_balance()
            self._state.update_balance(client_id, balance.total_usdt, balance.total_usdt)
        except Exception as exc:
            log.warning("Balance update failed for client=%s: %s", client_id, exc)
