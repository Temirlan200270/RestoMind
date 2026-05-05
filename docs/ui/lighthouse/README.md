# Lighthouse — админка (mobile)

Автоматический прогон Lighthouse по авторизованным URL (профиль **mobile**).

## Как запустить

```bash
npm install
npx playwright install chromium
python -m uvicorn app.main:app --reload   # отдельный терминал
npm run lh:admin
```

Переменные: `LH_BASE_URL` (по умолчанию `http://127.0.0.1:8000`), `ADMIN_USERNAME`, `ADMIN_PASSWORD` (если пароль пуст — **Попробовать демо**).

Артефакты: **`summary.json`**, таблица ниже, полные отчёты в **`reports/`** (в `.gitignore`).

---

## Сводка (сгенерировано 2026-05-05T13:49:22.578Z)

База: `http://127.0.0.1:9892` · профиль: **mobile**

| Экран | Performance | Accessibility | Best practices |
|--------|------------:|--------------:|---------------:|
| dashboard | 29 | 89 | 96 |
| orders | 42 | 90 | 93 |
| menu | 56 | 94 | 96 |
| settings_restaurant | 33 | 83 | 96 |
| settings_branding | 57 | 87 | 96 |
| settings_connections | 57 | 93 | 96 |
| settings_smart_sales | 57 | 87 | 96 |
| settings_team | 57 | 87 | 96 |
| settings_health | 56 | 93 | 96 |
| settings_technical | 56 | 93 | 96 |
| settings_bot_test | 40 | 93 | 96 |

**Интерпретация:** [UI_REDESIGN_PLAN.md](../UI_REDESIGN_PLAN.md) — на ключевых экранах ориентир: Accessibility ≥ 90, Performance ≥ 80, Best practices ≥ 90. Баллы зависят от данных БД и окружения.
