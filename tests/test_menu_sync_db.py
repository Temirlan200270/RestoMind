"""
Синхронизация меню iiko → БД: фиксируем, как строки записываются в ``menu_items``
(наличие позиций, категория, дубликаты UUID, фильтр по типу номенклатуры).
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.db.models import MenuItem, Organization
from app.services.menu_sync import sync_menu_from_iiko


@pytest.fixture
def mock_iiko_nomenclature():
    """Подстановка под ``async with IikoClient(...) as client`` — выставлять payload перед patch."""

    cm = AsyncMock()

    def configure_payload(payload: dict) -> None:
        inst = AsyncMock()
        inst.fetch_menu = AsyncMock(return_value=payload)
        cm.__aenter__.return_value = inst
        cm.__aexit__.return_value = None

    cm.configure_payload = configure_payload  # type: ignore[attr-defined]
    return cm


@pytest.mark.asyncio
async def test_sync_persists_sushi_category_from_parent_group_name(
    db_session,
    mock_iiko_nomenclature,
):
    pid = "11111111-1111-4111-a111-111111111111"
    payload = {
        "groups": [],
        "products": [
            {
                "id": pid,
                "name": "Филадельфия",
                "type": "Dish",
                "parentGroup": {"id": "orphan-grp", "name": "Суши"},
                "sizePrices": [{"price": {"currentPrice": 1290}}],
            },
        ],
    }

    mock_iiko_nomenclature.configure_payload(payload)

    org_db = 42
    with patch("app.services.menu_sync.IikoClient", return_value=mock_iiko_nomenclature):
        stats = await sync_menu_from_iiko(
            db_session,
            "fake-login",
            "iiko-org-uuid",
            only_dish_and_good=False,
            restomind_organization_id=org_db,
        )

    assert stats["created"] == 1
    assert stats["updated"] == 0
    assert stats["api_products_dup_merged"] == 0

    res = await db_session.execute(
        select(MenuItem).where(
            MenuItem.organization_id == org_db,
            MenuItem.iiko_id == pid,
        ),
    )
    row = res.scalar_one()
    assert row.name == "Филадельфия"
    assert row.category == "Суши"
    assert row.price == pytest.approx(1290)


@pytest.mark.asyncio
async def test_sync_resolves_restomind_org_and_does_not_scan_other_orgs(
    db_session,
    mock_iiko_nomenclature,
):
    pid = "12121212-1212-4121-a121-121212121212"
    org_a = Organization(name="A", slug="menu-a", iiko_organization_id="iiko-org-a", is_active=True)
    org_b = Organization(name="B", slug="menu-b", iiko_organization_id="iiko-org-b", is_active=True)
    db_session.add_all([org_a, org_b])
    await db_session.commit()
    await db_session.refresh(org_a)
    await db_session.refresh(org_b)
    db_session.add(
        MenuItem(
            organization_id=org_b.id,
            iiko_id=pid,
            name="Чужое старое имя",
            price=100,
            category="Чужое",
            source="iiko",
        ),
    )
    await db_session.commit()

    mock_iiko_nomenclature.configure_payload(
        {
            "groups": [],
            "products": [
                {
                    "id": pid,
                    "name": "Своя позиция",
                    "type": "Dish",
                    "productCategoryName": "Своя",
                    "price": 500,
                },
            ],
        },
    )

    with patch("app.services.menu_sync.IikoClient", return_value=mock_iiko_nomenclature):
        stats = await sync_menu_from_iiko(
            db_session,
            "fake-login",
            "iiko-org-a",
            only_dish_and_good=False,
        )

    assert stats["created"] == 1
    rows = (await db_session.execute(select(MenuItem).where(MenuItem.iiko_id == pid))).scalars().all()
    assert len(rows) == 2
    by_org = {int(row.organization_id): row for row in rows}
    assert by_org[int(org_a.id)].name == "Своя позиция"
    assert by_org[int(org_b.id)].name == "Чужое старое имя"


@pytest.mark.asyncio
async def test_sync_dedupe_product_ids_keeps_single_row_last_wins(db_session, mock_iiko_nomenclature):
    pid = "22222222-2222-4222-a222-222222222222"
    payload = {
        "groups": [],
        "products": [
            {
                "id": pid,
                "name": "Первый черновик",
                "type": "Dish",
                "productCategoryName": "Роллы",
                "price": 100,
            },
            {
                "id": pid,
                "name": "Окончательное имя",
                "type": "Dish",
                "productCategoryName": "Роллы",
                "price": 200,
            },
        ],
    }
    mock_iiko_nomenclature.configure_payload(payload)

    org_db = 43
    with patch("app.services.menu_sync.IikoClient", return_value=mock_iiko_nomenclature):
        stats = await sync_menu_from_iiko(
            db_session,
            "fake-login",
            "iiko-org-uuid",
            only_dish_and_good=False,
            restomind_organization_id=org_db,
        )

    assert stats["api_products_raw"] == 2
    assert stats["api_products"] == 1
    assert stats["api_products_dup_merged"] == 1
    assert stats["created"] == 1

    cnt = await db_session.execute(select(MenuItem).where(MenuItem.organization_id == org_db))
    items = cnt.scalars().all()
    assert len(items) == 1
    assert items[0].name == "Окончательное имя"
    assert items[0].price == pytest.approx(200)


@pytest.mark.asyncio
async def test_sync_updates_existing_same_iiko_uuid(db_session, mock_iiko_nomenclature):
    pid = "33333333-3333-4333-a333-333333333333"
    org_db = 44
    existing = MenuItem(
        organization_id=org_db,
        iiko_id=pid,
        name="Старое название",
        category="Прочее",
        price=100.0,
        is_available=True,
    )
    db_session.add(existing)
    await db_session.flush()

    payload = {
        "groups": [{"id": "g-pizza", "name": "Пицца"}],
        "products": [
            {
                "id": pid,
                "name": "Маргарита новая",
                "type": "Dish",
                "parentGroup": {"id": "g-pizza"},
                "sizePrices": [{"price": {"currentPrice": 2790}}],
            },
        ],
    }
    mock_iiko_nomenclature.configure_payload(payload)

    with patch("app.services.menu_sync.IikoClient", return_value=mock_iiko_nomenclature):
        stats = await sync_menu_from_iiko(
            db_session,
            "fake-login",
            "iiko-org-uuid",
            only_dish_and_good=False,
            restomind_organization_id=org_db,
        )

    assert stats["created"] == 0
    assert stats["updated"] == 1

    await db_session.refresh(existing)
    assert existing.name == "Маргарита новая"
    assert existing.category == "Пицца"
    assert existing.price == pytest.approx(2790)


@pytest.mark.asyncio
async def test_sync_only_dish_good_skips_modifier(db_session, mock_iiko_nomenclature):
    payload = {
        "groups": [],
        "products": [
            {"id": "44444444-4444-4444-a444-444444444444", "name": "Булка", "type": "Modifier"},
            {"id": "55555555-5555-4555-a555-555555555555", "name": "Стейк", "type": "Dish"},
        ],
    }
    mock_iiko_nomenclature.configure_payload(payload)

    org_db = 45
    with patch("app.services.menu_sync.IikoClient", return_value=mock_iiko_nomenclature):
        stats = await sync_menu_from_iiko(
            db_session,
            "fake-login",
            "iiko-org-uuid",
            only_dish_and_good=True,
            restomind_organization_id=org_db,
        )

    assert stats["skip_type"] == 1
    assert stats["created"] == 1

    cnt = await db_session.execute(select(MenuItem).where(MenuItem.organization_id == org_db))
    goods = cnt.scalars().all()
    assert len(goods) == 1
    assert goods[0].name == "Стейк"


@pytest.mark.asyncio
async def test_sync_prune_archives_missing_iiko_rows_but_keeps_manual(db_session, mock_iiko_nomenclature):
    org_db = 46
    keep_id = "66666666-6666-4666-a666-666666666666"
    stale_id = "77777777-7777-4777-a777-777777777777"
    db_session.add_all(
        [
            MenuItem(
                organization_id=org_db,
                iiko_id=stale_id,
                name="Старый iiko товар",
                category="Архив",
                price=100,
                source="iiko",
                is_available=True,
            ),
            MenuItem(
                organization_id=org_db,
                iiko_id="manual-local-id",
                name="Ручная позиция",
                category="Локальное",
                price=200,
                source="manual",
                is_available=True,
            ),
        ],
    )
    await db_session.flush()

    mock_iiko_nomenclature.configure_payload(
        {
            "groups": [],
            "products": [
                {
                    "id": keep_id,
                    "name": "Новый товар",
                    "type": "Dish",
                    "productCategoryName": "Основное",
                    "price": 300,
                },
            ],
        },
    )

    with patch("app.services.menu_sync.IikoClient", return_value=mock_iiko_nomenclature):
        stats = await sync_menu_from_iiko(
            db_session,
            "fake-login",
            "iiko-org-uuid",
            only_dish_and_good=False,
            restomind_organization_id=org_db,
            prune_missing=True,
            prune_mode="archive",
        )

    assert stats["created"] == 1
    assert stats["archived"] == 1
    assert stats["deleted"] == 0

    rows = (
        await db_session.execute(select(MenuItem).where(MenuItem.organization_id == org_db))
    ).scalars().all()
    by_name = {row.name: row for row in rows}
    assert by_name["Старый iiko товар"].is_archived is True
    assert by_name["Старый iiko товар"].is_available is False
    assert by_name["Ручная позиция"].is_archived is False
    assert by_name["Новый товар"].source == "iiko"
    assert by_name["Новый товар"].last_seen_iiko_sync_at is not None
