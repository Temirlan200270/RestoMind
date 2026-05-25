"""FAQ-кеш LLM-ответов в Redis (org-scoped)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import date

from app.core.config import settings
from app.db.session import redis_client
from app.schemas.ai_schemas import AIBrainResponse

logger = logging.getLogger(__name__)


def kb_fingerprint_from_text(kb_text: str) -> str:
    raw = (kb_text or "").strip()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def normalize_faq_question(text: str) -> str:
    s = re.sub(r"[^\wа-яёА-ЯЁ\s]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def _faq_cache_key(org_id: int, normalized_question: str) -> str:
    h = hashlib.sha256(normalized_question.encode("utf-8")).hexdigest()[:16]
    return f"rm:faq_cache:{int(org_id)}:{h}"


def _metric_key(org_id: int, kind: str) -> str:
    day = date.today().isoformat()
    return f"rm:metrics:faq_cache:{kind}:{int(org_id)}:{day}"


async def _bump_metric(org_id: int, kind: str) -> None:
    if not settings.redis_enabled:
        return
    try:
        key = _metric_key(org_id, kind)
        await redis_client.incr(key)
        await redis_client.expire(key, 7 * 86400)
    except Exception as exc:
        logger.debug("faq_cache metric incr failed org=%s kind=%s: %s", org_id, kind, exc)


async def get_cached_faq_reply(
    *,
    org_id: int,
    message_text: str,
    kb_fingerprint: str,
) -> str | None:
    if not settings.faq_cache_enabled or not settings.redis_enabled:
        return None
    norm = normalize_faq_question(message_text)
    if len(norm) < 5 or len(norm) > 100:
        return None
    key = _faq_cache_key(org_id, norm)
    try:
        raw = await redis_client.get(key)
    except Exception as exc:
        logger.warning("faq_cache get failed org=%s: %s", org_id, exc)
        return None
    if not raw:
        await _bump_metric(org_id, "miss")
        return None
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        data = json.loads(raw)
    except Exception:
        await _bump_metric(org_id, "miss")
        return None
    if data.get("kb_fp") != kb_fingerprint:
        await _bump_metric(org_id, "miss")
        return None
    reply = str(data.get("reply") or "").strip()
    if not reply:
        await _bump_metric(org_id, "miss")
        return None
    await _bump_metric(org_id, "hit")
    logger.debug("faq_cache hit org=%s key=%s", org_id, key[-20:])
    return reply


async def save_faq_reply(
    *,
    org_id: int,
    message_text: str,
    kb_fingerprint: str,
    reply: str,
    ttl_sec: int | None = None,
) -> None:
    if not settings.faq_cache_enabled or not settings.redis_enabled:
        return
    norm = normalize_faq_question(message_text)
    if len(norm) < 5 or len(norm) > 100:
        return
    text = (reply or "").strip()
    if not text or len(text) > 600:
        return
    key = _faq_cache_key(org_id, norm)
    payload = json.dumps(
        {"reply": text, "kb_fp": kb_fingerprint, "ts": int(time.time())},
        ensure_ascii=False,
    )
    ttl = int(ttl_sec if ttl_sec is not None else settings.faq_cache_ttl_sec)
    try:
        await redis_client.setex(key, max(300, ttl), payload)
        await _bump_metric(org_id, "save")
        logger.debug("faq_cache save org=%s key=%s", org_id, key[-20:])
    except Exception as exc:
        logger.warning("faq_cache set failed org=%s: %s", org_id, exc)


def should_save_faq_reply(
    ai_response: AIBrainResponse,
    *,
    has_draft: bool,
) -> bool:
    if ai_response.intent != "faq":
        return False
    if has_draft:
        return False
    if ai_response.items or ai_response.order_actions:
        return False
    if ai_response.is_recommendation:
        return False
    if ai_response.upsell_offered or ai_response.upsell_offered_id:
        return False
    reply = (ai_response.reply_text or "").strip()
    if not reply or len(reply) > 600:
        return False
    return True
