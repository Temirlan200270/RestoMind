# Final Mile — browser smoke checklist

Ручная проверка UI после деплоя или локально (`APP_DEBUG=true`, demo login). Автотесты покрывают API; этот чеклист — **видимость и RBAC в браузере**.

### Что уже автоматизировано

| Сценарий | Автотест | Ручной шаг остаётся |
|----------|----------|---------------------|
| Final Mile HTTP endpoints (200, no 5xx) | `tests/test_final_mile_smoke.py` | Визуальные disabled/hint по ролям |
| RBAC 403 на operator-only мутациях | частично в API-тестах | Кнопки disabled в UI + Console 0 errors |
| Admin baseline PNG | `scripts/capture_admin_u0_baseline.py` | Sign-off таблица ниже |

Перед ручным прогоном: `pytest tests/test_final_mile_smoke.py -q`

Скриншоты (опционально): `python scripts/capture_admin_u0_baseline.py` — включает `final_mile`, `guestcare`, `shift`.

---

## Подготовка

1. Chrome / Edge, DevTools → Console — **0 ошибок** на каждом экране.
2. Три учётки (или смена роли в **Настройки → Команда**): `operator`, `manager`, `admin`.
3. Hash-навигация: `#/ai_center?tab=final_mile`, `#/ai_center?tab=guestcare`, `#/shift`.

---

## Сценарии

| Роль | Экран | Действие | Ожидание |
|------|-------|----------|----------|
| operator | AI Center → Final Mile | Sync iiko, Voice «Сохранить», SupplyMind draft | Кнопки **disabled** + hint admin/manager |
| operator | Настройки → Команда | StaffMind «Запустить», «Спросить» | **disabled** + hint |
| operator | Настройки → Подключения | «Сохранить iiko Office» | **disabled** + hint admin |
| manager | Final Mile | Создать draft, Sync iiko (если configured) | Работает (403 нет) |
| manager | Final Mile | Voice «Сохранить» | **disabled** (admin only) |
| admin | Подключения | Save iiko Office (host, login, store_id) | 200, configured=true |
| admin | Final Mile | Voice realtime toggle + save | Сохраняется |
| admin | Guestcare | «Синхронизировать» | Сообщение stats; 2GIS отзывы или hint про review_url_2gis |
| admin | Shift (нижняя nav) | Открыть вкладку | S0–S5 badge, метрики, focus/queue без JS error |
| admin | Настройки → Ресторан | review_url_2gis + save | PATCH ok |

---

## Sign-off

| Проверил | Дата | URL (staging/prod) | Console чисто | Примечания |
|----------|------|----------------------|---------------|------------|
| | | | [ ] | |

После sign-off: строка в `CHANGELOG.md` `[Unreleased]` и ссылка на этот файл.
