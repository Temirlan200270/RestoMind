"""Tests for GuestCare 2GIS/Google HTML parsers (fixture-based, no network)."""

from __future__ import annotations

from pathlib import Path

import pytest
import httpx

from app.services.guestcare_parser import (
    GOOGLE_REVIEWS_LIMITATION,
    detect_review_source,
    parse_2gis_page,
    parse_google_page,
    parse_reviews_from_html,
)
from app.services.external_reviews_sync import normalize_2gis_reviews_url

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "guestcare"
GIS_HTML = FIXTURE_DIR / "2gis_firm_page.html"
GOOGLE_HTML = FIXTURE_DIR / "google_maps_empty.html"


def test_detect_review_source() -> None:
    assert detect_review_source("https://2gis.kz/almaty/firm/123") == "2gis"
    assert detect_review_source("https://www.google.com/maps/place/foo") == "google"
    assert detect_review_source("https://example.com/review") == "external"


def test_normalize_2gis_reviews_url_prefers_tab_reviews() -> None:
    assert normalize_2gis_reviews_url("https://2gis.kz/pavlodar/firm/70000001110142368") == (
        "https://2gis.kz/pavlodar/firm/70000001110142368/tab/reviews"
    )
    assert normalize_2gis_reviews_url("https://2gis.kz/pavlodar/firm/70000001110142368/tab/reviews") == (
        "https://2gis.kz/pavlodar/firm/70000001110142368/tab/reviews"
    )
    assert normalize_2gis_reviews_url("https://example.com/review") == "https://example.com/review"


def test_parse_2gis_fixture_extracts_json_ld_and_embedded() -> None:
    html = GIS_HTML.read_text(encoding="utf-8")
    reviews = parse_2gis_page(html, "https://2gis.kz/almaty/firm/test-restaurant")
    assert len(reviews) == 3
    authors = {r.author for r in reviews}
    assert "Анна К." in authors
    assert "Болат" in authors
    assert "Мария" in authors
    anna = next(r for r in reviews if r.author == "Анна К.")
    assert anna.rating == 5
    assert anna.source == "2gis"
    assert "Отличная кухня" in anna.text
    extra = next(r for r in reviews if r.external_id == "rev-extra-99")
    assert extra.rating == 4


def test_parse_google_fixture_empty_best_effort() -> None:
    html = GOOGLE_HTML.read_text(encoding="utf-8")
    reviews = parse_google_page(html, "https://www.google.com/maps/place/Test")
    assert reviews == []
    assert "Places API" in GOOGLE_REVIEWS_LIMITATION


def test_parse_reviews_from_html_dispatches() -> None:
    html = GIS_HTML.read_text(encoding="utf-8")
    items = parse_reviews_from_html(html, "https://2gis.kz/almaty/firm/x")
    assert len(items) >= 2


def test_parse_2gis_visible_review_cards_from_tab_reviews_html() -> None:
    html = """
    <div class="_1rowqpjv">
      <span class="_19h0cqe" title="Apple User">Apple User</span>
      <div class="_hf8ayi">
        <svg color="#ffb81c"></svg><svg color="#ffb81c"></svg><svg color="#ffb81c"></svg>
        <svg color="#ffb81c"></svg><svg color="#ffb81c"></svg>
      </div>
      <div class="_83kmcy">
        <a class="_oxthv5">Кухня на высшем! Официант Арыстан отлично выполняет работу.</a>
      </div>
    </div>
    <div class="_1rowqpjv">
      <span class="_19h0cqe" title="balnur">balnur</span>
      <div class="_hf8ayi">
        <svg color="#ffb81c"></svg><svg color="#ffb81c"></svg>
        <svg color="#929292"></svg><svg color="#929292"></svg><svg color="#929292"></svg>
      </div>
      <div class="_83kmcy">
        <a class="_co8kyiw">обслуживание очееенььь плохое, приборы принесли после.</a>
        <span class="_1e65qgv">Читать целиком</span>
      </div>
      <div class="_sgs1pz">
        <span class="_1cf08aj" title="Plovхана, кафе-халяль">Plovхана, кафе-халяль</span>
        <div class="_1wk3bjs">Здравствуйте! Нам очень жаль...</div>
      </div>
    </div>
    """

    reviews = parse_2gis_page(html, "https://2gis.kz/pavlodar/firm/70000001110142368/tab/reviews")

    assert len(reviews) == 2
    assert reviews[0].author == "Apple User"
    assert reviews[0].rating == 5
    assert "Кухня на высшем" in reviews[0].text
    assert reviews[1].author == "balnur"
    assert reviews[1].rating == 2
    assert "Читать целиком" not in reviews[1].text
    assert "Plovхана" not in reviews[1].text


