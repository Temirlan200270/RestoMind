from __future__ import annotations

import pathlib


def test_admin_app_wires_location_filter_to_loaders() -> None:
    js = pathlib.Path("app/static/js/admin-app.js").read_text(encoding="utf-8")

    assert "available_locations" in js
    assert "selectedLocationId" in js
    assert "activeLocationId" in js
    assert "locationQueryParams()" in js
    assert "locationQueryString" in js
    assert "/api/admin/orders?" in js and "p.set(k, v)" in js
    assert "/api/admin/chats?limit=" in js and "locationQuery" in js
    assert "/api/admin/stats${this.locationQueryString('?')}" in js
    assert "/api/admin/funnel?days=7${qs}" in js
    assert "/api/admin/analytics?period=" in js
    assert "/api/admin/intelligence/os-dashboard${this.locationQueryString('?')}" in js


def test_header_has_location_selector() -> None:
    html = pathlib.Path("app/templates/screens/_header.html").read_text(encoding="utf-8")

    assert "available_locations" in html
    assert 'x-model="selectedLocationId"' in html
    assert "onLocationFilterChanged()" in html
    assert "Все точки" in html
