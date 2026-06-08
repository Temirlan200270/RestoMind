"""
Общие фикстуры для тестов RestoMind.
"""

import logging
import os
import subprocess
import sys

# До импорта app.core.config: SessionMiddleware с https_only=True не отдаёт cookie в httpx по http:// (ASGITransport).
os.environ.setdefault("APP_DEBUG", "true")
os.environ.setdefault("DB_MODE", "postgres")
os.environ.setdefault("REDIS_MEMORY_ONLY", "true")
os.environ.setdefault("RESTOMIND_TEST_POSTGRES", "true")

_TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://restomind:restomind_secret@localhost:5432/restomind_test",
)
os.environ["DATABASE_URL"] = _TEST_DATABASE_URL

# CI/local: не засорять лог pytest тысячами строк SQLAlchemy DEBUG.
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
# Тесты ожидают синхронные consumers в той же транзакции (DailyOrgStats и т.д.).
os.environ.setdefault("EVENT_CONSUMERS_ASYNC", "false")
# Owner digest send_* тесты: send_weekly_digest требует непустой TELEGRAM_BOT_TOKEN (CI без .env).
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "000000000:TEST_CI_BOT_TOKEN")

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import event, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session as SyncSession
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.models import Base, Location, MenuItem, Order, Organization, StaffUser, User
from app.db.ssl_context import postgres_connect_args


ROOT = Path(__file__).resolve().parent.parent


def _url_for_asyncpg(url: str, *, database: str | None = None) -> str:
    parsed = make_url(url)
    if database is not None:
        parsed = parsed.set(database=database)
    if parsed.drivername == "postgresql+asyncpg":
        parsed = parsed.set(drivername="postgresql")
    return parsed.render_as_string(hide_password=False)


def _validate_database_name(url: URL) -> str:
    db_name = url.database or ""
    if not db_name or not db_name.replace("_", "").isalnum():
        raise RuntimeError(f"Unsafe TEST_DATABASE_URL database name: {db_name!r}")
    return db_name


async def _ensure_postgres_database(database_url: str) -> None:
    parsed = make_url(database_url)
    db_name = _validate_database_name(parsed)
    admin_dsn = _url_for_asyncpg(database_url, database="postgres")
    conn = await asyncpg.connect(admin_dsn)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()


async def _reset_postgres_schema(database_url: str) -> None:
    conn = await asyncpg.connect(_url_for_asyncpg(database_url))
    try:
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("GRANT ALL ON SCHEMA public TO PUBLIC")
    finally:
        await conn.close()


def make_postgres_engine() -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        connect_args=postgres_connect_args(settings.database_url),
        poolclass=NullPool,
    )


async def _truncate_tables(engine: AsyncEngine) -> None:
    table_names = [table.name for table in Base.metadata.sorted_tables if table.name != "alembic_version"]
    if not table_names:
        return
    async with engine.begin() as conn:
        preparer = conn.dialect.identifier_preparer
        quoted = ", ".join(preparer.quote(name) for name in table_names)
        await conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))


def _row_exists(session: SyncSession, model: type, row_id: int) -> bool:
    with session.no_autoflush:
        return session.get(model, row_id) is not None


def _pending_ids(session: SyncSession, model: type) -> set[int]:
    ids: set[int] = set()
    for obj in session.new:
        if isinstance(obj, model):
            row_id = getattr(obj, "id", None)
            if row_id is not None:
                ids.add(int(row_id))
    return ids


def _ensure_org(session: SyncSession, org_id: int | None) -> None:
    if org_id is None or int(org_id) in _pending_ids(session, Organization) or _row_exists(session, Organization, int(org_id)):
        return
    stmt = (
        pg_insert(Organization)
        .values(id=int(org_id), name=f"Test Org {org_id}", slug=f"test-org-{org_id}")
        .on_conflict_do_nothing(index_elements=["id"])
    )
    session.execute(stmt)