@pytest.mark.asyncio
async def test_sync_external_reviews_from_fixture(db_session) -> None:
    from sqlalchemy import select

    from app.db.models import ExternalReview, Organization
    from app.services.external_reviews_sync import sync_external_reviews_for_org

    org = Organization(
        name="GuestCare Sync Org",
        slug="guestcare-sync-org",
        review_url_2gis="https://2gis.kz/almaty/firm/sync-test",
    )
    db_session.add(org)
    await db_session.flush()

    stats = await sync_external_reviews_for_org(
        db_session,
        int(org.id),
        fixture_paths={"2gis": str(GIS_HTML)},
    )
    await db_session.commit()

    assert stats["skipped"] is False
    assert stats["parsed"] == 3
    assert stats["inserted"] == 3
    rows = (
        await db_session.execute(
            select(ExternalReview).where(ExternalReview.organization_id == org.id),
        )
    ).scalars().all()
    assert len(rows) == 3
    assert all(r.organization_id == org.id for r in rows)
    sources = {r.source for r in rows}
    assert sources == {"2gis"}


@pytest.mark.asyncio
async def test_sync_dedupes_on_second_run(db_session) -> None:
    from sqlalchemy import func, select

    from app.db.models import ExternalReview, Organization
    from app.services.external_reviews_sync import sync_external_reviews_for_org

    org = Organization(
        name="GuestCare Dedupe Org",
        slug="guestcare-dedupe-org",
        review_url_2gis="https://2gis.kz/almaty/firm/dedupe",
    )
    db_session.add(org)
    await db_session.flush()

    await sync_external_reviews_for_org(
        db_session,
        int(org.id),
        fixture_paths={"2gis": str(GIS_HTML)},
    )
    await db_session.commit()

    stats2 = await sync_external_reviews_for_org(
        db_session,
        int(org.id),
        fixture_paths={"2gis": str(GIS_HTML)},
    )
    await db_session.commit()

    assert stats2["inserted"] == 0
    assert stats2["updated"] == 3


@pytest.mark.asyncio
async def test_sync_skips_google_only_org(db_session) -> None:
    from app.db.models import Organization
    from app.services.external_reviews_sync import sync_external_reviews_for_org

    org = Organization(
        name="Google Only Org",
        slug="google-only-org",
        meta_json={"review_url_google": "https://www.google.com/maps/place/test"},
    )
    db_session.add(org)
    await db_session.flush()

    stats = await sync_external_reviews_for_org(db_session, int(org.id))
    assert stats["skipped"] is True
    assert stats["reason"] == "google_manual_only"
    assert stats["parsed"] == 0


@pytest.mark.asyncio
async def test_sync_reports_2gis_antibot_museum_redirect(db_session) -> None:
    from app.db.models import Organization
    from app.services.external_reviews_sync import sync_external_reviews_for_org

    org = Organization(
        name="2GIS Antibot Org",
        slug="2gis-antibot-org",
        review_url_2gis="https://2gis.kz/pavlodar/firm/70000001110142368",
    )
    db_session.add(org)
    await db_session.flush()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/museum":
            return httpx.Response(200, text="<html><title>2GIS</title></html>", request=request)
        return httpx.Response(
            302,
            headers={"Location": "https://2gis.kz/museum?return_url=https%3A%2F%2F2gis.kz%2Fpavlodar%2Ffirm%2F70000001110142368"},
            request=request,
        )

    stats = await sync_external_reviews_for_org(
        db_session,
        int(org.id),
        transport=httpx.MockTransport(handler),
    )

    assert stats["ok"] is False
    assert stats["parsed"] == 0
    assert stats["errors"][0]["code"] == "2gis_antibot"
