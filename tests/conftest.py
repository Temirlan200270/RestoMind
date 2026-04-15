"""
Общие фикстуры для тестов RestoMind.
"""

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, MenuItem, Organization


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Тестовая БД в памяти (SQLite)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def db_with_menu(db_session: AsyncSession) -> AsyncSession:
    """БД с тестовыми позициями меню."""
    db_session.add(Organization(id=1, name="Test Org", slug="test"))
    await db_session.flush()
    oid = 1
    items = [
        MenuItem(organization_id=oid, name="Плов", category="Горячее", price=2790.0, is_available=True, iiko_id="uuid-plov"),
        MenuItem(organization_id=oid, name="Лагман", category="Первое", price=1990.0, is_available=True, iiko_id="uuid-lagman"),
        MenuItem(organization_id=oid, name="Капучино", category="Кофе", price=1190.0, is_available=True, iiko_id="uuid-cappuccino"),
        MenuItem(organization_id=oid, name="Плов 1 кг", category="Горячее", price=4500.0, is_available=True, iiko_id="uuid-plov-1kg"),
        MenuItem(organization_id=oid, name="Самса с говядиной", category="Выпечка", price=790.0, is_available=True, iiko_id="uuid-samsa"),
        MenuItem(organization_id=oid, name="Маргарита", category="Пицца", price=2690.0, is_available=False, iiko_id="uuid-margherita"),
    ]
    db_session.add_all(items)
    await db_session.flush()
    return db_session
