# RestoMind — Руководство по деплою

Пошаговая инструкция запуска RestoMind на **своём VPS** с Docker и автоматическим HTTPS (Traefik).

**Управляемый хостинг без отдельного сервера:** [DEPLOY_RENDER.md](DEPLOY_RENDER.md) (Render Web Service + PostgreSQL, `render.yaml`).

---

## 1. Арендовать VPS

**Минимальные требования:**
- Ubuntu 22.04+ (или Debian 12+)
- 2 vCPU, 2 GB RAM, 20 GB SSD
- Публичный IPv4-адрес

**Рекомендуемые провайдеры:** Hetzner, DigitalOcean, Timeweb Cloud, Aeza.

---

## 2. Привязать домен

В DNS-панели вашего домена создайте **A-запись**:

```
Тип: A
Имя: restomind (или @ для корня)
Значение: <IP вашего VPS>
TTL: 300
```

Дождитесь распространения DNS (обычно 5-15 минут).
Проверка: `ping restomind.your-domain.com`

---

## 3. Подготовить сервер

Подключитесь к VPS через SSH и выполните:

```bash
# Обновить систему
sudo apt update && sudo apt upgrade -y

# Установить Docker
curl -fsSL https://get.docker.com | sh

# Установить Docker Compose plugin
sudo apt install -y docker-compose-plugin

# Добавить текущего пользователя в группу docker (перезайти в SSH после)
sudo usermod -aG docker $USER
```

---

## 4. Склонировать проект

```bash
cd ~
git clone <URL_ВАШЕГО_РЕПО> restomind
cd restomind
```

---

## 5. Настроить переменные окружения

```bash
cp .env.example .env
nano .env
```

**Обязательно измените:**

```env
APP_DEBUG=false

DB_MODE=postgres
POSTGRES_PASSWORD=<СГЕНЕРИРУЙТЕ_НАДЁЖНЫЙ_ПАРОЛЬ>

REDIS_ENABLED=true

OPENAI_API_KEY=<ВАШ_КЛЮЧ_OPENAI>

WHATSAPP_API_TOKEN=<ТОКЕН_ИЗ_META_BUSINESS>
WHATSAPP_VERIFY_TOKEN=<ПРИДУМАЙТЕ_СЕКРЕТНУЮ_СТРОКУ>
WHATSAPP_PHONE_NUMBER_ID=<ID_НОМЕРА_ИЗ_META>

DOMAIN=restomind.your-domain.com
ACME_EMAIL=your-email@example.com
```

Генерация пароля: `openssl rand -base64 24`

---

## 6. Запустить

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

**Что произойдёт:**
1. Соберётся Docker-образ FastAPI
2. Поднимутся PostgreSQL и Redis
3. Traefik автоматически получит SSL-сертификат от Let's Encrypt
4. Сайт станет доступен по `https://restomind.your-domain.com`

**Проверка:**
```bash
# Логи приложения
docker compose -f docker-compose.prod.yml logs -f app

# Статус всех сервисов
docker compose -f docker-compose.prod.yml ps

# Проверить HTTPS
curl https://restomind.your-domain.com/docs
```

---

## 7. Инициализировать БД

При первом запуске создайте таблицы и заполните тестовые данные:

```bash
# Создать таблицы (Alembic)
docker compose -f docker-compose.prod.yml exec app alembic upgrade head

# Заполнить тестовыми данными (опционально)
docker compose -f docker-compose.prod.yml exec app python seed.py
```

---

## 8. Настроить WhatsApp Webhook

1. Откройте [Meta for Developers](https://developers.facebook.com/)
2. Перейдите в ваше приложение → WhatsApp → Configuration
3. В поле **Callback URL** укажите:
   ```
   https://restomind.your-domain.com/api/whatsapp/webhook
   ```
4. В поле **Verify token** укажите значение `WHATSAPP_VERIFY_TOKEN` из `.env`
5. Нажмите **Verify and Save**
6. В **Webhook fields** подпишитесь на `messages`

**Проверка вебхука через curl (до подключения WhatsApp):**
```bash
# Проверить, что сервер принимает вебхуки
curl -X POST https://restomind.your-domain.com/api/whatsapp/webhook \
  -H "Content-Type: application/json" \
  -d '{"entry":[{"changes":[{"value":{"messages":[{"from":"77001234567","text":{"body":"Привет"}}]}}]}]}'
```

Ожидаемый ответ: `{"status":"ok"}`

**Проверка реального трафика:** отправьте сообщение на WhatsApp-номер бота. В логах должно появиться:
```bash
docker compose -f docker-compose.prod.yml logs -f app | grep "Сообщение от"
```

---

## 9. Админ-панель

Откройте в браузере:
```
https://restomind.your-domain.com/admin
```

Функционал:
- **Дашборд** — выручка, заказы, статистика
- **Канбан** — визуальный pipeline заказов (DRAFT → CONFIRMED → На кухне)
- **Live-чаты** — диалоги с клиентами в реальном времени через WebSocket
- **Перехват** — мгновенный переход в ручной режим (HUMAN_MODE)
- **Аналитика** — графики выручки, топ блюд, средний чек

---

## Обслуживание

### Обновление
```bash
cd ~/restomind
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

### Бэкап базы данных
```bash
docker compose -f docker-compose.prod.yml exec db pg_dump -U restomind restomind_db > backup_$(date +%Y%m%d).sql
```

### Восстановление из бэкапа
```bash
cat backup.sql | docker compose -f docker-compose.prod.yml exec -T db psql -U restomind restomind_db
```

### Просмотр логов
```bash
docker compose -f docker-compose.prod.yml logs -f app      # Приложение
docker compose -f docker-compose.prod.yml logs -f traefik   # Traefik (SSL)
docker compose -f docker-compose.prod.yml logs -f db        # PostgreSQL
```

### Перезапуск
```bash
docker compose -f docker-compose.prod.yml restart app
```

---

## Устранение неполадок

| Проблема | Решение |
|----------|---------|
| Traefik не получает сертификат | Проверьте DNS A-запись, порт 80 должен быть открыт |
| WhatsApp не доставляет сообщения | Проверьте Callback URL и Verify Token в Meta кабинете |
| WebSocket не подключается | Traefik автоматически проксирует WS — проверьте логи `app` |
| БД не запускается | Проверьте `POSTGRES_PASSWORD` в `.env`, удалите volume: `docker volume rm restomind_pgdata` |
