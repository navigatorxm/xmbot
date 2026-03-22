"""
HighWatermarkTracker — persists and updates per-client HWM.

The HWM:
  - Starts at zero (or the client's initial balance) on first registration
  - Only moves upward — never resets on a loss
  - Is updated atomically with the commission snapshot creation
  - Is persistent across process restarts via the DB
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Callable

from ambot.types import ClientId

log = logging.getLogger("ambot.commissions.hwm")


class HighWatermarkTracker:
    def __init__(self, session_factory: Callable) -> None:
        self._session_factory = session_factory

    def get(self, client_id: ClientId) -> Decimal:
        """Retrieve current HWM for a client. Returns 0 if not yet set."""
        from ambot.core.persistence import ClientHWM

        with self._session_factory() as session:
            record = (
                session.query(ClientHWM)
                .filter(ClientHWM.client_id == client_id)
                .one_or_none()
            )
            # Read hwm inside the session to avoid DetachedInstanceError
            if record is None:
                return Decimal("0")
            return Decimal(str(record.hwm))

    def update(self, client_id: ClientId, new_hwm: Decimal) -> None:
        """
        Persist a new HWM value.
        The caller is responsible for ensuring new_hwm >= old_hwm.
        """
        from ambot.core.persistence import ClientHWM

        with self._session_factory() as session:
            record = (
                session.query(ClientHWM)
                .filter(ClientHWM.client_id == client_id)
                .one_or_none()
            )
            if record is None:
                record = ClientHWM(client_id=client_id, hwm=new_hwm)
                session.add(record)
            else:
                old = Decimal(str(record.hwm))
                if new_hwm < old:
                    log.warning(
                        "HWM update rejected for client=%s: new (%.2f) < old (%.2f)",
                        client_id, float(new_hwm), float(old),
                    )
                    return
                record.hwm = new_hwm
            session.commit()
            log.info("HWM updated for client=%s: %.2f", client_id, float(new_hwm))

    def initialise(self, client_id: ClientId, initial_equity: Decimal) -> None:
        """
        Set the initial HWM for a new client.
        Only creates the record if one doesn't already exist.
        """
        from ambot.core.persistence import ClientHWM

        with self._session_factory() as session:
            exists = (
                session.query(ClientHWM)
                .filter(ClientHWM.client_id == client_id)
                .one_or_none()
            )
            if exists is None:
                session.add(ClientHWM(client_id=client_id, hwm=initial_equity))
                session.commit()
                log.info(
                    "HWM initialised for client=%s at %.2f",
                    client_id, float(initial_equity),
                )
