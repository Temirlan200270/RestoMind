# Lighthouse — админка (mobile)

Автоматический прогон Lighthouse по авторизованным URL (профиль **mobile**).

## Как запустить

```bash
npm install
npx playwright install chromium
python -m uvicorn app.main:app --reload   # отдельный терминал
npm run lh:admin
```

Переменные: `LH_BASE_URL` (по умолчанию `http://127.0.0.1:8000`), `ADMIN_USERNAME`, `ADMIN_PASSWORD` (если пароль пуст — **«Посмотреть демо»** / demo-login).

Артефакты: **`summary.json`**, таблица ниже, полные отчёты в **`reports/`** (в `.gitignore`).

---

## Сводка (сгенерировано 2026-05-08T14:52:31.793Z)

База: `http://127.0.0.1:8000` · профиль: **mobile**

| Экран | Performance | Accessibility | Best practices |
|--------|------------:|--------------:|---------------:|
| dashboard | 35 | 93 | 100 |
| orders | 21 | 96 | 96 |
| menu | 36 | 100 | 100 |
| settings_restaurant | 34 | 100 | 100 |
| settings_branding | 37 | 100 | 100 |
| settings_connections | 37 | 100 | 100 |
| settings_smart_sales | 37 | 93 | 100 |
| settings_team | 33 | 93 | 96 |
| settings_health | 28 | 98 | 100 |
| settings_technical | 32 | 98 | 96 |
| settings_bot_test | 27 | 93 | 100 |

**Интерпретация:** [UI_DESIGN_SYSTEM.md](../UI_DESIGN_SYSTEM.md) — целевые пороги и подход (a11y/Lighthouse). Баллы зависят от данных БД и окружения.

## Текущий mobile baseline

Accessibility уже приведена к рабочему уровню на большинстве экранов; оставшиеся просадки Performance — известный архитектурный долг, а не финальное целевое состояние. Главный bottleneck по отчётам: один большой `admin.html` (~550 KiB HTML), Alpine инициализирует скрытые `x-show` деревья всех вкладок, из-за чего растут DOM size, script evaluation и style/layout cost.

Что уже вынесено из initial render: Chart.js загружается лениво, Alpine отдается локально, Google Fonts снят с critical path, Dashboard не догружает заказы на `ws_ready`, длинные блоки настроек ресторана грузятся по видимости/клику.

Следующий крупный шаг к Performance ≥ 80: разделить админку на route/partial chunks или заменить тяжелые `x-show` вкладки на mount-on-demand (`x-if`/динамические partials), чтобы мобильный экран не создавал DOM для таблиц, канбана, аналитики, настроек и модалок одновременно.
