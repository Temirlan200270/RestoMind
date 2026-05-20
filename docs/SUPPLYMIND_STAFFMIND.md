# SupplyMind & StaffMind epics

## SupplyMind

Scope: iiko Office inventory read -> draft purchase order.

| Step | Deliverable |
|------|-------------|
| 1 | Read-only stock snapshots by `organization_id` |
| 2 | `forecast_ingredient_runout` on real SKU data |
| 3 | Draft purchase order API |

Dependencies: Location RBAC, шина событий → `DailyOrgStats` (backfill / event-driven KPI).

Implemented bridge:

- `GET /api/admin/intelligence/os-dashboard` reads `inventory_stock_snapshots` first and returns real `stock_alerts[]`.
- If no inventory rows exist, OS dashboard falls back to `build_stock_alerts_stub()` as the old proxy signal.
- `POST /api/admin/intelligence/inventory/snapshots/bulk` upserts the latest stock read model per SKU.
- `GET /api/admin/intelligence/inventory/stock-alerts` exposes the stock alert feed directly.
- `POST /api/admin/intelligence/supplymind/drafts` creates a draft purchase order from real stock alerts.
- `GET /api/admin/intelligence/supplymind/drafts` lists generated drafts.

## StaffMind

Scope: WhatsApp onboarding flow for staff shifts and tasks.

| Step | Deliverable |
|------|-------------|
| 1 | `StaffUser.meta_json` role metadata + `assigned_location_ids` |
| 2 | Staff onboarding sessions over WhatsApp-compatible API |
| 3 | Knowledge-base Q&A for new staff |

Implemented bridge:

- `POST /api/admin/intelligence/staffmind/onboarding` starts a staff onboarding session.
- `POST /api/admin/intelligence/staffmind/onboarding/{session_id}/message` answers from `KnowledgeItem`.
- `GET /api/admin/intelligence/staffmind/onboarding` lists active/recent sessions.

## Status

SupplyMind and StaffMind have **backend MVP APIs** (tests in [`tests/test_ultimate_platform_sprint.py`](../tests/test_ultimate_platform_sprint.py)). Full iiko Office stock sync and **admin UI screens** remain backlog — см. [`docs/REMAINING_UPDATES.md`](REMAINING_UPDATES.md).
