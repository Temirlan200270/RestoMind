"""E0.1: bookings router is mounted once."""

from __future__ import annotations

from app.main import app


def test_admin_bookings_routes_are_not_mounted_twice() -> None:
    matches = [
        route
        for route in app.routes
        if getattr(route, "path", "") == "/api/admin/bookings"
        and "GET" in getattr(route, "methods", set())
    ]
    assert len(matches) == 1
