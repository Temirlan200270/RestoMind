# Multi-Tenant Security Audit

Дата: 2026-06-09 (обновлено)  
Scope: изоляция данных по `organization_id` / `location_id` во всех сервисах и API.

---

## Гарантии изоляции (VERIFIED)

### API Layer
- Admin routes используют `admin_org_from_session(request)` + `require_admin_session_active`.
- **Per-org rate limit:** `admin_org_rate_limit_middleware` — `rate:org:{id}` на POST/PATCH/PUT/DELETE `/api/admin/*` (кроме auth/ws).
- **Admin audit:** `admin_action_audit_middleware` → `audit_log` + WS `os.audit`.
- WebSocket: org-scoped Redis channel + `_ws_event_allowed_for_org`.
- Shift endpoints: `location_id` + `allowed_location_ids_for_staff` ([`analytics.py`](../app/api/admin/analytics.py)).

### Data Layer
| Таблица | Изоляция |
|---|---|
| orders, chat_logs, bookings | `*_tenant_clause()` + location filters |
| menu_items, knowledge | legacy `IS NULL` **только** `default_organization_id` |
| system_events, audit_log | `organization_id` обязателен |

### Postgres RLS (last line of defense, 2026-06)

Миграция `20260609_tenant_rls` — `FORCE ROW LEVEL SECURITY` + политика `tenant_isolation_*` на:
`orders`, `users`, `menu_items`, `system_events`, `operational_insights`, `bookings`, `upsell_rules`, `agent_action_proposals`.

| Механизм | Файл / поведение |
|---|---|
| Session setting | `app.organization_id` / `app.bypass_rls` через [`app/db/tenant_rls.py`](../app/db/tenant_rls.py) |
| Admin HTTP | [`app/middleware/tenant_rls.py`](../app/middleware/tenant_rls.py) — org из cookie-сессии |
| DB session | `apply_tenant_rls()` в [`app/db/session.py`](../app/db/session.py) на каждом `get_db` |
| Bypass | Workers, webhooks, tests — `app.bypass_rls=true` (не admin API) |

RLS **дополняет**, не заменяет Rule 9: все запросы по-прежнему фильтруются по `organization_id` в приложении.

### Tenant backfill (ops)
- `GET /api/admin/intelligence/tenant-scope-gaps` — диагностика NULL org/location
- `POST /api/admin/intelligence/tenant-scope-backfill` — backfill из users + default location
- Сервис: [`app/services/tenant_backfill.py`](../app/services/tenant_backfill.py)

### Автоматические тесты
- [`tests/test_multitenant_isolation.py`](../tests/test_multitenant_isolation.py) — 9 базовых тестов
- [`tests/test_tenant_hardening.py`](../tests/test_tenant_hardening.py) — Booking, SystemEvent, AIContextSnapshot, backfill, legacy clause
- CI heuristic: [`scripts/check_tenant_scope.py`](../scripts/check_tenant_scope.py)

---

## Известные ограничения

1. **Legacy NULL organization_id** — fallback через `legacy_null_org_visible()` только для `DEFAULT_ORGANIZATION_ID`. Рекомендация: `POST /tenant-scope-backfill` на prod после миграции.

2. **Location NULL** — до backfill строки с `location_id IS NULL` видны всем staff org. Backfill через tenant-scope-backfill.

3. **DailyOrgStats** — org-level only; per-location KPI через SQL rollup (`rollup_location_event_stats`), не `daily_location_stats` table.

4. **RLS bypass paths** — worker/webhook/superuser обходят row filters; admin API должен всегда задавать `app.organization_id`. Superuser в Postgres по-прежнему bypasses RLS (ожидаемо для managed DB admin).

5. **Export endpoint** — при добавлении обязателен org scope + audit log.

6. **Global KnowledgeItem** (`organization_id IS NULL`) — видны только default org (не всем tenants).

7. **Не все таблицы под RLS** — `organizations`, `staff_users`, `chat_logs` и др. пока только app-level scope; расширение политик — отдельный эпик.

---

## Рекомендации (P5+)

- [x] Postgres RLS policies на core tenant tables — `20260609_tenant_rls` (см. выше)
- [ ] RLS на `chat_logs`, `staff_users` и прочие таблицы без политик
- [ ] `daily_location_stats` materialized aggregates
- [ ] Расширить `check_tenant_scope.py` до AST-анализа (не только heuristic)
