# Lighthouse — админка (mobile)

Автоматический прогон **Lighthouse** по авторизованным URL админки (профиль **mobile**, категории Performance / Accessibility / Best practices).

## Как запустить

1. Установить зависимости (из корня репозитория):

   ```bash
   npm install
   npx playwright install chromium
   ```

2. В другом терминале поднять API:

   ```bash
   python -m uvicorn app.main:app --reload
   ```

3. Запустить отчёты:

   ```bash
   npm run lh:admin
   ```

Переменные окружения (опционально):

| Переменная | Значение по умолчанию |
|------------|------------------------|
| `LH_BASE_URL` | `http://127.0.0.1:8000` |
| `ADMIN_USERNAME` | `admin` |
| `ADMIN_PASSWORD` | пусто → кнопка **«Попробовать демо»** (если API отвечает **503** «Демо временно недоступно», задайте пароль в `.env`, как для обычного входа) |

Скрипт подхватывает **`ADMIN_USERNAME` / `ADMIN_PASSWORD` из файла `.env`** в корне репозитория, если они не заданы в shell.

После успешного прогона появятся:

- **`summary.json`** — сводка баллов по каждому экрану;
- **`README.md`** в этой папке — будет **перезаписан** скриптом таблицей с цифрами;
- **`reports/*.json`** — полные отчёты Lighthouse (каталог `reports/` в `.gitignore`).

## Что прогоняется

Дашборд, заказы, меню и **все 8** подвкладок настроек (`restaurant`, `branding`, `connections`, `smart_sales`, `team`, `health`, `technical`, `bot_test`) — см. `scripts/run_admin_lighthouse.mjs`.

## Целевые пороги (из UI_REDESIGN_PLAN)

На ключевых экранах ориентир: **Accessibility ≥ 90**, **Performance ≥ 80**, **Best practices ≥ 90**. Фактические числа зависят от БД, сети и железа; зафиксируйте `summary.json` после локального прогона перед релизом.

---

*Таблица результатов появится здесь после первого успешного `npm run lh:admin`.*
