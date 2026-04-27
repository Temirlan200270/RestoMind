"""
Интеграция fetch_ai_read_context: реальные AsyncSession + asyncio.gather, без моков ORM.

Патчим только фабрику сессий на in-memory SQLite (как в payment webhook тестах) —
сам SQLAlchemy-стек, пул и закрытие context manager — настоящие.
"""

from __future__ import annotations

import asyncio
import typing

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.config as app_config
import app.services.context_engine as context_engine
from app.db.models import (
    Base,
    KnowledgeItem,
    MenuItem,
    Order,
    OrderStatus,
    Organization,
    User,
)
from app.services.context_engine import fetch_ai_read_context

pytestmark = pytest.mark.asyncio


def _make_tracking_session_factory(
    inner: async_sessionmaker[AsyncSession],
    counter: list[int],
) -> async_sessionmaker[AsyncSession]:
    """
    Оборачивает фабрику: считает +1 на __aenter__, −1 в finally __aexit__.
    Ожидаем 0 после await fetch (5 параллельных with внутри gather).
    """

    class _TrackedFactory:
        def __call__(self) -> AsyncSession:
            sess = inner()
            orig_enter = sess.__aenter__
            orig_exit = sess.__aexit__

            async def _aenter() -> AsyncSession:
                counter[0] += 1
                return await orig_enter()

            async def _aexit(
                exc_type: type[BaseException] | None,
                exc: BaseException | None,
                tb: typing.Any,
            ) -> bool | None:
                try:
                    return await orig_exit(exc_type, exc, tb)  # type: ignore[func-returns-value]
                finally:
                    counter[0] -= 1

            sess.__aenter__ = _aenter  # type: ignore[method-assign, assignment]
            sess.__aexit__ = _aexit  # type: ignore[method-assign, assignment]
            return sess

    from typing import cast

    return cast("async_sessionmaker[AsyncSession]", _TrackedFactory())


@pytest_asyncio.fixture
async def ctx_engine() -> typing.AsyncIterator[tuple[typing.Any, list[int], async_sessionmaker[AsyncSession]]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    inner_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    open_sessions: list[int] = [0]

    yield engine, open_sessions, inner_factory

    open_sessions[0] = 0
    await engine.dispose()


async def _seed_full(ctx_engine: tuple[typing.Any, list[int], async_sessionmaker[AsyncSession]]) -> None:
    engine, _open_sessions, session_factory = ctx_engine
    _ = engine
    # default_organization_id=1: филиал 2 в load_available_menu фильтруется строго по org_id
    async with session_factory() as db:
        org1 = Organization(name="Org One", slug="one", timezone="Asia/Almaty")
        org2 = Organization(name="Org Two", slug="two", timezone="Europe/London", schedule_json={"mon": {}})
        db.add_all([org1, org2])
        await db.flush()
        assert org1.id == 1
        assert org2.id == 2

        # Меню: только org2 попадёт в выборку при organization_id=2; чужой филиал и скрытая позиция — нет
        db.add_all(
            [
                MenuItem(
                    organization_id=1,
                    name="Чужой филиал Суп",
                    category="Суп",
                    price=100.0,
                    is_available=True,
                    iiko_id="iiko-org1-soup",
                ),
                MenuItem(
                    organization_id=2,
                    name="Борщ целевой",
                    category="Суп",
                    price=200.0,
                    is_available=True,
                    iiko_id="iiko-borsch",
                ),
                MenuItem(
                    organization_id=2,
                    name="Скрытая пицца",
                    category="Пицца",
                    price=300.0,
                    is_available=False,
                    iiko_id="iiko-hidden",
                ),
            ],
        )
        await db.flush()

        user2 = User(
            organization_id=2,
            phone="+77001002030",
            name="Тестовый гость",
            operator_note="VIP без лука",
        )
        db.add(user2)
        await db.flush()

        # История для build_customer_context (последние заказы)
        past = Order(
            organization_id=2,
            user_id=user2.id,
            status=OrderStatus.CONFIRMED,
            total_price=1500.0,
            items_json={
                "items": [
                    {"name": "Прошлое блюдо", "qty": 1},
                ],
            },
        )
        draft = Order(
            organization_id=2,
            user_id=user2.id,
            status=OrderStatus.DRAFT,
            total_price=400.0,
            items_json={
                "items": [
                    {"name": "Борщ целевой", "qty": 1},
                ],
            },
        )
        db.add_all([past, draft])
        await db.flush()

        db.add(
            KnowledgeItem(
                organization_id=2,
                knowledge_kind="facility",
                category="Парковка",
                question="Парковка?",
                answer="Для теста: бесплатно на 2 места.",
                is_active=True,
                sort_order=0,
            ),
        )
        await db.commit()


async def test_fetch_ai_read_context_integration(
    ctx_engine: tuple[typing.Any, list[int], async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, open_sessions, inner_factory = ctx_engine
    _ = engine

    monkeypatch.setattr(app_config.settings, "default_organization_id", 1)
    await _seed_full(ctx_engine)

    tracked = _make_tracking_session_factory(inner_factory, open_sessions)
    open_sessions[0] = 0
    monkeypatch.setattr(context_engine, "async_session_factory", tracked)

    phone = "+77001002030"
    org_id = 2

    assert open_sessions[0] == 0
    out = await fetch_ai_read_context(phone, org_id)
    assert open_sessions[0] == 0, "все context manager'ы сессий должны отработать __aexit__"

    # Пользователь и телефон
    assert out.user is not None
    assert out.user.phone == phone
    assert out.user.organization_id == org_id
    assert out.org is not None
    assert out.org.id == org_id
    assert out.org.name == "Org Two"
    assert "Europe" in (out.org.timezone or "")

    # Меню: только org_id=2, is_available, без чужого филиала
    names = {m.name for m in out.menu_items}
    assert "Борщ целевой" in names
    assert "Чужой филиал Суп" not in names
    assert "Скрытая пицца" not in names
    assert all(m.organization_id == org_id for m in out.menu_items)

    # KB
    assert "бесплатно" in (out.kb_context or "").lower()

    # Клиентский контекст: имя, заметка, история
    cc = out.customer_ctx
    assert "Тестовый" in cc
    assert "VIP" in cc
    assert "Прошлое" in cc or "прошл" in cc.lower()

    # Черновик
    assert out.draft_row is not None
    assert out.draft_row.status == OrderStatus.DRAFT
    assert out.draft_row.user_id == out.user.id
    raw = out.draft_row.items_json
    assert isinstance(raw, dict)
    items = raw.get("items")
    assert isinstance(items, list) and items
    assert any("Борщ" in str(it.get("name", "")) for it in items if isinstance(it, dict))

    # Нагрузка: несколько полных gather подряд — счётчик снова 0
    for _ in range(3):
        open_sessions[0] = 0
        await asyncio.gather(
            fetch_ai_read_context(phone, org_id),
            fetch_ai_read_context(phone, org_id),
        )
        assert open_sessions[0] == 0
