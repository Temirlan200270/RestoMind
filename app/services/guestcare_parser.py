"""GuestCare External — conservative parsing of public review pages (2GIS / Google).

We do not run headless browsers or aggressive scraping. Strategies (in order):
1. ``application/ld+json`` blocks with ``@type: Review``
2. Embedded JSON blobs (``__INITIAL_STATE__``, ``__NEXT_DATA__``, etc.)
3. Shallow recursive scan for review-shaped dicts

Google Maps has no stable public HTML API without Places API credentials — see
``parse_google_page`` limitations in docstring and ``GOOGLE_REVIEWS_LIMITATION``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

GOOGLE_REVIEWS_LIMITATION = (
    "Google Reviews: без Google Places API (ключ + billing) автоматический импорт "
    "с публичной страницы Maps ненадёжен и может нарушать ToS. "
    "Укажите review_url_google в meta_json организации — sync попытается JSON-LD/microdata; "
    "для production рекомендуется официальный Places API."
)

_JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
_EMBEDDED_JSON_RE = re.compile(
    r"(?:window\.)?(?:__INITIAL_STATE__|__NEXT_DATA__|__NUXT__)\s*=\s*(\{.*?\})\s*;?\s*</script>",
    re.S,
)
_REVIEW_ID_KEYS = ("id", "reviewId", "review_id", "uuid", "external_id")
_TEXT_KEYS = ("text", "reviewBody", "body", "comment", "content", "message")
_AUTHOR_KEYS = ("author", "user", "userName", "user_name", "name")
_RATING_KEYS = ("rating", "score", "stars", "reviewRating", "review_rating")
_DATE_KEYS = ("datePublished", "date", "createdAt", "created_at", "published_at")


@dataclass(frozen=True)
class ParsedExternalReview:
    external_id: str
    source: str
    url: str
    author: str
    rating: int | None
    text: str
    published_at: str | None = None

    def as_import_dict(self) -> dict[str, Any]:
        return {
            "id": self.external_id,
            "source": self.source,
            "url": self.url,
            "author": self.author,
            "rating": self.rating,
            "text": self.text,
            "published_at": self.published_at,
            "reply_draft": None,
        }


def detect_review_source(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if "2gis" in host:
        return "2gis"
    if "google" in host or "g.page" in host or "maps.app.goo.gl" in host:
        return "google"
    return "external"


def _stable_external_id(source: str, raw_id: str | None, *, author: str, text: str, rating: int | None) -> str:
    if raw_id and str(raw_id).strip():
        return str(raw_id).strip()[:120]
    digest = hashlib.sha256(
        f"{source}|{author}|{text}|{rating or ''}".encode("utf-8"),
    ).hexdigest()[:16]
    return digest


def _coerce_rating(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("ratingValue", "value", "rating"):
            if key in value:
                return _coerce_rating(value[key])
        return None
    try:
        rating = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    if 1 <= rating <= 5:
        return rating
    return None


def _first_str(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            for sub in _AUTHOR_KEYS:
                if sub in val and isinstance(val[sub], str) and val[sub].strip():
                    return val[sub].strip()
    return ""


def _looks_like_review(obj: dict[str, Any]) -> bool:
    if not isinstance(obj, dict):
        return False
    text = _first_str(obj, _TEXT_KEYS)
    if len(text) < 8:
        return False
    has_rating = _coerce_rating(
        next((obj.get(k) for k in _RATING_KEYS if k in obj), None),
    ) is not None
    has_author = bool(_first_str(obj, _AUTHOR_KEYS))
    has_id = any(obj.get(k) for k in _REVIEW_ID_KEYS)
    return has_rating or has_author or has_id


def _dict_to_review(obj: dict[str, Any], *, source: str, page_url: str) -> ParsedExternalReview | None:
    if not _looks_like_review(obj):
        return None
    text = _first_str(obj, _TEXT_KEYS)
    author = _first_str(obj, _AUTHOR_KEYS) or "Гость"
    rating = _coerce_rating(next((obj.get(k) for k in _RATING_KEYS if k in obj), None))
    raw_id = next((str(obj[k]) for k in _REVIEW_ID_KEYS if obj.get(k) is not None), None)
    published = _first_str(obj, _DATE_KEYS) or None
    external_id = _stable_external_id(source, raw_id, author=author, text=text, rating=rating)
    review_url = page_url
    if raw_id and page_url:
        review_url = f"{page_url.rstrip('/')}#review-{raw_id}"
    return ParsedExternalReview(
        external_id=external_id,
        source=source,
        url=review_url,
        author=author,
        rating=rating,
        text=text,
        published_at=published,
    )


def _walk_for_reviews(node: Any, *, source: str, page_url: str, out: list[ParsedExternalReview]) -> None:
    if isinstance(node, dict):
        atype = node.get("@type") or node.get("type")
        if atype == "Review" or (isinstance(atype, str) and "review" in atype.lower()):
            parsed = _dict_to_review(node, source=source, page_url=page_url)
            if parsed is not None:
                out.append(parsed)
        if _looks_like_review(node):
            parsed = _dict_to_review(node, source=source, page_url=page_url)
            if parsed is not None:
                out.append(parsed)
        for val in node.values():
            _walk_for_reviews(val, source=source, page_url=page_url, out=out)
    elif isinstance(node, list):
        for item in node:
            _walk_for_reviews(item, source=source, page_url=page_url, out=out)


def _dedupe_reviews(items: list[ParsedExternalReview]) -> list[ParsedExternalReview]:
    seen: set[str] = set()
    unique: list[ParsedExternalReview] = []
    for item in items:
        key = f"{item.source}:{item.external_id}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _parse_json_ld(html: str, *, source: str, page_url: str) -> list[ParsedExternalReview]:
    found: list[ParsedExternalReview] = []
    for block in _JSON_LD_RE.findall(html):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            reviews = node.get("review")
            if isinstance(reviews, list):
                for rev in reviews:
                    _walk_for_reviews(rev, source=source, page_url=page_url, out=found)
            elif isinstance(reviews, dict):
                _walk_for_reviews(reviews, source=source, page_url=page_url, out=found)
            _walk_for_reviews(node, source=source, page_url=page_url, out=found)
    return found


def _parse_embedded_json(html: str, *, source: str, page_url: str) -> list[ParsedExternalReview]:
    found: list[ParsedExternalReview] = []
    for raw in _EMBEDDED_JSON_RE.findall(html):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        _walk_for_reviews(data, source=source, page_url=page_url, out=found)
    return found


def parse_2gis_page(html: str, page_url: str) -> list[ParsedExternalReview]:
    """Parse reviews from a 2GIS firm page HTML snapshot."""
    source = "2gis"
    items: list[ParsedExternalReview] = []
    items.extend(_parse_json_ld(html, source=source, page_url=page_url))
    items.extend(_parse_embedded_json(html, source=source, page_url=page_url))
    if not items:
        logger.info("parse_2gis_page: no reviews extracted from %s", page_url)
    return _dedupe_reviews(items)


def parse_google_page(html: str, page_url: str) -> list[ParsedExternalReview]:
    """
    Best-effort parse for Google Maps place pages.

    Often returns an empty list: Maps renders reviews client-side and blocks bots.
    Production ingestion should use Google Places API (``reviews`` field) with an API key.
    """
    source = "google"
    items: list[ParsedExternalReview] = []
    items.extend(_parse_json_ld(html, source=source, page_url=page_url))
    items.extend(_parse_embedded_json(html, source=source, page_url=page_url))
    if not items:
        logger.info(
            "parse_google_page: no reviews (expected without Places API): %s — %s",
            page_url,
            GOOGLE_REVIEWS_LIMITATION,
        )
    return _dedupe_reviews(items)


def parse_reviews_from_html(html: str, page_url: str) -> list[ParsedExternalReview]:
    """Dispatch parser by URL host."""
    source = detect_review_source(page_url)
    if source == "2gis":
        return parse_2gis_page(html, page_url)
    if source == "google":
        return parse_google_page(html, page_url)
    return _dedupe_reviews(
        _parse_json_ld(html, source=source, page_url=page_url)
        + _parse_embedded_json(html, source=source, page_url=page_url),
    )
