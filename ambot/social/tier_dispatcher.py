"""
TierDispatcher — routes signals to clients based on their isolation tier.

T1 (shared engine):     Inline function call in the same event loop.
T2 (logical isolation): asyncio.Queue per client sub-instance.
T3 (container):         IPC via named pipe or Redis pub-sub to the client's container.

The engine currently uses the replicator + direct calls for T1/T2.
T3 IPC is designed but stubbed for the initial release; use a separate
container per T3 client with its own engine instance.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from ambot.strategies.signals import Signal
from ambot.types import ClientId, Tier

log = logging.getLogger("ambot.social.dispatcher")


class TierDispatcher:
    """
    Determines the dispatch mechanism based on a client's tier.

    T1 → direct call (handled by BotEngine._process_client_signal)
    T2 → asyncio.Queue per client (client has its own consumer coroutine)
    T3 → IPC (not implemented in v1; use separate container + engine instance)
    """

    def __init__(self) -> None:
        self._t2_queues: dict[ClientId, asyncio.Queue[Signal]] = {}
        self._t2_consumers: dict[ClientId, asyncio.Task] = {}

    def register_t2_client(
        self,
        client_id: ClientId,
        consumer: Callable[[Signal], asyncio.Coroutine],
        queue_size: int = 100,
    ) -> None:
        """
        Register a T2 client with its own signal queue.
        The consumer coroutine is started as a background task.
        """
        queue: asyncio.Queue[Signal] = asyncio.Queue(maxsize=queue_size)
        self._t2_queues[client_id] = queue

        async def _consume() -> None:
            while True:
                signal = await queue.get()
                try:
                    await consumer(signal)
                except Exception as exc:
                    log.error("T2 consumer error for client=%s: %s", client_id, exc)
                finally:
                    queue.task_done()

        self._t2_consumers[client_id] = asyncio.create_task(
            _consume(), name=f"t2_consumer_{client_id}"
        )
        log.info("Registered T2 sub-instance for client=%s", client_id)

    async def dispatch(
        self,
        client_id: ClientId,
        tier: Tier,
        signal: Signal,
        inline_handler: Callable[[Signal], asyncio.Coroutine] | None = None,
    ) -> None:
        """Route a signal to the appropriate dispatch mechanism."""
        if tier == Tier.T1:
            # Inline: handled directly by the caller (BotEngine)
            if inline_handler:
                await inline_handler(signal)

        elif tier == Tier.T2:
            queue = self._t2_queues.get(client_id)
            if queue is None:
                log.warning("No T2 queue for client=%s, falling back to inline", client_id)
                if inline_handler:
                    await inline_handler(signal)
            else:
                try:
                    queue.put_nowait(signal)
                except asyncio.QueueFull:
                    log.warning("T2 queue full for client=%s — signal dropped", client_id)

        elif tier == Tier.T3:
            # T3: each client runs in its own container with its own BotEngine.
            # Signals are dispatched via a separate market data feed to the container.
            # The container's engine processes signals independently.
            log.debug("T3 client=%s — signal handled by dedicated container", client_id)

    async def stop(self) -> None:
        """Cancel all T2 consumer tasks."""
        for task in self._t2_consumers.values():
            task.cancel()
        if self._t2_consumers:
            await asyncio.gather(*self._t2_consumers.values(), return_exceptions=True)
