"""POS adapter registry bootstrap."""

from __future__ import annotations

from app.services.pos.adapters.base import register_pos_adapter
from app.services.pos.adapters.iiko_adapter import IikoPOSAdapter
from app.services.pos.adapters.rkeeper_adapter import RKeeperPOSAdapter

register_pos_adapter("iiko", IikoPOSAdapter)
register_pos_adapter("rkeeper", RKeeperPOSAdapter)

__all__ = ["IikoPOSAdapter", "RKeeperPOSAdapter", "register_pos_adapter"]
