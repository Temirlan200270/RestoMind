"""Compatibility shim for legacy admin exports.

E0.1.x split protected REST into domain modules (``demo``, ``settings_ops``, ``export``)
assembled in ``core.py``. Keep this module tiny until old imports disappear.
"""

from .core import router
from .deps import require_admin_session, require_admin_session_active
from .orders import _check_mixed_payment_split

__all__ = [
    "_check_mixed_payment_split",
    "require_admin_session",
    "require_admin_session_active",
    "router",
]
