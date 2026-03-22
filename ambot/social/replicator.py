"""
DeterministicReplicator — scales signals proportionally to each client's equity.

The 'deterministic' property means:
  - All clients receive identical signal SHAPE (direction, symbol, sl/tp ratios)
  - Only position SIZE differs, scaled linearly to client equity
  - Given the same market snapshot, all clients ALWAYS get the same signal shape

This is distinct from master-follower copy trading:
  - No dependency on a master account's actual trade
  - Each client executes independently
  - Clean accounting — no capital pooling
"""
from __future__ import annotations

from decimal import Decimal

from ambot.strategies.signals import Signal


class DeterministicReplicator:
    """
    Scales a base signal to each client's equity.

    The base signal's quantity is assumed to be sized for `reference_equity`.
    Each client's actual quantity = base_qty × (client_equity / reference_equity).
    """

    @staticmethod
    def scale_signal(
        signal: Signal,
        client_equity: Decimal,
        reference_equity: float,
    ) -> Signal:
        """
        Return a new Signal with quantity scaled to the client's equity.

        Parameters
        ----------
        signal:           The base signal from the strategy.
        client_equity:    The client's current equity in USDT.
        reference_equity: The equity the strategy was sized for (e.g. 10,000).

        Returns
        -------
        Signal
            A new Signal with a fresh UUID and scaled quantity.
            Returns the original signal unmodified if reference_equity is zero.
        """
        ref = Decimal(str(reference_equity))
        if ref <= 0 or client_equity <= 0:
            return signal

        ratio = client_equity / ref
        scaled_qty = signal.quantity * ratio

        # Enforce minimum quantity (exchange minimum lot size)
        if scaled_qty <= Decimal("0"):
            return signal

        return signal.with_size(scaled_qty)

    @staticmethod
    def scale_stop_loss_pct(signal: Signal) -> Decimal | None:
        """
        Return the stop-loss as a percentage of entry price.
        Useful for verifying the sl/tp ratio is preserved after scaling.
        """
        if signal.stop_loss is None or signal.entry_price is None:
            return None
        if signal.entry_price == 0:
            return None
        return abs(signal.stop_loss - signal.entry_price) / signal.entry_price

    @staticmethod
    def scale_take_profit_pct(signal: Signal) -> Decimal | None:
        """Return the take-profit as a percentage of entry price."""
        if signal.take_profit is None or signal.entry_price is None:
            return None
        if signal.entry_price == 0:
            return None
        return abs(signal.take_profit - signal.entry_price) / signal.entry_price
