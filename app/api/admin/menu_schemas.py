"""
Схемы и сериализация меню админки (E0.1: вынесено из _monolith).
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.db.models import MenuItem


def menu_item_dict(item: MenuItem) -> dict:
    """Сериализация позиции меню для API и админки."""
    return {
        "id": item.id,
        "iiko_id": item.iiko_id,
        "name": item.name,
        "category": item.category or "",
        "description": item.description or "",
        "tags": item.tags or "",
        "portion_kind": getattr(item, "portion_kind", None) or "single",
        "serves_min": int(getattr(item, "serves_min", None) or 1),
        "serves_max": int(getattr(item, "serves_max", None) or 1),
        "allergens": getattr(item, "allergens", None) or "",
        "ingredients_summary": getattr(item, "ingredients_summary", None) or "",
        "dietary_tags": getattr(item, "dietary_tags", None) or "",
        "upsell_pairs": getattr(item, "upsell_pairs", None) or "",
        "price": float(item.price),
        "cost_price": float(item.cost_price) if item.cost_price is not None else None,
        "is_available": item.is_available,
        "source": getattr(item, "source", None) or "manual",
        "last_seen_iiko_sync_at": (
            item.last_seen_iiko_sync_at.isoformat()
            if getattr(item, "last_seen_iiko_sync_at", None)
            else None
        ),
        "is_archived": bool(getattr(item, "is_archived", False)),
        "archived_at": item.archived_at.isoformat() if getattr(item, "archived_at", None) else None,
        "image_url": item.image_url,
    }


class MenuItemPatchBody(BaseModel):
    """Частичное обновление позиции (только переданные поля)."""

    name: str | None = Field(None, min_length=1, max_length=255)
    category: str | None = Field(None, max_length=100)
    description: str | None = None
    tags: str | None = None
    portion_kind: str | None = Field(None, description="single | shareable")
    serves_min: int | None = Field(None, ge=1, le=99)
    serves_max: int | None = Field(None, ge=1, le=99)
    allergens: str | None = None
    ingredients_summary: str | None = None
    dietary_tags: str | None = None
    upsell_pairs: str | None = None
    price: float | None = Field(None, ge=0)
    cost_price: float | None = Field(None, ge=0, description="Себестоимость для Menu Profit Lab")
    is_available: bool | None = None
    image_url: str | None = Field(None, max_length=500)


class ClearMenuBody(BaseModel):
    """Подтверждение полной очистки таблицы меню (например, на деплое без Shell)."""

    confirm: bool = Field(False, description="Должно быть true")


class MenuItemCreateBody(BaseModel):
    """Создание позиции вручную (без iiko)."""

    name: str = Field(..., min_length=1, max_length=255)
    category: str = Field(default="", max_length=100)
    description: str = ""
    tags: str = ""
    portion_kind: str = Field(default="single", description="single | shareable")
    serves_min: int = Field(default=1, ge=1, le=99)
    serves_max: int = Field(default=1, ge=1, le=99)
    allergens: str = ""
    ingredients_summary: str = ""
    dietary_tags: str = ""
    upsell_pairs: str = ""
    price: float = Field(0, ge=0)
    cost_price: float | None = Field(None, ge=0, description="Себестоимость для Menu Profit Lab")
    is_available: bool = True
    image_url: str | None = Field(None, max_length=500)


class MenuBulkStoplistBody(BaseModel):
    """Массовые действия по стоп-листу / разделу."""

    action: Literal["stop", "unstop", "set_category"]
    item_ids: list[int] = Field(..., min_length=1, max_length=200)
    category: str | None = Field(
        None,
        max_length=100,
        description="Название раздела как в menu_items.category (не числовой id; обязательно для set_category).",
    )

    @model_validator(mode="after")
    def _require_category_for_set(self) -> "MenuBulkStoplistBody":
        if self.action == "set_category":
            c = (self.category or "").strip()
            if not c:
                raise ValueError("Для set_category укажите непустое поле category")
            self.category = c
        return self
