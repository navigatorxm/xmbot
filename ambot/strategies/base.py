"""Abstract base class for all ambot strategies."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ambot.strategies.signals import MarketSnapshot, Signal


class AbstractStrategy(ABC):
    """
    All strategies implement this interface.

    Determinism contract
    --------------------
    on_tick() MUST be a pure function of its inputs:
    - Same MarketSnapshot → same list of Signals (same shape, same direction)
    - No side-effects on external state
    - No random numbers with unseeded generators
    - No external API calls or I/O inside on_tick()
    - No time-dependent logic beyond snapshot.timestamp

    All parameters that affect signal generation must be captured in
    strategy.version so they can be audited.
    """

    @abstractmethod
    def on_tick(self, snapshot: MarketSnapshot) -> list[Signal]:
        """
        Process a market snapshot and return zero or more signals.

        Parameters
        ----------
        snapshot:
            Immutable market data for the current bar.

        Returns
        -------
        list[Signal]
            Empty list means no action this tick.
        """
        ...

    @abstractmethod
    def on_fill(self, fill: object) -> None:
        """
        Called after a trade is confirmed filled.
        Used to update strategy-internal position tracking.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy identifier, e.g. 'ema_cross_v1'."""
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        """
        Semantic version string, e.g. '1.0.0'.
        Increment whenever signal generation logic or parameters change.
        This version is embedded in every Signal for audit purposes.
        """
        ...

    @property
    @abstractmethod
    def symbols(self) -> list[str]:
        """List of trading pairs this strategy operates on."""
        ...
