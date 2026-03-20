"""
Стартовое меню: если таблица menu_items пуста, заполняем встроенным каталогом (PLOVXANA).

Вызывается при старте приложения и из сида демо — **один раз** для пустой БД;
дальше позиции меняются только через админку / iiko-синхронизацию.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.plovxana_menu import PLOVXANA_MENU_ITEMS
from app.db.models import MenuItem


async def ensure_default_menu_if_empty(db: AsyncSession) -> int:
    """
    Если в БД нет ни одной позиции — добавить все строки из PLOVXANA_MENU_ITEMS.

    Returns:
        Число добавленных позиций (0, если меню уже было непустым).
    """
    cnt = await db.scalar(select(func.count()).select_from(MenuItem)) or 0
    if cnt > 0:
        return 0
    for name, category, price, description in PLOVXANA_MENU_ITEMS:
        mi = MenuItem(
            name=name,
            category=category,
            price=price,
            description=description,
            is_available=True,
        )
        mi.iiko_id = str(uuid.uuid4())
        db.add(mi)
    await db.flush()
    return len(PLOVXANA_MENU_ITEMS)
