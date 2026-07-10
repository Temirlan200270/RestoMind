"""
Авто-сбор отзывов после заказа.

Через REVIEW_REQUEST_DELAY_SEC после SENT_TO_IIKO/COMPLETED отправляем
WhatsApp-сообщение с кнопками 👍/👎.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def run_send_review_request(*, org_id: int, order_id: int, phone: str) -> None:
    """
    ARQ-задача (deferred). Проверяет актуальность заказа и отправляет кнопки отзыва.
    Вызывается из worker.py::send_review_request.
    """
    from app.db.session import async_session_factory
    from app.db.models import Order
    from app.services.customer_reply import send_customer_text
    from app.core.config import settings

    if not settings.review_requests_enabled:
        return

    async with async_session_factory() as db:
        order = await db.get(Order, order_id)
        if order is None:
            return
        if order.status == "cancelled":
            return
        # Загружаем org для review_url_2gis (не нужен здесь — нужен при ответе)

    text = "Спасибо за заказ! 😊 Как всё прошло?"
    try:
        await send_customer_text(
            phone,
            f"{text}\n\nОтветьте «Отлично» или «Есть замечание».",
            organization_id=int(org_id),
        )
    except Exception as exc:
        logger.warning("send_review_request: send failed phone=%s: %s", phone, exc)


async def save_customer_feedback(
    *,
    org_id: int,
    phone: str,
    rating: str,
    order_id: int | None = None,
    text: str | None = None,
) -> None:
    """Сохраняет отзыв клиента в БД."""
    from app.db.session import async_session_factory
    from app.db.models import CustomerFeedback, User
    from sqlalchemy import select

    async with async_session_factory() as db:
        user_result = await db.execute(
            select(User).where(User.organization_id == org_id, User.phone == phone)
        )
        user = user_result.scalar_one_or_none()

        feedback = CustomerFeedback(
            organization_id=org_id,
            user_id=user.id if user else None,
            order_id=order_id,
            rating=rating,
            text=text,
        )
        db.add(feedback)
        await db.commit()


async def send_review_positive_reply(phone: str, org_id: int) -> None:
    """👍 — благодарим и отправляем ссылку на 2GIS если задана."""
    from app.db.session import async_session_factory
    from app.db.models import Organization
    from app.services.customer_reply import send_customer_text

    async with async_session_factory() as db:
        org = await db.get(Organization, org_id)

    review_url = (org.review_url_2gis or "").strip() if org else ""
    if review_url:
        msg = (
            "Спасибо! Нам очень важна ваша оценка. 🙏\n"
            f"Если у вас есть минутка — оставьте отзыв:\n{review_url}"
        )
    else:
        msg = "Спасибо за тёплые слова! 🙏 Ждём вас снова."

    await send_customer_text(phone, msg, organization_id=int(org_id))


async def send_review_negative_alert(phone: str, org_id: int) -> None:
    """👎 — Telegram-алерт персоналу с phone_last4."""
    from app.integrations.telegram import send_ops_notification_html

    phone_last4 = phone[-4:] if len(phone) >= 4 else phone
    msg = (
        f"👎 <b>Негативный отзыв</b>\n"
        f"Клиент ...{phone_last4} оценил заказ негативно. "
        "Рекомендуется связаться с клиентом."
    )
    await send_ops_notification_html(msg, organization_id=org_id)
