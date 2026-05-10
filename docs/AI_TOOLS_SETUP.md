# Настройка AI-инструментов для работы над RestoMind

Документ описывает, как настроить **Claude Code**, **Cursor** и подобные агенты, чтобы они работали по одному набору правил и MCP-инструментам.

Цель — чтобы любой агент (Claude Code, Cursor Composer / Background Agent, Aider и т.д.) поднимал одинаковый контекст из репо и работал по одному набору правил.

---

## 1. Общая схема

```
RestoMind/
├── .cursor/                   # настройки для Cursor
│   ├── mcp.json               # MCP-серверы (browser, context7)
│   └── rules/
│       ├── restomind-zones.mdc   # alwaysApply: true — общие правила репо
│       ├── restomind-ai.mdc      # alwaysApply: true — соло-workflow (статусы в ROADMAP)
│       └── ui-redesign.mdc       # globs: app/templates/**, app/static/** — UI-правила
├── CLAUDE.md (опционально)    # специфика Claude Code
├── docs/
│   ├── ROADMAP.md             # единственный трекер задач/статусов (P0–P4)
│   ├── CONVENTIONS.md         # инварианты + §8 шаблоны Jinja / миграции
│   ├── sprints/               # временные мини‑родмапы/чеклисты по спринтам
│   ├── UI_DESIGN_SYSTEM.md    # спецификация UI и компонентов
│   ├── UI_MAP.md              # карта админ UI (screens + компоненты)
│   ├── AI_OPERATIONS.md       # Intelligence / операционка
│   ├── EVENT_ARCHITECTURE.md  # durable SystemEvent
│   ├── WHATSAPP_PHASE13_TEMPLATES.md
│   └── AI_TOOLS_SETUP.md      # этот файл
└── CHANGELOG.md               # история (агенты дописывают в [Unreleased])
```

---

## 2. Cursor

### 2.1. MCP-серверы

В репозитории уже создан [`.cursor/mcp.json`](../.cursor/mcp.json) с тремя серверами:

- **`chrome-devtools`** — управление локальным Chrome для UI-верификации (скрины, клики, console).
- **`playwright`** — альтернатива: headless-браузер для автоматизации сценариев.
- **`context7`** — актуальная документация для библиотек / SDK.

Для активации:

1. Установить Node.js 18+ (нужен для `npx`).
2. Открыть Cursor → Settings → MCP → проверить, что серверы видны как "Active".
3. Перезапустить Cursor если требуется.

При первом запуске Chrome DevTools MCP попросит указать порт (обычно `localhost:8000` для нашего dev-сервера) — соглашаемся.

### 2.2. Cursor Rules

Два файла в [`.cursor/rules/`](../.cursor/rules/):

- **`restomind-zones.mdc`** (`alwaysApply: true`) — общие правила репо: запреты на правки платёжных вебхуков, контракты API, зоны двух агентов. Подключается к **каждому** запросу агента.
- **`ui-redesign.mdc`** (`globs: app/templates/**, app/static/**`) — правила UI-редизайна: запрет inline Tailwind, использование макросов, AI Value Visibility. Подключается **автоматически** при работе с шаблонами и фронтом.

Cursor подхватывает их сам — отдельно ничего делать не нужно.

### 2.3. Workflow в Cursor

| Режим | Когда использовать |
|-------|--------------------|
| **Composer (Cmd/Ctrl+I)** | Интерактивная работа: «Открой docs/ROADMAP.md и сделай задачу X». |
| **Chat (Cmd/Ctrl+L)** | Q&A, обсуждение, ревью кода без изменений. |
| **Background Agent** | Долгие задачи без присмотра (миграция раздела, генерация компонентов). Работает в облачном Linux-контейнере, требует push в GitHub-ветку. MCP-серверы доступны. |

### 2.4. Параллельная работа (если нужно)

Если запускаете два агента одновременно — давайте им **разные вертикальные задачи** (не один и тот же файл). Статусы отмечаем только в `docs/ROADMAP.md`, факт — в `CHANGELOG.md`.

