"""
In-process state manager.

Maintains the live state for every active client:
  - Current balance and equity
  - Open positions
  - Daily loss tracking
  - Period start balance (for commission calculation)

Thread-safe. Survives process restarts by being re-hydrated from the DB
and re-synced with broker positions on startup.
"""
from __future__ import annotations

import threading
from copy import deepcopy
from decimal import Decimal

from ambot.risk.checks import ClientState, PositionState
from ambot.types import ClientId


class StateManager:
    """
    Central in-process state store.
    All reads and writes are protected by a reentrant lock.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._states: dict[ClientId, ClientState] = {}

    # ── Read ──────────────────────────────────────────────────────────────────

    def get(self, client_id: ClientId) -> ClientState | None:
        with self._lock:
            state = self._states.get(client_id)
            return deepcopy(state) if state else None

    def all_clients(self) -> list[tuple[ClientId, ClientState]]:
        with self._lock:
            return [(cid, deepcopy(s)) for cid, s in self._states.items()]

    # ── Write ─────────────────────────────────────────────────────────────────

    def hydrate(
        self,
        client_id: ClientId,
        balance: Decimal,
        equity: Decimal,
        period_start_balance: Decimal | None = None,
    ) -> None:
        """Initialise or reset state for a client from DB/broker data."""
        with self._lock:
            self._states[client_id] = ClientState(
                client_id=client_id,
                balance=balance,
                equity=equity,
                daily_loss_pct=Decimal("0"),
                open_positions={},
                period_start_balance=period_start_balance or balance,
            )

    def update_balance(
        self,
        client_id: ClientId,
        balance: Decimal,
        equity: Decimal,
    ) -> None:
        with self._lock:
            state = self._require(client_id)
            prev_equity = state.equity
            state.balance = balance
            state.equity = equity

            # Update daily loss tracking
            if prev_equity > 0 and equity < prev_equity:
                loss = (prev_equity - equity) / prev_equity
                state.daily_loss_pct = state.daily_loss_pct + loss

    def open_position(
        self,
        client_id: ClientId,
        symbol: str,
        side: str,
        quantity: Decimal,
        entry_price: Decimal,
        leverage: Decimal = Decimal("1"),
    ) -> None:
        with self._lock:
            state = self._require(client_id)
            state.open_positions[symbol] = PositionState(
                symbol=symbol,
                side=side,
                quantity=quantity,
                entry_price=entry_price,
                leverage=leverage,
            )

    def close_position(self, client_id: ClientId, symbol: str) -> None:
        with self._lock:
            state = self._require(client_id)
            state.open_positions.pop(symbol, None)

    def inject_position(
        self,
        client_id: ClientId,
        symbol: str,
        position: PositionState,
    ) -> None:
        """Insert a position discovered by the reconciler (ghost position correction)."""
        with self._lock:
            state = self._require(client_id)
            state.open_positions[symbol] = position

    def reset_daily_loss(self, client_id: ClientId) -> None:
        """Called at start of each trading day."""
        with self._lock:
            state = self._require(client_id)
            state.daily_loss_pct = Decimal("0")

    def remove_client(self, client_id: ClientId) -> None:
        with self._lock:
            self._states.pop(client_id, None)

    # ── Private ───────────────────────────────────────────────────────────────

    def _require(self, client_id: ClientId) -> ClientState:
        state = self._states.get(client_id)
        if state is None:
            raise KeyError(f"No state found for client_id={client_id}")
        return state
