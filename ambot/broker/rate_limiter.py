"""
Per-client token bucket rate limiter.

Binance enforces rate limits per API key, so each client must have an
independent bucket. A misbehaving or fast client cannot throttle others.
"""
from __future__ import annotations

import threading
import time


class TokenBucket:
    """
    Thread-safe token bucket.

    Parameters
    ----------
    rate:     Tokens refilled per second.
    capacity: Maximum token accumulation (burst allowance).
    """

    def __init__(self, rate: float = 10.0, capacity: float = 20.0) -> None:
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, tokens: float = 1.0) -> bool:
        """
        Attempt to consume `tokens` from the bucket.

        Returns True if the tokens were available (request allowed),
        False if the bucket is depleted (request should be throttled).
        """
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def _refill(self) -> None:
        now = time.monotonic()
        delta = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + delta * self.rate)
        self._last_refill = now

    @property
    def available_tokens(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens


class PerClientRateLimiter:
    """
    Registry of per-client token buckets.
    Thread-safe — safe to call from multiple coroutines.
    """

    def __init__(
        self,
        default_rate: float = 10.0,
        default_capacity: float = 20.0,
    ) -> None:
        self._default_rate = default_rate
        self._default_capacity = default_capacity
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def allow(self, client_id: str) -> bool:
        """Check and consume one token for the given client."""
        bucket = self._get_or_create(client_id)
        return bucket.consume()

    def _get_or_create(self, client_id: str) -> TokenBucket:
        with self._lock:
            if client_id not in self._buckets:
                self._buckets[client_id] = TokenBucket(
                    rate=self._default_rate,
                    capacity=self._default_capacity,
                )
            return self._buckets[client_id]

    def configure(self, client_id: str, rate: float, capacity: float) -> None:
        """Set a custom rate/capacity for a specific client."""
        with self._lock:
            self._buckets[client_id] = TokenBucket(rate=rate, capacity=capacity)
