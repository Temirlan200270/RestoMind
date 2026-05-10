# Спринт: параллельные потоки A/B/C/D (2026-05-11)

**Связь с Roadmap:** пункты P0 / P1 / P1.5 / E0.1 в [`docs/ROADMAP.md`](../../ROADMAP.md). Галочки по задачам ставим **только** там.

## Цель

Запустить до пяти **независимых** линий разработки с минимальными конфликтами по файлам и понятным порядком мержей.

| Поток | Зона | Роль |
|-------|------|------|
| **A** | `webhooks.py`, `services/`, при необходимости миграции | Надёжность WhatsApp / dialog state / меню |
| **B** | `app/api/admin/*` (в т.ч. раскол `_monolith.py`) | API без смены поведения; новые эндпоинты |
| **C** | `templates/screens/`, `components/`, `src/css/admin-input.css`, `admin-app.js` | UX P1.5 |
| **D** | `docs/ui/baseline/`, скрипты съёмки | Документация UI после стабилизации верстки |

**Красная зона:** платежи — не смешивать с этими потоками без отдельного ТЗ.

---

## Пара 1 — A ∥ C

| Поток | Задача (Roadmap) | Файлы (ориентир) |
|-------|------------------|------------------|
| **A** | P0 «Source of Truth для dialog state» — helper + 4 точки только Redis | [`app/api/webhooks.py`](../../../app/api/webhooks.py) (`:699`, `:712`, `:983-984`, `:1021-1022`); новый helper рядом с [`app/services/dialog_mgr.py`](../../../app/services/dialog_mgr.py) или в `webhooks` через тонкий вызов сервиса |
| **C** | P1.5 «Skeletons + relative time» **или** «Tenant color stripe» | Skeletons: [`_skeleton.html`](../../../app/templates/components/_skeleton.html), тяжёлые табы; `fmt.timeAgo` в [`admin-app.js`](../../../app/static/js/admin-app.js). Stripe: [`admin-brand-tokens.js`](../../../app/static/js/admin-brand-tokens.js), шапка/сайдбар, сценарий `select-org` |

**Почему параллельно:** разные деревья; риск конфликта только если A трогает общий JS (не должен).

**DoD A:** все переходы состояния зеркалятся в БД + Redis по одному паттерну; `pytest -q` зелёный.

**DoD C:** smoke админки; при stripe — нет «мигания» чужого бренда после смены филиала.

---

## Пара 2 — A ∥ B

| Поток | Задача (Roadmap) | Файлы (ориентир) |
|-------|------------------|------------------|
| **A** | P0 «Data leak меню» / `load_available_menu` без `organization_id=None` | [`app/services/order_logic.py`](../../../app/services/order_logic.py), callers в ROADMAP; тесты [`tests/test_order_logic.py`](../../../tests/test_order_logic.py), [`tests/test_intent_phase18.py`](../../../tests/test_intent_phase18.py), [`tests/regression/test_upsell_anti_repeat.py`](../../../tests/regression/test_upsell_anti_repeat.py) |
| **B** | P1 **E0.1** — вынести **другой** кусок из [`_monolith.py`](../../../app/api/admin/_monolith.py) в подмодуль, поведение 1:1 | Новый файл в `app/api/admin/`, правка `__init__.py` / агрегации роутеров |

**Условие параллели:** PR по B **не** меняет `order_logic.py` и не трогает те же тесты, что A. Если нужен общий импорт — мержим сначала меньший PR.

**DoD A:** вызов без org → ошибка; регресс-тест «зов без org»; нет ветки legacy `MenuItem.organization_id IS NULL` без бэкфила (как в ROADMAP).

**DoD B:** дифф только перенос/импорты; OpenAPI/поведение идентичны; `pytest -q`.

---

## Пара 3 — B → C (контракт, затем UI)

| Этап | Ответственный | Содержание |
|------|---------------|------------|
| **3a** | B + короткое согласование | Зафиксировать JSON контракта (ниже) в этом файле или в PR-описании |
| **3b** | B | `POST /api/admin/menu/bulk-stoplist` (или согласованное имя), scope `organization_id`, идемпотентность не обязательна, но ошибки per-item допустимы |
| **3c** | C | [`_tab_menu.html`](../../../app/templates/screens/_tab_menu.html): чекбоксы, sticky-панель, мобильный long-press по ROADMAP |

### Черновик контракта (согласовать до кодирования UI)

**Запрос (пример):**

```json
{
  "action": "stop" | "unstop" | "set_category",
  "item_ids": [1, 2, 3],
  "category_id": 42
}
```

Поле `category_id` обязательно только для `set_category`.

**Ответ (пример):**

```json
{
  "ok": true,
  "updated": 3,
  "failed": [
    { "id": 2, "error": "not_found" }
  ]
}
```

**Инварианты:** все строки только в рамках текущей org из сессии; при нарушении — 403/404 как в остальном admin API.

---

## Пара 4 — A ∥ C

| Поток | Задача (Roadmap) | Файлы (ориентир) |
|-------|------------------|------------------|
| **A** | P0 «WhatsApp inbound dedupe» — убрать ранний Redis-preclaim до DB-claim | [`app/api/webhooks.py`](../../../app/api/webhooks.py) (~`:1587`); см. [`app/services/whatsapp_idempotency.py`](../../../app/services/whatsapp_idempotency.py) |
| **C** | P1.5 «Compact Kanban» | [`_tab_orders.html`](../../../app/templates/screens/_tab_orders.html), [`admin-app.js`](../../../app/static/js/admin-app.js) (`kanbanDensity`), [`src/css/admin-input.css`](../../../src/css/admin-input.css) (`ds-kanban-card--compact`), затем `npm run build:admin-css` |

**Почему параллельно:** нет пересечения путей.

**DoD A:** Redis остаётся кэш-проверкой после коммита `done`; нет потери сообщения при падении между шагами.

**DoD C:** переключатель Normal/Compact в `localStorage`; целевая плотность на 1440px — по критерию ROADMAP; Lighthouse при необходимости `npm run lh:admin`.

---

## Пара 5 — D (после крупного UI)

| Поток | Задача (Roadmap) | Когда стартовать |
|-------|------------------|------------------|
| **D** | Обновить [`docs/ui/baseline/`](../../ui/baseline/) (и при необходимости mobile-review) | После мержа **крупных** UI-изменений (Compact Kanban, stripe, skeletons), иначе скрины мгновенно устаревают |

Инструменты: [`scripts/run_admin_lighthouse.mjs`](../../../scripts/run_admin_lighthouse.mjs) / Playwright / MCP — как в ROADMAP. Старые PNG — в архивную подпапку с датой и ссылкой на коммит.

---

## Порядок мержей (рекомендация)

1. Мелкие изолированные PR (E0.1 кусок, stripe, dedupe) — как готовы.
2. Пара 3: сначала **B** (эндпоинт + тесты), затем **C**.
3. **D** — последним волном после стабилизации P1.5 кусков.

---

## Итог спринта (DoD)

- [ ] Каждая закрытая задача: `[x]` в [`docs/ROADMAP.md`](../../ROADMAP.md) + строка в [`CHANGELOG.md`](../../../CHANGELOG.md) `## [Unreleased]`.
- [ ] Backend: `pytest -q`.
- [ ] UI: smoke (логин → ключевой таб → консоль без ошибок).
- [ ] После спринта: удалить эту папку или вплавить выжимку в `docs/CONVENTIONS.md` / `UI_DESIGN_SYSTEM.md` по правилам [`docs/sprints/README.md`](../README.md).
