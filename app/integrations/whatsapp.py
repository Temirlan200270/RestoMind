"""
Клиент WhatsApp (Meta Cloud API).
Автоматически переключается между режимами:
- Если WHATSAPP_API_TOKEN задан → реальная отправка через Meta Graph API
- Если пуст → логирование в консоль (для разработки)
"""

import logging
import re

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v21.0"
WHATSAPP_API_URL = (
    f"https://graph.facebook.com/{GRAPH_API_VERSION}/{settings.whatsapp_phone_number_id}/messages"
)

SEND_TIMEOUT = 10.0
MEDIA_TIMEOUT = 60.0
MAX_RETRIES = 2


def _digits_only(phone: str) -> str:
    return re.sub(r"\D+", "", phone or "").strip()


def _whatsapp_to_candidates(phone: str) -> list[str]:
    """
    Поле `to` для Cloud API: только цифры, **ровно как в вебхуке** (`messages[].from`).

    Раньше добавлялся второй кандидат `78…` для KZ — это давало номер вроде 787051310837
    и ломало доставку (403/131005), хотя Meta присылает уже корректный 7705….
    """
    d = _digits_only(phone)
    return [d] if d else []


def _is_recipient_not_allowed(resp: httpx.Response) -> bool:
    try:
        payload = resp.json() or {}
        err = payload.get("error") or {}
        return int(err.get("code") or 0) == 131030
    except Exception:
        return False


async def send_message(phone: str, text: str) -> bool:
    """
    Отправить текстовое сообщение клиенту в WhatsApp.
    Если токен не настроен — просто логирует (режим разработки).

    Returns:
        True если сообщение отправлено (или залогировано), False при ошибке.
    """
    if not settings.whatsapp_api_token:
        logger.info("📤 [WhatsApp → %s]: %s", phone, text[:200])
        return True

    candidates = _whatsapp_to_candidates(phone)
    if not candidates:
        logger.error("WhatsApp: пустой номер получателя")
        return False

    headers = {
        "Authorization": f"Bearer {settings.whatsapp_api_token}",
        "Content-Type": "application/json",
    }

    for to in candidates:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=SEND_TIMEOUT) as client:
                    response = await client.post(
                        WHATSAPP_API_URL,
                        headers=headers,
                        json={
                            "messaging_product": "whatsapp",
                            "to": to,
                            "type": "text",
                            "text": {"body": text},
                        },
                    )

                if response.status_code == 200:
                    logger.info("WhatsApp: сообщение доставлено → %s", to)
                    return True

                logger.error(
                    "WhatsApp: ошибка %d (to=%s, попытка %d/%d): %s",
                    response.status_code, to, attempt, MAX_RETRIES, response.text[:200],
                )
                if response.status_code == 400 and _is_recipient_not_allowed(response):
                    # Пробуем следующий формат номера (workaround KZ test allowlist)
                    break

            except httpx.TimeoutException:
                logger.error(
                    "WhatsApp: таймаут (to=%s, попытка %d/%d)", to, attempt, MAX_RETRIES,
                )
            except httpx.HTTPError as exc:
                logger.error(
                    "WhatsApp: сетевая ошибка (to=%s, попытка %d/%d): %s", to, attempt, MAX_RETRIES, exc,
                )

    logger.error("WhatsApp: не удалось отправить сообщение → %s после %d попыток", phone, MAX_RETRIES)
    return False


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

    for to in candidates:
        payload["to"] = to
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=SEND_TIMEOUT) as client:
                    response = await client.post(
                        WHATSAPP_API_URL,
                        headers=headers,
                        json=payload,
                    )
                if response.status_code == 200:
                    logger.info("WhatsApp template '%s' → %s", template_name, to)
                    return True
                logger.error(
                    "WhatsApp template error %d (to=%s, попытка %d/%d): %s",
                    response.status_code, to, attempt, MAX_RETRIES, response.text[:200],
                )
                if response.status_code == 400 and _is_recipient_not_allowed(response):
                    break
            except (httpx.TimeoutException, httpx.HTTPError) as exc:
                logger.error(
                    "WhatsApp template error (to=%s, попытка %d/%d): %s", to, attempt, MAX_RETRIES, exc,
                )

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

    meta_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{media_id}"

    try:
        async with httpx.AsyncClient(timeout=MEDIA_TIMEOUT) as client:
            r = await client.get(
                meta_url,
                headers={"Authorization": f"Bearer {token}"},
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


MEDIA_UPLOAD_URL = (
    f"https://graph.facebook.com/{GRAPH_API_VERSION}/{settings.whatsapp_phone_number_id}/media"
)


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

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=MEDIA_TIMEOUT) as client:
                response = await client.post(
                    MEDIA_UPLOAD_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    files={
                        "file": (filename, file_bytes, mime_type),
                    },
                    data={
                        "messaging_product": "whatsapp",
                        "type": mime_type,
                    },
                )
            if response.status_code == 200:
                mid = (response.json() or {}).get("id")
                if mid:
                    return str(mid)
                logger.error("upload_media: нет id в ответе: %s", response.text[:200])
                return None
            logger.error(
                "upload_media HTTP %s (попытка %d/%d): %s",
                response.status_code, attempt, MAX_RETRIES, response.text[:200],
            )
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            logger.error("upload_media ошибка (попытка %d/%d): %s", attempt, MAX_RETRIES, exc)

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

    for to in candidates:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=SEND_TIMEOUT) as client:
                    response = await client.post(
                        WHATSAPP_API_URL,
                        headers=headers,
                        json={
                            "messaging_product": "whatsapp",
                            "to": to,
                            "type": "audio",
                            "audio": {"id": media_id},
                        },
                    )
                if response.status_code == 200:
                    logger.info("WhatsApp: аудио доставлено → %s", to)
                    return True
                logger.error(
                    "WhatsApp audio: HTTP %s (to=%s, попытка %d/%d): %s",
                    response.status_code, to, attempt, MAX_RETRIES, response.text[:200],
                )
                if response.status_code == 400 and _is_recipient_not_allowed(response):
                    break
            except (httpx.TimeoutException, httpx.HTTPError) as exc:
                logger.error(
                    "WhatsApp audio: сеть (to=%s, попытка %d/%d): %s",
                    to, attempt, MAX_RETRIES, exc,
                )

    return False
