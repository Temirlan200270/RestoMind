"""GuestCare External — fetch public review pages and upsert ``external_reviews``."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING
from urllib.parse import urlparse, urlunparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import ExternalReview, Organization
from app.services.guestcare_parser import (
    GOOGLE_REVIEWS_LIMITATION,
    ParsedExternalReview,
    detect_review_source,
    parse_reviews_from_html,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_FETCH_HEADERS = {
    "User-Agent": "RestoMind-GuestCare/1.0 (+https://restomind.app; review-sync)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}
_FETCH_TIMEOUT = 20.0


class ExternalReviewFetchError(RuntimeError):
    """Public review page could not be fetched in a parseable form."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def _is_2gis_antibot_url(url: str) -> bool:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/")
    return "2gis" in host and path == "/museum"


def org_review_sources(org: Organization) -> dict[str, str]:
    """Return configured review page URLs (all sources, for display/import)."""
    urls: dict[str, str] = {}
    gis = (getattr(org, "review_url_2gis", None) or "").strip()
    if gis:
        urls["2gis"] = gis
    meta = org.meta_json if isinstance(org.meta_json, dict) else {}
    google = str(meta.get("review_url_google") or meta.get("guestcare_google_url") or "").strip()
    if google:
        urls["google"] = google
    return urls


def normalize_2gis_reviews_url(url: str) -> str:
    """Prefer the review tab for 2GIS firm pages; it exposes more review HTML."""
    raw = (url or "").strip()
    if not raw:
        return raw
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/")
    if "2gis" not in host or "/firm/" not in path or path.endswith("/tab/reviews"):
        return raw
    normalized = parsed._replace(path=f"{path}/tab/reviews")
    return urlunparse(normalized)


def org_review_auto_sync_sources(org: Organization) -> dict[str, str]:
    """URLs for cron + «Синхронизировать» — 2GIS only (Google Places API not in scope)."""
    gis = (getattr(org, "review_url_2gis", None) or "").strip()
    if not gis:
        return {}
    return {"2gis": normalize_2gis_reviews_url(gis)}


