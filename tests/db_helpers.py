"""Shared helpers for Postgres-backed FastAPI tests."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


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
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_db
