"""
Клиент WhatsApp (Meta Cloud API).
Автоматически переключается между режимами:
- Если WHATSAPP_API_TOKEN задан → реальная отправка через Meta Graph API
- Если пуст → логирование в консоль (для разработки)
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings


@dataclass(frozen=True)
class WhatsAppSendResult:
    """Результат отправки текстового сообщения (для трекинга wamid и ошибок)."""

    ok: bool
    message_id: str | None = None
    error: dict[str, Any] | None = None

logger = logging.getLogger(__name__)


def _strip_markdown_for_whatsapp(text: str) -> str:
    """
    Привести текст к тому, что WhatsApp покажет читабельно.

    Модели часто копируют Markdown (**жирный**, ## заголовок). В WhatsApp
    жирный — только *одинарные* звёздочки; двойные остаются буквально.
    Убираем/нормализуем распространённый мусор без HTML-парсера.
    """
    s = (text or "").replace("\r\n", "\n")
    if not s.strip():
        return s

    # **bold** или **bold * mix ** → *bold* (итеративно, вложенные пары)
    for _ in range(20):
        prev = s
        s = re.sub(r"\*\*([^*]+)\*\*", r"*\1*", s)
        if s == prev:
            break

    # Оставшиеся «висячие» **
    s = s.replace("**", "")

    # __underline__ → _underline_ (WA поддерживает курсив одинарным _)
    s = re.sub(r"__([^_]+)__", r"_\1_", s)

    # Заголовки markdown: # ... до конца строки
    s = re.sub(r"(?m)^#{1,6}\s*", "", s)

    # Блоки ```...``` — раньше инлайн-кода `...`, иначе `` внутри фенса ломают разбор.
    s = re.sub(r"```(?:\w+)?(?:\r?\n|\s)?([\s\S]*?)```", r"\1", s)

    # Инлайн-код `...`
    s = re.sub(r"`([^`]+)`", r"\1", s)

    # Ссылки [label](url) → label (url) или только label
    def _link_repl(m: re.Match[str]) -> str:
        label = (m.group(1) or "").strip()
        url = (m.group(2) or "").strip()
        if url and label:
            return f"{label} ({url})"
        return label or url

    s = re.sub(r"\[([^\]]*)\]\(([^)]*)\)", _link_repl, s)

    return s


def _graph_api_version() -> str:
    return (getattr(settings, "whatsapp_api_version", "") or "v21.0").strip() or "v21.0"


def _messages_url() -> str:
    pnid = (settings.whatsapp_phone_number_id or "").strip()
    return f"https://graph.facebook.com/{_graph_api_version()}/{pnid}/messages"


def _media_upload_url() -> str:
    pnid = (settings.whatsapp_phone_number_id or "").strip()
    return f"https://graph.facebook.com/{_graph_api_version()}/{pnid}/media"

SEND_TIMEOUT = 10.0
MEDIA_TIMEOUT = 60.0
MAX_RETRIES = 3
BACKOFF_CAP_SECONDS = 8.0

_http_client: httpx.AsyncClient | None = None
_http_lock: asyncio.Lock | None = None


def _retry_delay_seconds(attempt_index: int) -> float:
    """Экспоненциальная задержка перед следующей попыткой (attempt_index = номер неудачной попытки, с 1)."""
    base = min(2.0 ** (attempt_index - 1), BACKOFF_CAP_SECONDS)
    return base + random.uniform(0, 0.25)


async def _ensure_http_client() -> httpx.AsyncClient:
    """Один AsyncClient на процесс: пул соединений + корректное закрытие из lifespan."""
    global _http_client, _http_lock
    if _http_lock is None:
        _http_lock = asyncio.Lock()
    async with _http_lock:
        if _http_client is None or _http_client.is_closed:
            _http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(MEDIA_TIMEOUT, connect=15.0),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            )
        return _http_client


async def init_whatsapp_http_client() -> None:
    """Прогрев клиента при старте приложения (опционально)."""
    if (settings.whatsapp_api_token or "").strip():
        await _ensure_http_client()


async def close_whatsapp_http_client() -> None:
    """Закрыть HTTP-клиент WhatsApp (вызывать из lifespan shutdown)."""
    global _http_client
    if _http_lock is None:
        return
    async with _http_lock:
        if _http_client is not None and not _http_client.is_closed:
            await _http_client.aclose()
        _http_client = None


def _digits_only(phone: str) -> str:
    return re.sub(r"\D+", "", phone or "").strip()


def _whatsapp_to_candidates(phone: str) -> list[str]:
    """
    Кандидаты для поля `to` (только цифры).

    1) Как в вебхуке — корректный E.164 для KZ: 7705… (11 цифр).
    2) Запасной `78705…`: в панели Meta тестовый список получателей иногда сохраняет
       номер как «+7 8 (705)…» → allow list совпадает только с 12-значным `78…`.
       При 131030 send_* переключается на следующего кандидата (см. break в циклах).
    """
    d = _digits_only(phone)
    if not d:
        return []
    out: list[str] = [d]
    if len(d) == 11 and d.startswith("77"):
        alt = "78" + d[1:]
        if alt not in out:
            out.append(alt)
    return out


def _is_recipient_not_allowed(resp: httpx.Response) -> bool:
    try:
        payload = resp.json() or {}
        err = payload.get("error") or {}
        return int(err.get("code") or 0) == 131030
    except Exception:
        return False


def _parse_send_error_payload(resp: httpx.Response) -> dict[str, Any]:
    try:
        payload = resp.json()
        if isinstance(payload, dict) and payload.get("error"):
            err = payload["error"]
            return err if isinstance(err, dict) else {"raw": err}
        return {"http_status": resp.status_code, "body": resp.text[:500]}
    except Exception:
        return {"http_status": resp.status_code, "body": resp.text[:500]}


def _recipient_not_allowed(response: httpx.Response) -> bool:
    return response.status_code == 400 and _is_recipient_not_allowed(response)


async def _post_whatsapp_messages(
    *,
    candidates: list[str],
    headers: dict[str, str],
    build_json: Callable[[str], dict[str, Any]],
    timeout: float,
    extract_wamid: bool,
    log_prefix: str,
) -> WhatsAppSendResult:
    """
    Общий POST на /messages: кандидаты номера, 131030 → следующий номер,
    между повторными попытками — exponential backoff + jitter.
    """
    last_err: dict[str, Any] | None = None
    client = await _ensure_http_client()

    for to in candidates:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = await client.post(
                    _messages_url(),
                    headers=headers,
                    json=build_json(to),
                    timeout=timeout,
                )
            except httpx.TimeoutException:
                last_err = {"code": "timeout", "to": to, "attempt": attempt}
                logger.error(
                    "%s: таймаут (to=%s, попытка %d/%d)",
                    log_prefix,
                    to,
                    attempt,
                    MAX_RETRIES,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(_retry_delay_seconds(attempt))
                continue
            except httpx.HTTPError as exc:
                last_err = {"code": "network", "detail": str(exc)[:300]}
                logger.error(
                    "%s: сеть (to=%s, попытка %d/%d): %s",
                    log_prefix,
                    to,
                    attempt,
                    MAX_RETRIES,
                    exc,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(_retry_delay_seconds(attempt))
                continue

            if response.status_code == 200:
                wamid: str | None = None
                if extract_wamid:
                    try:
                        payload = response.json()
                        msgs = payload.get("messages") if isinstance(payload, dict) else None
                        if isinstance(msgs, list) and msgs and isinstance(msgs[0], dict):
                            mid = msgs[0].get("id")
                            if mid:
                                wamid = str(mid)
                    except Exception:
                        wamid = None
                logger.info("%s: сообщение принято API -> %s", log_prefix, to)
                return WhatsAppSendResult(ok=True, message_id=wamid)

            last_err = _parse_send_error_payload(response)
            logger.error(
                "%s: ошибка %d (to=%s, попытка %d/%d): %s",
                log_prefix,
                response.status_code,
                to,
                attempt,
                MAX_RETRIES,
                response.text[:200],
            )
            if _recipient_not_allowed(response):
                break
            if response.status_code == 429 and attempt < MAX_RETRIES:
                retry_after = (response.headers.get("Retry-After") or "").strip()
                try:
                    ra_s = float(retry_after) if retry_after else 0.0
                except Exception:
                    ra_s = 0.0
                sleep_s = max(ra_s, _retry_delay_seconds(attempt) * 2)
                await asyncio.sleep(min(sleep_s, BACKOFF_CAP_SECONDS * 2))
                continue
            if attempt < MAX_RETRIES:
                await asyncio.sleep(_retry_delay_seconds(attempt))

    logger.error(
        "%s: не удалось отправить после %d попыток на номер(а)",
        log_prefix,
        MAX_RETRIES,
    )
    return WhatsAppSendResult(ok=False, error=last_err or {"code": "unknown"})


async def send_message(phone: str, text: str) -> WhatsAppSendResult:
    """
    Отправить текстовое сообщение клиенту в WhatsApp.
    Если токен не настроен — логирует (режим разработки) и возвращает ok без wamid.
    """
    from app.services.trace_context import trace_log_prefix

    text = _strip_markdown_for_whatsapp(text)
    trace_prefix = trace_log_prefix()
    if not settings.whatsapp_api_token:
        logger.info("%sWhatsApp (dev, no token) -> %s: %s", trace_prefix, phone, text[:200])
        return WhatsAppSendResult(ok=True, message_id=None)

    candidates = _whatsapp_to_candidates(phone)
    if not candidates:
        logger.error("WhatsApp: пустой номер получателя")
        return WhatsAppSendResult(ok=False, error={"code": "empty_recipient"})

    headers = {
        "Authorization": f"Bearer {settings.whatsapp_api_token}",
        "Content-Type": "application/json",
    }

    def _body(to: str) -> dict[str, Any]:
        return {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }

    return await _post_whatsapp_messages(
        candidates=candidates,
        headers=headers,
        build_json=_body,
        timeout=SEND_TIMEOUT,
        extract_wamid=True,
        log_prefix="WhatsApp",
    )


async def send_interactive_buttons(
    phone: str,
    body_text: str,
    buttons: list[dict],
) -> WhatsAppSendResult:
    """
    Отправить сообщение с quick-reply кнопками (Meta interactive/button).
    buttons: список {"id": str, "title": str}, максимум 3 штуки.
    Без токена (dev) — fallback: текст + подсказка вариантов.
    """
    if not settings.whatsapp_api_token:
        labels = " / ".join(b.get("title", b.get("id", "")) for b in buttons[:3])
        logger.info("WhatsApp (dev) interactive_buttons -> %s: %s [%s]", phone, body_text[:120], labels)
        return WhatsAppSendResult(ok=True, message_id=None)

    candidates = _whatsapp_to_candidates(phone)
    if not candidates:
        return WhatsAppSendResult(ok=False, error={"code": "empty_recipient"})

    headers = {
        "Authorization": f"Bearer {settings.whatsapp_api_token}",
        "Content-Type": "application/json",
    }
    safe_buttons = buttons[:3]

    def _body(to: str) -> dict[str, Any]:
        return {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": _strip_markdown_for_whatsapp(body_text)},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}}
                        for b in safe_buttons
                    ]
                },
            },
        }

    return await _post_whatsapp_messages(
        candidates=candidates,
        headers=headers,
        build_json=_body,
        timeout=SEND_TIMEOUT,
        extract_wamid=True,
        log_prefix="WhatsApp Interactive",
    )


async def send_cta_url_button(
    phone: str,
    body_text: str,
    button_title: str,
    url: str,
) -> WhatsAppSendResult:
    """
    Отправить сообщение с CTA-кнопкой-ссылкой (Meta interactive/cta_url).
    Используется для платёжных ссылок (E14).
    Без токена — fallback: текст + URL на новой строке.
    """
    if not settings.whatsapp_api_token:
        logger.info("WhatsApp (dev) cta_url -> %s: %s → %s", phone, body_text[:80], url)
        return WhatsAppSendResult(ok=True, message_id=None)

    candidates = _whatsapp_to_candidates(phone)
    if not candidates:
        return WhatsAppSendResult(ok=False, error={"code": "empty_recipient"})

    headers = {
        "Authorization": f"Bearer {settings.whatsapp_api_token}",
        "Content-Type": "application/json",
    }

    def _body(to: str) -> dict[str, Any]:
        return {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "cta_url",
                "body": {"text": _strip_markdown_for_whatsapp(body_text)},
                "action": {
                    "name": "cta_url",
                    "parameters": {
                        "display_text": button_title[:20],
                        "url": url,
                    },
                },
            },
        }

    return await _post_whatsapp_messages(
        candidates=candidates,
        headers=headers,
        build_json=_body,
        timeout=SEND_TIMEOUT,
        extract_wamid=True,
        log_prefix="WhatsApp CTA",
    )


async def send_template(
    phone: str,
    template_name: str,
    language_code: str = "ru",
    parameters: list[str] | None = None,
) -> bool:
    """
    Отправить шаблонное (template) сообщение через WhatsApp.
    Шаблоны нужны для проактивных уведомлений (заказ готов, статус брони и т.д.).
    Шаблон должен быть предварительно одобрен в Meta Business.

    Args:
        phone: Номер получателя.
        template_name: Имя шаблона в Meta (например, 'order_ready').
        language_code: Код языка шаблона.
        parameters: Подстановочные значения ({{1}}, {{2}}, ...).
    """
    if not settings.whatsapp_api_token:
        params_str = ", ".join(parameters) if parameters else "—"
        logger.info("📤 [WhatsApp Template → %s]: %s (%s)", phone, template_name, params_str)
        return True

    components: list[dict] = []
    if parameters:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": p} for p in parameters],
        })

    payload = {
        "messaging_product": "whatsapp",
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
        },
    }
    if components:
        payload["template"]["components"] = components

    candidates = _whatsapp_to_candidates(phone)
    if not candidates:
        logger.error("WhatsApp template: пустой номер получателя")
        return False

    headers = {
        "Authorization": f"Bearer {settings.whatsapp_api_token}",
        "Content-Type": "application/json",
    }

    def _body(to: str) -> dict[str, Any]:
        body = {**payload, "to": to}
        return body

    result = await _post_whatsapp_messages(
        candidates=candidates,
        headers=headers,
        build_json=_body,
        timeout=SEND_TIMEOUT,
        extract_wamid=False,
        log_prefix=f"WhatsApp template '{template_name}'",
    )
    if result.ok:
        return True
    return False


async def download_media_bytes(media_id: str) -> tuple[bytes, str] | None:
    """
    Скачивает бинарные данные вложения WhatsApp по ID из вебхука.

    Returns:
        (bytes, mime_type) или None при ошибке.
    """
    token = (settings.whatsapp_api_token or "").strip()
    if not token or not media_id:
        logger.warning("WhatsApp media: нет токена или media_id")
        return None

    meta_url = f"https://graph.facebook.com/{_graph_api_version()}/{media_id}"

    try:
        client = await _ensure_http_client()
        r = await client.get(
            meta_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=MEDIA_TIMEOUT,
        )
        if r.status_code != 200:
            logger.error(
                "WhatsApp media meta: HTTP %s — %s",
                r.status_code,
                r.text[:300],
            )
            return None
        payload = r.json()
        download_url = payload.get("url")
        mime_type = (payload.get("mime_type") or "application/octet-stream").split(";")[0].strip()
        if not download_url:
            logger.error("WhatsApp media meta: нет поля url")
            return None

        r2 = await client.get(
            download_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=MEDIA_TIMEOUT,
        )
        if r2.status_code != 200:
            logger.error(
                "WhatsApp media download: HTTP %s",
                r2.status_code,
            )
            return None
        return (r2.content, mime_type)
    except httpx.HTTPError as exc:
        logger.error("WhatsApp media: ошибка загрузки: %s", exc)
        return None


async def upload_media_bytes(
    file_bytes: bytes,
    mime_type: str,
    filename: str = "audio.mp3",
) -> str | None:
    """
    Загружает файл в WhatsApp Cloud API, возвращает media id для отправки в сообщении.
    """
    token = (settings.whatsapp_api_token or "").strip()
    if not token or not file_bytes:
        logger.warning("upload_media: нет токена или пустой файл")
        return None

    client = await _ensure_http_client()
    last_exc: BaseException | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        response: httpx.Response | None = None
        try:
            response = await client.post(
                _media_upload_url(),
                headers={"Authorization": f"Bearer {token}"},
                files={
                    "file": (filename, file_bytes, mime_type),
                },
                data={
                    "messaging_product": "whatsapp",
                    "type": mime_type,
                },
                timeout=MEDIA_TIMEOUT,
            )
            if response.status_code == 200:
                mid = (response.json() or {}).get("id")
                if mid:
                    return str(mid)
                logger.error("upload_media: нет id в ответе: %s", response.text[:200])
                return None
            logger.error(
                "upload_media HTTP %s (попытка %d/%d): %s",
                response.status_code,
                attempt,
                MAX_RETRIES,
                response.text[:200],
            )
            # 4xx (кроме 429) обычно не имеет смысла ретраить.
            if 400 <= response.status_code < 500 and response.status_code != 429:
                break
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            last_exc = exc
            logger.error("upload_media ошибка (попытка %d/%d): %s", attempt, MAX_RETRIES, exc)
        if attempt < MAX_RETRIES:
            if response is not None and response.status_code == 429:
                retry_after = (response.headers.get("Retry-After") or "").strip()
                try:
                    ra_s = float(retry_after) if retry_after else 0.0
                except Exception:
                    ra_s = 0.0
                sleep_s = max(ra_s, _retry_delay_seconds(attempt) * 2)
                await asyncio.sleep(min(sleep_s, BACKOFF_CAP_SECONDS * 2))
            else:
                await asyncio.sleep(_retry_delay_seconds(attempt))

    if last_exc:
        logger.error("upload_media: исчерпаны попытки: %s", last_exc)
    return None


async def send_voice_message(phone: str, audio_mp3: bytes) -> bool:
    """
    Отправить голосовое (MP3): загрузка медиа + сообщение type=audio.
    Без токена — только лог (режим разработки).
    """
    if not audio_mp3:
        return False
    if not settings.whatsapp_api_token:
        logger.info("📤 [WhatsApp audio → %s]: %d байт MP3 (без токена)", phone, len(audio_mp3))
        return True

    media_id = await upload_media_bytes(audio_mp3, "audio/mpeg", "reply.mp3")
    if not media_id:
        return False

    candidates = _whatsapp_to_candidates(phone)
    if not candidates:
        logger.error("WhatsApp audio: пустой номер получателя")
        return False

    headers = {
        "Authorization": f"Bearer {settings.whatsapp_api_token}",
        "Content-Type": "application/json",
    }

    def _body(to: str) -> dict[str, Any]:
        return {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "audio",
            "audio": {"id": media_id},
        }

    result = await _post_whatsapp_messages(
        candidates=candidates,
        headers=headers,
        build_json=_body,
        timeout=SEND_TIMEOUT,
        extract_wamid=False,
        log_prefix="WhatsApp audio",
    )
    if result.ok:
        return True
    return False


def phase13_order_receipt_template_name() -> str | None:
    """Имя шаблона Meta для «чека» / подтверждения заказа (Phase 13.1). Пусто — не используется."""
    name = (getattr(settings, "whatsapp_template_order_receipt", "") or "").strip()
    return name or None


def phase13_order_status_template_name() -> str | None:
    """Имя интерактивного шаблона со статусом / кнопками (Phase 13.1). Пусто — не используется."""
    name = (getattr(settings, "whatsapp_template_order_status_buttons", "") or "").strip()
    return name or None


def phase13_template_language_code() -> str:
    """Языковой код шаблонов в Meta (совпадает с одобренным языком в Business Manager)."""
    return (getattr(settings, "whatsapp_template_language", "") or "ru").strip() or "ru"


async def send_phase13_order_receipt_template(
    phone: str,
    parameters: list[str] | None = None,
) -> bool:
    """
    Обёртка над send_template для сценария «чек после заказа».
    Требует WHATSAPP_TEMPLATE_ORDER_RECEIPT в окружении и одобрения шаблона в Business Manager.
    """
    t = phase13_order_receipt_template_name()
    if not t:
        logger.info("Phase 13.1: WHATSAPP_TEMPLATE_ORDER_RECEIPT не задан — пропуск send_template")
        return False
    return await send_template(phone, t, language_code=phase13_template_language_code(), parameters=parameters)