async def fetch_review_page_html(
    url: str,
    *,
    fixture_path: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    if fixture_path:
        from pathlib import Path

        return Path(fixture_path).read_text(encoding="utf-8")
    async with httpx.AsyncClient(
        timeout=_FETCH_TIMEOUT,
        headers=_FETCH_HEADERS,
        follow_redirects=True,
        transport=transport,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        if _is_2gis_antibot_url(str(response.url)):
            raise ExternalReviewFetchError(
                "2GIS вернул защитную страницу /museum вместо страницы организации. "
                "Публичный HTML сейчас не содержит отзывы; для авто-импорта нужен официальный/партнёрский источник "
                "или scraper-интеграция.",
                code="2gis_antibot",
            )
        return response.text


async def upsert_parsed_review(
    db: AsyncSession,
    organization_id: int,
    parsed: ParsedExternalReview,
) -> tuple[ExternalReview, bool]:
    """Insert or update review row. Returns (row, created)."""
    row = await db.scalar(
        select(ExternalReview).where(
            ExternalReview.organization_id == organization_id,
            ExternalReview.source == parsed.source,
            ExternalReview.external_id == parsed.external_id,
        )
    )
    created = row is None
    if row is None:
        row = ExternalReview(
            organization_id=organization_id,
            source=parsed.source,
            external_id=parsed.external_id,
            url=parsed.url,
        )
        db.add(row)
    row.author = parsed.author
    row.rating = parsed.rating
    row.text = parsed.text
    row.url = parsed.url
    row.payload_json = parsed.as_import_dict()
    row.status = row.status or "new"
    return row, created


async def sync_external_reviews_for_org(
    db: AsyncSession,
    organization_id: int,
    *,
    fixture_paths: dict[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """
    Fetch configured 2GIS/Google pages and upsert reviews for one organization.

    ``fixture_paths`` — test-only map ``{"2gis": "/path/to.html"}``.
    """
    org = await db.get(Organization, organization_id)
    if org is None:
        raise ValueError(f"Organization {organization_id} not found")

    sources = org_review_auto_sync_sources(org)
    if not sources:
        has_google_only = bool(org_review_sources(org)) and not (getattr(org, "review_url_2gis", None) or "").strip()
        reason = "google_manual_only" if has_google_only else "no_review_urls"
        return {
            "ok": True,
            "organization_id": organization_id,
            "skipped": True,
            "reason": reason,
            "sources": {},
            "inserted": 0,
            "updated": 0,
            "parsed": 0,
            "errors": [],
            "limitations": [GOOGLE_REVIEWS_LIMITATION],
        }

    inserted = 0
    updated = 0
    parsed_total = 0
    errors: list[dict[str, str]] = []
    per_source: dict[str, Any] = {}

    for label, page_url in sources.items():
        source_key = detect_review_source(page_url)
        fixture = (fixture_paths or {}).get(label) or (fixture_paths or {}).get(source_key)
        try:
            html = await fetch_review_page_html(
                page_url,
                fixture_path=fixture,
                transport=transport,
            )
            reviews = parse_reviews_from_html(html, page_url)
            parsed_total += len(reviews)
            src_inserted = 0
            src_updated = 0
            for item in reviews:
                _row, created = await upsert_parsed_review(db, organization_id, item)
                if created:
                    src_inserted += 1
                else:
                    src_updated += 1
            inserted += src_inserted
            updated += src_updated
            per_source[label] = {
                "url": page_url,
                "parsed": len(reviews),
                "inserted": src_inserted,
                "updated": src_updated,
            }
        except Exception as exc:
            error_code = getattr(exc, "code", "fetch_or_parse_failed")
            logger.exception(
                "sync_external_reviews_for_org org=%s source=%s",
                organization_id,
                label,
            )
            errors.append({"source": label, "url": page_url, "error": str(exc), "code": str(error_code)})
            per_source[label] = {"url": page_url, "error": str(exc), "code": str(error_code)}

    meta = dict(org.meta_json or {}) if isinstance(org.meta_json, dict) else {}
    meta["guestcare_sync"] = {
        "last_at": datetime.now(tz=timezone.utc).isoformat(),
        "inserted": inserted,
        "updated": updated,
        "parsed": parsed_total,
        "per_source": per_source,
        "errors": errors,
    }
    org.meta_json = meta

    result = {
        "ok": len(errors) == 0,
        "organization_id": organization_id,
        "skipped": False,
        "sources": per_source,
        "inserted": inserted,
        "updated": updated,
        "parsed": parsed_total,
        "errors": errors,
        "limitations": [GOOGLE_REVIEWS_LIMITATION],
    }
    if parsed_total == 0 and not errors:
        result["warnings"] = [
            "Страница 2GIS открылась, но тексты отзывов не извлечены. Проверьте, что ссылка ведёт на /tab/reviews; "
            "если разметка 2GIS изменилась, нужно обновить парсер.",
        ]
    logger.info(
        "sync_external_reviews_for_org org=%s inserted=%s updated=%s parsed=%s",
        organization_id,
        inserted,
        updated,
        parsed_total,
    )
    return result


async def list_organizations_with_review_urls(db: AsyncSession) -> list[Organization]:
    rows = (await db.execute(
        select(Organization).where(Organization.is_active.is_(True)),
    )).scalars().all()
    out: list[Organization] = []
    for org in rows:
        if org_review_auto_sync_sources(org):
            out.append(org)
    return out


async def run_external_reviews_scheduled_sync() -> None:
    """Cron: sync external reviews for all orgs with configured URLs."""
    if not settings.guestcare_sync_enabled:
        logger.debug("run_external_reviews_scheduled_sync: disabled")
        return
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        orgs = await list_organizations_with_review_urls(db)
    for org in orgs:
        try:
            async with async_session_factory() as db:
                await sync_external_reviews_for_org(db, int(org.id))
                await db.commit()
        except Exception:
            logger.exception(
                "run_external_reviews_scheduled_sync: org_id=%s",
                org.id,
            )
    logger.info(
        "run_external_reviews_scheduled_sync: %d orgs processed",
        len(orgs),
    )