### 2.5. Скрины и визуальная верификация

- **Drag-drop** скрина в окно чата — Cursor понимает изображения с конца 2024.
- **Через MCP** — агент сам открывает страницу через Chrome DevTools MCP и делает скрин. Удобнее для итерации.
- **Baseline** — лежат в `docs/ui/baseline/*.png` (создаются в Phase U0 плана редизайна).
- **Mobile review (авто)** — `python scripts/capture_admin_mobile_review.py`: несколько viewport, вывод в `docs/ui/mobile-review/`. Перед запуском подними админку локально или задай `MOBILE_REVIEW_BASE_URL` (см. шапку скрипта).

---

## 3. Claude Code

### 3.1. MCP-серверы

Claude Code использует `~/.claude/.mcp.json` (глобально) или `.mcp.json` в корне репо. Содержимое аналогично `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "chrome-devtools": {
      "command": "npx",
      "args": ["-y", "chrome-devtools-mcp@latest"]
    },
    "playwright": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp@latest"]
    }
  }
}
```

После сохранения — `/mcp` в Claude Code → проверить статус.

### 3.2. CLAUDE.md (опционально)

Если хочется специфичные для Claude Code инструкции (slash-команды, примеры) — создаётся `CLAUDE.md` в корне. Для общих правил репо достаточно `.cursor/rules/*.mdc`.

### 3.3. Skills и Plan Mode

- `/plan` режим — для длинных задач с явным согласованием плана перед исполнением. Используется для редизайна, миграций, рефакторингов.
- Skills (например, `update-config`, `simplify`) — встроенные мини-программы, активируются по контексту.

---

## 4. Универсальные принципы для любого агента

1. **Перед началом** — читать `docs/ROADMAP.md` (задачи/статусы) и `.cursor/rules/*.mdc` (запреты/стандарты).
2. **При изменениях** — дописывать в `## [Unreleased]` секцию `CHANGELOG.md`, никогда не переписывать существующие записи.
3. **При закрытии задачи** — обновлять статус в `docs/ROADMAP.md`.
4. **Для UI** — опираться на `docs/UI_DESIGN_SYSTEM.md`; новые блоки только через макросы из `app/templates/components/`.
5. **Не трогать платежи** (`app/api/payment_webhook.py`, `app/services/payment_*`) без явного согласования.
6. **`pytest -q` перед PR** для backend-изменений; smoke в браузере для UI.
7. **Никаких `--force`, `--no-verify`, `--no-gpg-sign`** push без явного запроса от пользователя.

---

## 5. FAQ

**Q: Можно ли перейти на Cursor целиком и отказаться от Claude Code?**
Да. Все планы в репо, Cursor читает их и rules. Единственное отличие — у Claude Code есть удобный Plan Mode и Skills, у Cursor — Composer и Background Agent. Выбор по предпочтению.

**Q: Чем Cursor Background Agent лучше Claude Code?**
Background Agent работает в облаке Cursor (Linux-контейнер) без присмотра — идеально для ночного прогона миграции. Claude Code — локальный, требует открытого терминала. Для интерактивной работы оба сравнимы.

**Q: Нужно ли коммитить `.cursor/`?**
Да. Это часть конфигурации проекта, как `pytest.ini` или `.editorconfig`. Каждый разработчик / агент получает одинаковые MCP-серверы и rules.

**Q: Что если у меня нет Node.js для `npx`?**
MCP-серверы можно установить глобально (`npm i -g chrome-devtools-mcp`) и заменить команды в `mcp.json` на абсолютные пути. Но проще поставить Node.js.

**Q: Как запускать задачи по roadmap?**
Открыть Composer (Cmd/Ctrl+I), сказать: «Открой `docs/ROADMAP.md`, возьми задачу `<название>` и выполни». В конце: отметь `[x]` в `docs/ROADMAP.md` и допиши в `CHANGELOG.md`.
