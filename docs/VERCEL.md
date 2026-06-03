# Vercel и RestoMind

## Почему основной API не на Vercel

- **RestoMind** — один **FastAPI**-процесс: HTML-админка (Jinja), **WebSocket** (`/api/admin/ws`), фоновые задачи, долгие соединения.
- **Vercel** ориентирован на **serverless** и статические сайты: лимиты по времени выполнения, холодные старты, неудобные долгоживущие WebSocket для такого монолита.

Production deployment is documented for Render; see [DEPLOY_RENDER.md](../DEPLOY_RENDER.md) and [docs/DEPLOY_RUNBOOK.md](DEPLOY_RUNBOOK.md).

## Когда Vercel уместен

- Отдельный **фронтенд** (например, Next.js), который ходит в API на Render по HTTPS.
- Статический **лендинг** или редирект на основной домен API.

Подключение плагина Vercel к этому репозиторию **не деплоит бэкенд** автоматически — настройте отдельный проект только если добавите сюда статический фронт или вынесете UI в другой репозиторий.
