# Деплой RestoMind на Render

Полноценный бэкенд (FastAPI + Jinja-админка + WebSocket) размещается **на Render** как **Web Service**; база данных — **внешний PostgreSQL** (рекомендуется [Supabase](https://supabase.com), см. [docs/SUPABASE_MIGRATION.md](docs/SUPABASE_MIGRATION.md)) или любой другой хост с `DATABASE_URL`.  
**Vercel** этот репозиторий напрямую не подходит: нет долгоживущего процесса и нормальных WebSocket для текущей архитектуры (см. [docs/VERCEL.md](docs/VERCEL.md)).

Я не могу зайти в ваш аккаунт Render/Vercel — деплой выполняете вы по шагам ниже.

---

## Предварительно

1. Репозиторий на **GitHub** / **GitLab** / **Bitbucket** (Render подключает к git).
2. Файлы в корне: `Dockerfile`, `render.yaml` (Blueprint).
3. Учётная запись на [render.com](https://render.com) (часто нужна привязка карты даже для free-тарифов).

---

## Вариант A: Blueprint (рекомендуется)

1. Создайте проект в **Supabase** (или другой PostgreSQL) и скопируйте **Database URL** с `sslmode=require` (подробно — [docs/SUPABASE_MIGRATION.md](docs/SUPABASE_MIGRATION.md)).
2. Залейте код в удалённый репозиторий (ветка `main` или `master`).
3. В Render: **New** → **Blueprint**.
4. Укажите репозиторий и корень с `render.yaml`.
5. Подтвердите создание **Web Service** `restomind`.
6. В интерфейсе Render задайте секреты, которые запросит мастер (в т.ч. **`DATABASE_URL`** — строка подключения к Postgres):
   - `DATABASE_URL` — URI Supabase или другого PostgreSQL (обязательно перед первым успешным деплоем).
   - `SESSION_SECRET` — если не используете `generateValue` из Blueprint, задайте вручную длинную случайную строку для cookie-сессии и подписи `ws_token`.
   - `OPENAI_API_KEY`
   - `ADMIN_PASSWORD` (и при желании смените `ADMIN_USERNAME` в Environment)
   - токены WhatsApp (`WHATSAPP_*`), когда подключите бота.
7. Дождитесь сборки и деплоя. Логи: сервис → **Logs**.

Миграции выполняются командой **`preDeployCommand: alembic upgrade head`** перед выкладкой.

---

## Зачем нужен `SESSION_SECRET` (и почему без него падает деплой)

`SESSION_SECRET` используется для:

- подписи cookie-сессии админки (чтобы её нельзя было подделать);
- подписи WebSocket-токена (`ws_token`) для `/api/admin/ws`.

В продакшене мы **обязательно** требуем задать `SESSION_SECRET`, чтобы:

- не хранить “предсказуемый” ключ по умолчанию;
- не допустить ситуации, когда злоумышленник подделывает сессию или токен и получает доступ к админке/чатам.

На Render это проверяется автоматически, потому что наличие `DATABASE_URL` считается признаком прод-окружения.

## Вариант B: Вручную (без Blueprint)

1. **New** → **PostgreSQL** — создайте БД, скопируйте **Internal Database URL** или **External**.
2. **New** → **Web Service** → подключите репозиторий, окружение **Docker**, путь к `Dockerfile` — корень.
3. В **Environment** добавьте:
   - `DATABASE_URL` = вставьте URL из шага 1 (или соберите из полей, как в `.env.example`).
   - `APP_DEBUG=false`, `DB_MODE=postgres`, `REDIS_ENABLED=false` (или подключите Redis позже).
   - `SESSION_SECRET` — случайная длинная строка (`openssl rand -hex 32`).
   - (временно, если нужно “разблокировать деплой”): `ALLOW_INSECURE_PROD_SETTINGS=true` — **небезопасно**, потом убрать.
   - `OPENAI_API_KEY`, `ADMIN_PASSWORD`, при необходимости WhatsApp/iiko.
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

Сейчас в Blueprint **`REDIS_ENABLED=false`** — сессии/события работают в in-memory в рамках одного инстанса. Для нескольких инстансов или устойчивости включите Redis и задайте URL.

**Тесты без лимитов Upstash:** задайте **`REDIS_MEMORY_ONLY=true`** (можно оставить `REDIS_URL` в секретах). Внешний Redis не вызывается; приложение использует только память процесса — удобно, пока квота исчерпана или не нужен общий кэш.

- **Render Key Value:** `REDIS_ENABLED=true` и внутренний хост/порт (или одна строка **`REDIS_URL`**, если Render отдаёт полный URL).
- **Upstash:** в Dashboard скопируйте **Redis Connect** (`rediss://default:ПАРОЛЬ@….upstash.io:6379`) → в Render добавьте секрет **`REDIS_URL`** (значение целиком) и **`REDIS_ENABLED=true`**.  
  **Не** используйте для этого проекта только Upstash **REST** (HTTPS + отдельный токен): у него нет Pub/Sub, а живые события админки идут через `redis.publish` / `subscribe`.

При ошибках TLS к облачному Redis можно временно выставить **`REDIS_SSL_SKIP_VERIFY=true`** (хуже для безопасности; в идеале разобраться с CA).

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
| `render.yaml` | Blueprint: веб-сервис + env (БД — внешняя, `DATABASE_URL`) |
| `Dockerfile` | Сборка образа; слушает `$PORT` |
| `DEPLOY_GUIDE.md` | Альтернатива: свой VPS + Docker + Traefik |
