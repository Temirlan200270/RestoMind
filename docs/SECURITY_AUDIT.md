# Multi-Tenant Security Audit

Дата: 2026-05-25 (обновлено)  
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

4. **Postgres RLS** — не включён; изоляция на уровне приложения. Отдельный enterprise-эпик для Supabase RLS.

5. **Export endpoint** — при добавлении обязателен org scope + audit log.

6. **Global KnowledgeItem** (`organization_id IS NULL`) — видны только default org (не всем tenants).

---

## Рекомендации (P5+)

- [ ] Postgres RLS policies на `orders`, `chat_logs`, `users` (Supabase)
- [ ] `daily_location_stats` materialized aggregates
- [ ] Расширить `check_tenant_scope.py` до AST-анализа (не только heuristic)
