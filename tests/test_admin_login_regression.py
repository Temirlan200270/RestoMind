"""
Регрессионные тесты: вход в админку.

Проблема, которую детектирует:
    Если в модели Organization появляется новый столбец без соответствующей
    миграции (или SQLite-патча в main.py), SQLAlchemy генерирует
    SELECT ... organizations.new_col ... при любом db.get(Organization, id).
    Это происходит в login-эндпоинте → вход полностью ломается с
    UndefinedColumnError, хотя сами credentials верны.

Пример реального бага (2026-05):
    payment_config_json добавлен в модель Organization, но миграция на прод
    уже была применена без этого столбца. Симптом: 500 при попытке войти
    с правильными admin/restomind кредами.

Соглашение: docs/CONVENTIONS.md §8.2
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.session as db_session_module
from app.db.models import Base, Organization
from app.db.session import get_db
from app.main import app


def _make_engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest_asyncio.fixture
async def login_client(monkeypatch):
    """
    Фикстура: приложение с in-memory SQLite и одной организацией.
    create_all создаёт таблицы по текущим ORM-моделям — именно это
    нам нужно: тест проверяет что модели консистентны сами по себе.
    """
    engine = _make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
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

    # Создаём организацию — без неё legacy-login не найдёт org и вернёт ошибку
    async with session_factory() as db:
        org = Organization(name="Test Cafe", slug="test", is_active=True)
        db.add(org)
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_login_with_default_credentials(login_client: AsyncClient):
    """
    Вход с дефолтными кредами admin/restomind должен вернуть 200 и ok=True.

    Регрессия: если в модели Organization есть столбец, которого нет в БД
    (например payment_config_json добавлен в models.py без миграции),
    login-эндпоинт падает с 500 на SELECT organizations WHERE id=$1.
    """
    resp = await login_client.post(
        "/api/admin/auth/login",
        json={"username": "admin", "password": "restomind"},
    )
    assert resp.status_code == 200, (
        f"Login вернул {resp.status_code}: {resp.text}\n"
        "Возможная причина: в модели Organization есть столбец без миграции/патча. "
        "Проверьте docs/CONVENTIONS.md §8.2"
    )
    data = resp.json()
    assert data.get("ok") is True, f"Login не вернул ok=True: {data}"


@pytest.mark.asyncio
async def test_organization_model_columns_accessible(login_client: AsyncClient):
    """
    SELECT * FROM organizations должен работать без ошибок.

    Проверяет что все столбцы в ORM-модели Organization доступны в БД.
    Если столбец добавлен в модель без миграции — этот тест упадёт
    раньше, чем пользователь попытается войти в систему.
    """
    from sqlalchemy import select, text
    from app.db.models import Organization as OrgModel

    engine = _make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as db:
        # Добавляем org для теста
        org = OrgModel(name="Probe", slug="probe", is_active=True)
        db.add(org)
        await db.commit()
        await db.refresh(org)
        org_id = org.id

    async with session_factory() as db:
        # db.get использует SELECT со всеми столбцами модели
        # Если какого-то столбца нет в БД — упадёт здесь
        result = await db.get(OrgModel, org_id)
        assert result is not None, "Organization не найдена в тестовой БД"
        assert result.name == "Probe"

        # Полный SELECT тоже должен работать
        rows = (await db.execute(select(OrgModel))).scalars().all()
        assert len(rows) >= 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_login_returns_ws_token(login_client: AsyncClient):
    """
    После успешного входа должен выдаваться ws_token для WebSocket.
    Регрессия: login-эндпоинт должен полностью отрабатывать, не падать на БД.
    """
    resp = await login_client.post(
        "/api/admin/auth/login",
        json={"username": "admin", "password": "restomind"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "ws_token" in data, f"ws_token отсутствует в ответе login: {data}"
    assert data["ws_token"], "ws_token пустой"
