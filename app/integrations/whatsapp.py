"""
Клиент WhatsApp (Meta Cloud API).
Автоматически переключается между режимами:
- Если WHATSAPP_API_TOKEN задан → реальная отправка через Meta Graph API
- Если пуст → логирование в консоль (для разработки)
"""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

WHATSAPP_API_URL = (
    f"https://graph.facebook.com/v21.0/{settings.whatsapp_phone_number_id}/messages"
)

SEND_TIMEOUT = 10.0
MAX_RETRIES = 2


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

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=SEND_TIMEOUT) as client:
                response = await client.post(
                    WHATSAPP_API_URL,
                    headers={
                        "Authorization": f"Bearer {settings.whatsapp_api_token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "messaging_product": "whatsapp",
                        "to": phone,
                        "type": "text",
                        "text": {"body": text},
                    },
                )

            if response.status_code == 200:
                logger.info("WhatsApp: сообщение доставлено → %s", phone)
                return True

            logger.error(
                "WhatsApp: ошибка %d (попытка %d/%d): %s",
                response.status_code, attempt, MAX_RETRIES, response.text[:200],
            )

        except httpx.TimeoutException:
            logger.error(
                "WhatsApp: таймаут (попытка %d/%d) → %s", attempt, MAX_RETRIES, phone,
            )
        except httpx.HTTPError as exc:
            logger.error(
                "WhatsApp: сетевая ошибка (попытка %d/%d): %s", attempt, MAX_RETRIES, exc,
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
        "to": phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
        },
    }
    if components:
        payload["template"]["components"] = components

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=SEND_TIMEOUT) as client:
                response = await client.post(
                    WHATSAPP_API_URL,
                    headers={
                        "Authorization": f"Bearer {settings.whatsapp_api_token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            if response.status_code == 200:
                logger.info("WhatsApp template '%s' → %s", template_name, phone)
                return True
            logger.error(
                "WhatsApp template error %d (попытка %d/%d): %s",
                response.status_code, attempt, MAX_RETRIES, response.text[:200],
            )
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            logger.error("WhatsApp template error (попытка %d/%d): %s", attempt, MAX_RETRIES, exc)

    return False
