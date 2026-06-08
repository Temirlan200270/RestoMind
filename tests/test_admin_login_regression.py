"""
Регрессионные тесты: вход в админку.

Проблема, которую детектирует:
    Если в модели Organization появляется новый столбец без соответствующей
    миграции, SQLAlchemy генерирует
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

import app.db.session as db_session_module
from app.db.models import Organization
from app.db.session import get_db
from app.main import app
from tests.db_helpers import install_app_db_override


@pytest_asyncio.fixture
async def login_client(monkeypatch, postgres_session_factory):
    """
    Фикстура: приложение с Postgres test DB и одной организацией.
    Alembic создаёт схему как в production — тест ловит drift моделей/миграций.
    """
    session_factory = postgres_session_factory
    install_app_db_override(app, get_db, monkeypatch, db_session_module, session_factory)

    # Создаём организацию — без неё legacy-login не найдёт org и вернёт ошибку
    async with session_factory() as db:
        org = Organization(name="Test Cafe", slug="test", is_active=True)
        db.add(org)
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


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
        "Возможная причина: в модели Organization есть столбец без миграции. "
        "Проверьте docs/CONVENTIONS.md §8.2"
    )
    data = resp.json()
    assert data.get("ok") is True, f"Login не вернул ok=True: {data}"
    assert data.get("role") == "admin", f"Login должен возвращать role=admin для UI: {data}"
    assert data.get("staff_role") == "admin"


@pytest.mark.asyncio
async def test_superadmin_login_returns_admin_role_for_ui(login_client: AsyncClient, monkeypatch):
    """POST /login с SUPERADMIN_* env: role=admin (не operator), is_superadmin=true."""
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "superadmin_username", "platform-root")
    monkeypatch.setattr(config_module.settings, "superadmin_password", "super-secret")

    resp = await login_client.post(
        "/api/admin/auth/login",
        json={"username": "platform-root", "password": "super-secret"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("role") == "admin"
    assert data.get("staff_role") == "admin"
    assert data.get("is_superadmin") is True


@pytest.mark.asyncio
async def test_superadmin_me_after_env_login(login_client: AsyncClient, monkeypatch):
    """Legacy SUPERADMIN_* login: /api/superadmin/me не возвращает 403."""
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "superadmin_username", "platform-root")
    monkeypatch.setattr(config_module.settings, "superadmin_password", "super-secret")

    login = await login_client.post(
        "/api/admin/auth/login",
        json={"username": "platform-root", "password": "super-secret"},
    )
    assert login.status_code == 200

    me = await login_client.get("/api/superadmin/me")
    assert me.status_code == 200, me.text
    assert me.json().get("ok") is True


@pytest.mark.asyncio
async def test_organizations_model_columns_accessible(postgres_session_factory):
    """
    SELECT * FROM organizations должен работать без ошибок.

    Проверяет что все столбцы в ORM-модели Organization доступны в БД.
    Если столбец добавлен в модель без миграции — этот тест упадёт
    раньше, чем пользователь попытается войти в систему.
    """
    from sqlalchemy import select
    from app.db.models import Organization as OrgModel

    session_factory = postgres_session_factory
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
