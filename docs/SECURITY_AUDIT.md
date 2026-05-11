# Multi-Tenant Security Audit

Дата: 2026-05-13  
Scope: изоляция данных по `organization_id` во всех сервисах и API.

---

## Гарантии изоляции (VERIFIED)

### API Layer
- **139 мест** в `app/api/admin/` используют `admin_org_from_session(request)` для
  извлечения `org_id` из cookie-сессии — оператор видит только свою организацию.
- `require_admin_session_active()` проверяет: сессия активна, org активна, staff активен,
  billing не приостановлен.
- `StaffUser.tenant_owner_id` — сотрудники с ролью tenant_owner имеют доступ к
  нескольким филиалам в рамках одного тенанта, но не к чужим тенантам.

### Data Layer (критические таблицы)
| Таблица | Изоляция |
|---|---|
| orders | `organization_id` обязателен, индексирован; `orders_tenant_clause()` |
| chat_logs | `organization_id` обязателен |
| menu_items | `organization_id` обязателен + уникальный ключ `(org_id, iiko_product_id)` |
| escalation_events | `organization_id` обязателен |
| operational_insights | `organization_id` обязателен |
| ai_usage_logs | `organization_id` обязателен |
| pipeline_latency_logs | `organization_id` обязателен (P4 sprint) |
| business_recommendations | `organization_id` обязателен (P4 sprint) |
| payment_transactions | `organization_id` обязателен |
| organization_payment_configs | `organization_id` обязателен, CASCADE delete |

### Автоматические тесты
`tests/test_multitenant_isolation.py` — 9 тестов, каждый создаёт 2 независимые
организации и проверяет что данные не пересекаются:
- `test_orders_isolated_by_org`
- `test_chat_logs_isolated`
- `test_menu_items_isolated`
- `test_escalation_events_isolated`
- `test_insights_isolated`
- `test_ai_usage_isolated`
- `test_pipeline_latency_isolated`
- `test_recommendations_isolated`
- `test_no_cross_org_escalation_via_phone`

---

## Известные ограничения (не критичные)

1. **Legacy NULL organization_id** — исторически некоторые ChatLog/MenuItem могут иметь
   `organization_id = NULL` (до мигращии мультитенантности). Обрабатываются через
   `orders_tenant_clause()` с fallback на глобальный `default_organization_id`.
   _Рекомендация:_ периодический запрос для поиска строк с NULL.

2. **Аудит-лог админских действий** — текущая реализация не хранит кто и когда изменил
   настройки (`OperationalInsight`, `BusinessRecommendation`, конфиги организации).
   _Рекомендация:_ добавить `AdminAuditLog` модель в P5 спринте.

3. **Export endpoint** — отсутствует endpoint для экспорта данных клиентов. При добавлении
   такого endpoint ОБЯЗАТЕЛЬНО проверять `organization_id` и логировать в AdminAuditLog.

---

## Рекомендации для следующего спринта (P5)

- [ ] `AdminAuditLog` модель: `(org_id, staff_id, action, resource, timestamp, ip)`
- [ ] Middleware для автологирования всех `POST/PUT/PATCH/DELETE /api/admin/*`
- [ ] Проверка NULL `organization_id` в существующих данных (скрипт диагностики)
- [ ] Rate limiting по `org_id` (сейчас только глобальный rate limit)
