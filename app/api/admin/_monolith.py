"""Compatibility shim for legacy admin exports.

E0.1.x moved the remaining protected REST implementation to ``legacy_ops.py``.
Keep this module tiny until old imports of ``app.api.admin._monolith`` disappear.
"""

from .legacy_ops import (
    _check_mixed_payment_split,
    require_admin_session,
    require_admin_session_active,
    router,
)

__all__ = [
    "_check_mixed_payment_split",
    "require_admin_session",
    "require_admin_session_active",
    "router",
]