def _ensure_location(session: SyncSession, location_id: int | None, org_id: int | None) -> None:
    if location_id is None or int(location_id) in _pending_ids(session, Location) or _row_exists(session, Location, int(location_id)):
        return
    resolved_org_id = int(org_id or 1)
    _ensure_org(session, resolved_org_id)
    stmt = (
        pg_insert(Location)
        .values(
            id=int(location_id),
            organization_id=resolved_org_id,
            name=f"Test Location {location_id}",
            slug=f"loc-{location_id}",
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    session.execute(stmt)


def _ensure_user(session: SyncSession, user_id: int | None, org_id: int | None) -> None:
    if user_id is None or int(user_id) in _pending_ids(session, User) or _row_exists(session, User, int(user_id)):
        return
    resolved_org_id = int(org_id or 1)
    _ensure_org(session, resolved_org_id)
    stmt = (
        pg_insert(User)
        .values(
            id=int(user_id),
            organization_id=resolved_org_id,
            phone=f"+700000{int(user_id):06d}",
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    session.execute(stmt)


def _ensure_staff_user(session: SyncSession, staff_user_id: int | None, org_id: int | None) -> None:
    if staff_user_id is None or int(staff_user_id) in _pending_ids(session, StaffUser) or _row_exists(session, StaffUser, int(staff_user_id)):
        return
    resolved_org_id = int(org_id or 1)
    _ensure_org(session, resolved_org_id)
    stmt = (
        pg_insert(StaffUser)
        .values(
            id=int(staff_user_id),
            organization_id=resolved_org_id,
            email=f"staff-{int(staff_user_id)}@test.local",
            password_hash="test",
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    session.execute(stmt)


def _ensure_order(session: SyncSession, order_id: int | None, org_id: int | None, user_id: int | None) -> None:
    if order_id is None or int(order_id) in _pending_ids(session, Order) or _row_exists(session, Order, int(order_id)):
        return
    resolved_org_id = int(org_id or 1)
    with session.no_autoflush:
        existing_user_id = session.scalar(
            select(User.id).where(User.organization_id == resolved_org_id).limit(1),
        )
    resolved_user_id = int(user_id or existing_user_id or order_id)
    _ensure_user(session, resolved_user_id, resolved_org_id)
    stmt = (
        pg_insert(Order)
        .values(
            id=int(order_id),
            organization_id=resolved_org_id,
            user_id=resolved_user_id,
            status="draft",
            total_price=0,
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    session.execute(stmt)


@event.listens_for(SyncSession, "before_flush")
def _seed_missing_test_parents(session: SyncSession, _flush_context, _instances) -> None:
    """Postgres enforces FK constraints; legacy unit tests often use hardcoded ids."""
    if session.info.get("_seeding_test_parents"):
        return
    session.info["_seeding_test_parents"] = True
    try:
        for obj in list(session.new) + list(session.dirty):
            if isinstance(obj, Organization):
                continue
            org_id = getattr(obj, "organization_id", None)
            location_id = getattr(obj, "location_id", None)
            user_id = getattr(obj, "user_id", None)
            order_id = getattr(obj, "order_id", None)
            staff_user_id = (
                getattr(obj, "staff_user_id", None)
                or getattr(obj, "updated_by_staff_id", None)
                or getattr(obj, "decided_by_staff_id", None)
            )
            if org_id is not None:
                _ensure_org(session, int(org_id))
            if location_id is not None:
                _ensure_location(session, int(location_id), int(org_id) if org_id is not None else None)
            if user_id is not None:
                _ensure_user(session, int(user_id), int(org_id) if org_id is not None else None)
            if staff_user_id is not None:
                _ensure_staff_user(session, int(staff_user_id), int(org_id) if org_id is not None else None)
            if order_id is not None:
                _ensure_order(
                    session,
                    int(order_id),
                    int(org_id) if org_id is not None else None,
                    int(user_id) if user_id is not None else None,
                )
    finally:
        session.info.pop("_seeding_test_parents", None)


event.listen(AsyncSession.sync_session_class, "before_flush", _seed_missing_test_parents)


class TestAsyncSession(AsyncSession):
    """AsyncSession с test-only parent seeding для Postgres FK compatibility."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        event.listen(self.sync_session, "before_flush", _seed_missing_test_parents)

    async def _seed_missing_parents_async(self) -> None:
        referenced_org_ids: set[int] = set()
        referenced_location_ids: set[int] = set()
        referenced_user_ids: set[int] = set()
        referenced_staff_ids: set[int] = set()
        referenced_order_ids: set[int] = set()
        for obj in list(self.sync_session.new) + list(self.sync_session.dirty):
            if isinstance(obj, Organization):
                continue
            org_id = getattr(obj, "organization_id", None)
            location_id = getattr(obj, "location_id", None)
            user_id = getattr(obj, "user_id", None)
            order_id = getattr(obj, "order_id", None)
            staff_user_id = (
                getattr(obj, "staff_user_id", None)
                or getattr(obj, "updated_by_staff_id", None)
                or getattr(obj, "decided_by_staff_id", None)
            )
            if org_id is not None:
                referenced_org_ids.add(int(org_id))
            if location_id is not None:
                referenced_location_ids.add(int(location_id))
            if user_id is not None:
                referenced_user_ids.add(int(user_id))
            if staff_user_id is not None:
                referenced_staff_ids.add(int(staff_user_id))
            if order_id is not None:
                referenced_order_ids.add(int(order_id))

        def _pending(model: type, row_ids: set[int]) -> list:
            return [
                obj
                for obj in self.sync_session.new
                if isinstance(obj, model) and getattr(obj, "id", None) in row_ids
            ]

        for model, row_ids in (
            (Organization, referenced_org_ids),
            (Location, referenced_location_ids),
            (User, referenced_user_ids),
            (StaffUser, referenced_staff_ids),
            (Order, referenced_order_ids),
        ):
            parents = _pending(model, row_ids)
            if parents:
                await AsyncSession.flush(self, objects=parents)

        for obj in list(self.sync_session.new) + list(self.sync_session.dirty):
            if isinstance(obj, Organization):
                continue
            org_id = getattr(obj, "organization_id", None)
            location_id = getattr(obj, "location_id", None)
            user_id = getattr(obj, "user_id", None)
            order_id = getattr(obj, "order_id", None)
            staff_user_id = (
                getattr(obj, "staff_user_id", None)
                or getattr(obj, "updated_by_staff_id", None)
                or getattr(obj, "decided_by_staff_id", None)
            )
            if org_id is not None and int(org_id) not in _pending_ids(self.sync_session, Organization):
                await self.execute(
                    pg_insert(Organization)
                    .values(id=int(org_id), name=f"Test Org {org_id}", slug=f"test-org-{org_id}")
                    .on_conflict_do_nothing(index_elements=["id"]),
                )
            if location_id is not None and int(location_id) not in _pending_ids(self.sync_session, Location):
                resolved_org_id = int(org_id or 1)
                await self.execute(
                    pg_insert(Organization)
                    .values(id=resolved_org_id, name=f"Test Org {resolved_org_id}", slug=f"test-org-{resolved_org_id}")
                    .on_conflict_do_nothing(index_elements=["id"]),
                )
                await self.execute(
                    pg_insert(Location)
                    .values(
                        id=int(location_id),
                        organization_id=resolved_org_id,
                        name=f"Test Location {location_id}",
                        slug=f"loc-{location_id}",
                    )
                    .on_conflict_do_nothing(index_elements=["id"]),
                )
            if user_id is not None and int(user_id) not in _pending_ids(self.sync_session, User):
                resolved_org_id = int(org_id or 1)
                await self.execute(
                    pg_insert(Organization)
                    .values(id=resolved_org_id, name=f"Test Org {resolved_org_id}", slug=f"test-org-{resolved_org_id}")
                    .on_conflict_do_nothing(index_elements=["id"]),
                )
                await self.execute(
                    pg_insert(User)
                    .values(
                        id=int(user_id),
                        organization_id=resolved_org_id,
                        phone=f"+700000{int(user_id):06d}",
                    )
                    .on_conflict_do_nothing(index_elements=["id"]),
                )
            if staff_user_id is not None and int(staff_user_id) not in _pending_ids(self.sync_session, StaffUser):
                resolved_org_id = int(org_id or 1)
                await self.execute(
                    pg_insert(Organization)
                    .values(id=resolved_org_id, name=f"Test Org {resolved_org_id}", slug=f"test-org-{resolved_org_id}")
                    .on_conflict_do_nothing(index_elements=["id"]),
                )
                await self.execute(
                    pg_insert(StaffUser)
                    .values(
                        id=int(staff_user_id),
                        organization_id=resolved_org_id,
                        email=f"staff-{int(staff_user_id)}@test.local",
                        password_hash="test",
                    )
                    .on_conflict_do_nothing(index_elements=["id"]),
                )
            if order_id is not None and int(order_id) not in _pending_ids(self.sync_session, Order):
                resolved_org_id = int(org_id or 1)
                with self.no_autoflush:
                    existing_user_id = await self.scalar(
                        select(User.id).where(User.organization_id == resolved_org_id).limit(1),
                    )
                resolved_user_id = int(user_id or existing_user_id or order_id)
                await self.execute(
                    pg_insert(Organization)
                    .values(id=resolved_org_id, name=f"Test Org {resolved_org_id}", slug=f"test-org-{resolved_org_id}")
                    .on_conflict_do_nothing(index_elements=["id"]),
                )
                await self.execute(
                    pg_insert(User)
                    .values(
                        id=resolved_user_id,
                        organization_id=resolved_org_id,
                        phone=f"+700000{resolved_user_id:06d}",
                    )
                    .on_conflict_do_nothing(index_elements=["id"]),
                )
                await self.execute(
                    pg_insert(Order)
                    .values(
                        id=int(order_id),
                        organization_id=resolved_org_id,
                        user_id=resolved_user_id,
                        status="draft",
                        total_price=0,
                    )
                    .on_conflict_do_nothing(index_elements=["id"]),
                )

    async def flush(self, objects=None) -> None:
        await self._seed_missing_parents_async()
        await super().flush(objects=objects)

    async def commit(self) -> None:
        await self._seed_missing_parents_async()
        await super().commit()


def install_app_db_override(
    app,
    get_db,
    monkeypatch,
    db_session_module,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Подменить FastAPI get_db и глобальную фабрику сессий на тестовую Postgres-БД."""
    monkeypatch.setattr(db_session_module, "async_session_factory", session_factory)

    async def _override_db():
        from app.db.tenant_rls import apply_tenant_rls

        async with session_factory() as session:
            await apply_tenant_rls(session)
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_db


@pytest.fixture(autouse=True)
def _tenant_rls_test_bypass():
    """Тесты по умолчанию обходят RLS — изоляция проверяется в test_tenant_rls.py."""
    from app.db.tenant_rls import reset_tenant_rls_bypass, set_tenant_rls_bypass

    token = set_tenant_rls_bypass(True)
    yield
    reset_tenant_rls_bypass(token)


@pytest.fixture(scope="session", autouse=True)
def migrated_postgres_schema() -> None:
    """Тестовая схема поднимается на Postgres и помечается текущим Alembic head."""
    asyncio.run(_ensure_postgres_database(_TEST_DATABASE_URL))
    asyncio.run(_reset_postgres_schema(_TEST_DATABASE_URL))
    engine = make_postgres_engine()
    async def _create_schema() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_create_schema())
    env = os.environ.copy()
    env["DATABASE_URL"] = _TEST_DATABASE_URL
    env["DB_MODE"] = "postgres"
    subprocess.run(
        [sys.executable, "-m", "alembic", "stamp", "head"],
        cwd=ROOT,
        env=env,
        check=True,
    )


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Postgres-сессия с откатом всей работы теста."""
    engine = make_postgres_engine()
    connection = await engine.connect()
    transaction = await connection.begin()
    session = TestAsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
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


@pytest_asyncio.fixture
async def postgres_session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Фабрика реальных Postgres-сессий для тестов с несколькими commit/HTTP-сессиями."""
    engine = make_postgres_engine()
    await _truncate_tables(engine)
    session_factory = async_sessionmaker(bind=engine, class_=TestAsyncSession, expire_on_commit=False)
    try:
        yield session_factory
    finally:
        await engine.dispose()
        cleanup_engine = make_postgres_engine()
        try:
            await _truncate_tables(cleanup_engine)
        finally:
            await cleanup_engine.dispose()


@pytest_asyncio.fixture
async def asgi_memory_client(monkeypatch):
    """
    FastAPI-приложение с Postgres test DB и переопределённым get_db.

    Для тестов админ-сессии и cookie (login → /auth/me).
    """
    import app.db.session as db_session_module
    from app.db.session import get_db
    from app.main import app
    from httpx import ASGITransport, AsyncClient

    engine = make_postgres_engine()
    await _truncate_tables(engine)
    session_factory = async_sessionmaker(bind=engine, class_=TestAsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_session_module, "async_session_factory", session_factory)

    async def _override_db():
        from app.db.tenant_rls import apply_tenant_rls

        async with session_factory() as session:
            await apply_tenant_rls(session)
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
    await engine.dispose()
    cleanup_engine = make_postgres_engine()
    try:
        await _truncate_tables(cleanup_engine)
    finally:
        await cleanup_engine.dispose()
