# Деплой RestoMind на Render

Полноценный бэкенд (FastAPI + Jinja-админка + WebSocket) размещается **на Render** как **Web Service** + **PostgreSQL**.  
**Vercel** этот репозиторий напрямую не подходит: нет долгоживущего процесса и нормальных WebSocket для текущей архитектуры (см. [docs/VERCEL.md](docs/VERCEL.md)).

Я не могу зайти в ваш аккаунт Render/Vercel — деплой выполняете вы по шагам ниже.

---

## Предварительно

1. Репозиторий на **GitHub** / **GitLab** / **Bitbucket** (Render подключает к git).
2. Файлы в корне: `Dockerfile`, `render.yaml` (Blueprint).
3. Учётная запись на [render.com](https://render.com) (часто нужна привязка карты даже для free-тарифов).

---

## Вариант A: Blueprint (рекомендуется)

1. Залейте код в удалённый репозиторий (ветка `main` или `master`).
2. В Render: **New** → **Blueprint**.
3. Укажите репозиторий и корень с `render.yaml`.
4. Подтвердите создание ресурсов: **Web Service** `restomind` + **PostgreSQL** `restomind-db`.
5. В интерфейсе Render задайте переменные с **sync: false** (если мастер их запросит):
   - `GEMINI_API_KEY`
   - `ADMIN_PASSWORD` (и при желании смените `ADMIN_USERNAME` в Environment)
   - токены WhatsApp (`WHATSAPP_*`), когда подключите бота.
6. Дождитесь сборки и деплоя. Логи: сервис → **Logs**.

`DATABASE_URL` подставится из БД автоматически. Миграции выполняются командой **`preDeployCommand: alembic upgrade head`** перед выкладкой.

---

## Вариант B: Вручную (без Blueprint)

1. **New** → **PostgreSQL** — создайте БД, скопируйте **Internal Database URL** или **External**.
2. **New** → **Web Service** → подключите репозиторий, окружение **Docker**, путь к `Dockerfile` — корень.
3. В **Environment** добавьте:
   - `DATABASE_URL` = вставьте URL из шага 1 (или соберите из полей, как в `.env.example`).
   - `APP_DEBUG=false`, `DB_MODE=postgres`, `REDIS_ENABLED=false` (или подключите Redis позже).
   - `SESSION_SECRET` — случайная длинная строка (`openssl rand -hex 32`).
   - `GEMINI_API_KEY`, `ADMIN_PASSWORD`, при необходимости WhatsApp/iiko.
4. В настройках сервиса укажите **Pre-Deploy Command**: `alembic upgrade head`.
5. **Start Command** оставьте из Dockerfile (uvicorn с `$PORT`) или пусто, если используется только `CMD` образа.

---

## После деплоя

- URL приложения: `https://<имя-сервиса>.onrender.com` (или свой домен в **Settings** → **Custom Domain**).
- Админка: `https://…onrender.com/admin` — вход логин/пароль из env.
- **WhatsApp Webhook** в Meta:  
  `https://<ваш-домен>/api/whatsapp/webhook`  
  Verify Token = `WHATSAPP_VERIFY_TOKEN`.
- **Healthcheck:** `GET /health` — уже указан в `render.yaml` как `healthCheckPath`.

### Free tier

Сервис **засыпает** после простоя; первый запрос может занять ~30–60 с. Для продакшена без «сна» нужен платный план.

### Redis (опционально)

Сейчас в Blueprint **`REDIS_ENABLED=false`** — сессии/события работают в in-memory в рамках одного инстанса. Для нескольких инстансов или устойчивости добавьте **Render Key Value (Redis)** и переменные `REDIS_ENABLED=true`, `REDIS_HOST`, `REDIS_PORT` из дашборда.

---

## Проверка

```bash
curl -sS https://<ваш-хост>/health
```

Ожидается JSON со статусом ok.

---

## Файлы в репозитории

| Файл | Назначение |
|------|------------|
| `render.yaml` | Blueprint: веб + Postgres + env |
| `Dockerfile` | Сборка образа; слушает `$PORT` |
| `DEPLOY_GUIDE.md` | Альтернатива: свой VPS + Docker + Traefik |
