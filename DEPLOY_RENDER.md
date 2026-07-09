# Деплой RestoMind на Render

> **Launch Window:** операционный чеклист (env-матрица, worker, smoke) — [`docs/DEPLOY_RUNBOOK.md`](docs/DEPLOY_RUNBOOK.md).

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
5. Подтвердите создание сервисов из Blueprint:
   - **Web Service** `restomind`
   - **Background Worker** `restomind-worker`
6. В интерфейсе Render задайте секреты (как минимум **`DATABASE_URL`** и **`REDIS_URL`**):
   - `DATABASE_URL` — URI Supabase или другого PostgreSQL (обязательно перед первым успешным деплоем).
   - `REDIS_URL` — Upstash **Redis Connect** `rediss://…` (TCP, не REST API).
   - `SESSION_SECRET` — если не используете `generateValue` из Blueprint, задайте вручную длинную случайную строку для cookie-сессии и подписи `ws_token`.
   - `OPENAI_API_KEY`
   - `ADMIN_PASSWORD` (и при желании смените `ADMIN_USERNAME` в Environment)
   - токены WhatsApp (`WHATSAPP_*`), когда подключите бота.
7. Дождитесь сборки и деплоя **web и worker**. Логи: **Logs** у каждого сервиса.

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
   - `APP_ENV=production`, `APP_DEBUG=false`, `DB_MODE=postgres`
   - `REDIS_ENABLED=true`, `REDIS_MEMORY_ONLY=false`, `REDIS_URL=…`, `ARQ_ENABLED=true`
   - `DB_POOL_SIZE=3`, `DB_MAX_OVERFLOW=2`
   - `SESSION_SECRET` — случайная длинная строка (`openssl rand -hex 32`).
   - (временно, если нужно “разблокировать деплой”): `ALLOW_INSECURE_PROD_SETTINGS=true` — **небезопасно**, потом убрать.
   - `OPENAI_API_KEY`, `ADMIN_PASSWORD`, при необходимости WhatsApp/iiko.
4. Для Render Free укажите Docker Command / Start Command: `/app/start_render_free.sh`. Он выполнит миграции, поднимет embedded ARQ worker и затем `uvicorn`.
5. Добавьте `START_EMBEDDED_WORKER=true`, `RUN_MIGRATIONS_ON_START=true`, `REDIS_ENABLED=true`, `REDIS_MEMORY_ONLY=false`, `ARQ_ENABLED=true`, `REDIS_URL=…`.
6. Для paid production можно вместо embedded worker создать **Background Worker** с тем же Docker-образом: Start Command `python -m arq app.worker.WorkerSettings`, те же env.

---

## Messaging Gateway для WhatsApp Web

Для WhatsApp Web/Baileys нужен отдельный долгоживущий Node.js сервис:

- Dockerfile path: `services/messaging-gateway/Dockerfile`
- Root/context: `services/messaging-gateway`
- Healthcheck: `/health`
- Plan на текущем бесплатном этапе: `free`
- Production-вариант позже: VPS с volume для `/sessions` или платный Render `starter`/выше с persistent disk
- Env:
  - `RESTOMIND_API_URL=https://<ваш-restomind-домен>`
  - `RESTOMIND_GATEWAY_SECRET=<тот же секрет, что MESSAGING_GATEWAY_SECRET в RestoMind>`
  - `SESSION_ROOT=/sessions`

В основном RestoMind сервисе задайте:

- `MESSAGING_GATEWAY_URL=https://<домен-messaging-gateway>`
- `MESSAGING_GATEWAY_SECRET=<тот же секрет>`

Ограничение бесплатного Render: файловая система сервиса эфемерная, а сам web service может засыпать. Поэтому Baileys-сессия в `/sessions` может потеряться после redeploy/restart/spin-down, и WhatsApp снова попросит QR. На бесплатном этапе это приемлемо только для smoke/демо проверки: QR появился, телефон подключился, входящее сообщение дошло до AI, ответ ушел обратно.

Для стабильной работы с клиентами лучше позже вынести `restomind-messaging-gateway` на VPS с постоянным volume. Тогда основной `restomind` может оставаться на Render, а gateway будет жить отдельно и держать WhatsApp Web-сессию без сна.

Минимальный smoke после деплоя:

```bash
curl -sS https://<домен-messaging-gateway>/health
```

Ожидаемый ответ:

```json
{"ok":true,"provider":"whatsapp_baileys","active_connections":0}
```

После этого в админке RestoMind откройте `Настройки -> Подключения`, создайте WhatsApp Web подключение и проверьте, что QR появился. После сканирования статус должен стать `connected`.

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

### Redis (обязателен для prod/staging)

Blueprint [`render.yaml`](render.yaml) по умолчанию: **`REDIS_ENABLED=true`**, **`REDIS_MEMORY_ONLY=false`**, **`ARQ_ENABLED=true`**, **`APP_ENV=production`**, embedded worker внутри Web Service. В Dashboard всё равно нужно задать секрет **`REDIS_URL`** (Upstash TCP).

Без внешнего Redis и worker входящие WhatsApp/фоновые sync-задачи не обрабатываются через ARQ, live WebSocket админки не шарится между инстансами.

**Только для локальных тестов / исчерпанной квоты Upstash:** **`REDIS_MEMORY_ONLY=true`** — внешний Redis не вызывается; in-memory в процессе web (не для prod).

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
| `render.yaml` | Blueprint: web `restomind` + worker `restomind-worker`, env (БД и Redis — внешние, `DATABASE_URL` + `REDIS_URL`) |
| `Dockerfile` | Сборка образа; слушает `$PORT` |
| `DEPLOY_GUIDE.md` | Альтернатива: свой VPS + Docker + Traefik |
