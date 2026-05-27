"""
Общие фикстуры для тестов RestoMind.
"""

import os

# До импорта app.core.config: SessionMiddleware с https_only=True не отдаёт cookie в httpx по http:// (ASGITransport).
os.environ.setdefault("APP_DEBUG", "true")
# Тесты ожидают синхронные consumers в той же транзакции (DailyOrgStats и т.д.).
os.environ.setdefault("EVENT_CONSUMERS_ASYNC", "false")
# Owner digest send_* тесты: send_weekly_digest требует непустой TELEGRAM_BOT_TOKEN (CI без .env).
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "000000000:TEST_CI_BOT_TOKEN")

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, MenuItem, Organization


def _sqlite_engine_url(db_path: Path | None) -> str:
    if db_path is None:
        return "sqlite+aiosqlite:///:memory:"
    return f"sqlite+aiosqlite:///{db_path.resolve().as_posix()}"


def make_sqlite_memory_engine() -> AsyncEngine:
    """Один in-memory connection (StaticPool) — для unit-фикстур с одной сессией."""
    return create_async_engine(
        _sqlite_engine_url(None),
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        pool_reset_on_return=None,
    )


def make_sqlite_file_engine(db_path: Path) -> AsyncEngine:
    """File SQLite — несколько HTTP-сессий на одну БД без StaticPool dispose race."""
    return create_async_engine(
        _sqlite_engine_url(db_path),
        connect_args={"check_same_thread": False},
        pool_reset_on_return=None,
    )


async def _dispose_test_engine(engine: AsyncEngine) -> None:
    """Закрыть engine; StaticPool + aiosqlite на CI иногда гоняется с pool.dispose()."""
    for _ in range(3):
        await asyncio.sleep(0)
    try:
        await engine.dispose()
    except KeyError:
        pass


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Тестовая БД в памяти (SQLite)."""
    engine = make_sqlite_memory_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    await _dispose_test_engine(engine)


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


@pytest_asyncio.fixture
async def asgi_memory_client(monkeypatch, tmp_path: Path):
    """
    FastAPI-приложение с SQLite (file) и переопределённым get_db.

    Для тестов админ-сессии и cookie (login → /auth/me).
    """
    import app.db.session as db_session_module
    from app.db.session import get_db
    from app.main import app
    from httpx import ASGITransport, AsyncClient

    db_file = tmp_path / "restomind_asgi_test.db"
    engine = make_sqlite_file_engine(db_file)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_session_module, "async_session_factory", session_factory)

    async def _override_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, session_factory

    app.dependency_overrides.clear()
    await _dispose_test_engine(engine)
