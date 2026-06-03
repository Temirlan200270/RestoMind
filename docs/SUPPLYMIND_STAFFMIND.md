# SupplyMind & StaffMind epics

## SupplyMind

Scope: iiko Office inventory read → low-stock alerts → **internal purchase checklist** (not iiko PO export).

| Step | Deliverable | Status |
|------|-------------|--------|
| 1 | Read-only stock snapshots by `organization_id` (+ `location_id`) | ✅ MVP — manual bulk upsert |
| 2 | `forecast_ingredient_runout` / `stock_alerts` on real SKU data | ✅ when snapshots exist; else OS proxy stub |
| 3 | Draft purchase checklist API + admin UI | ✅ lifecycle + CSV + UI |
| 4 | **iiko Office pull sync** | ✅ code; staging with live Office |

Dependencies: Location RBAC, event bus → `DailyOrgStats` (KPI proxy until real stock).

### Implemented bridge (MVP)

- `GET /api/admin/intelligence/os-dashboard` reads `inventory_stock_snapshots` first → real `stock_alerts[]`; fallback `build_stock_alerts_stub()`.
- `POST /api/admin/intelligence/inventory/snapshots/bulk` — upsert read model per SKU.
- `GET /api/admin/intelligence/inventory/stock-alerts` — direct alert feed (location-aware).
- `POST /api/admin/intelligence/supplymind/drafts` — checklist from low-stock alerts.
- `GET /api/admin/intelligence/supplymind/drafts` — list drafts.
- Admin UI: `aiCenterTab=final_mile` — alerts, create draft.

### iiko Office inventory sync (implemented)

**Why separate from iiko Cloud:** [`IikoClient`](app/integrations/iiko_client.py) covers menu, deliveries, stop-lists — **not** warehouse balances.

**Ops gate:** iiko Office often sits on-prem behind NAT/firewall. Roll out warehouse sync as a one-venue pilot first (VPN/static IP/port forwarding agreed), and keep manual/bulk stock snapshots as the fallback for other restaurants.

| Component | Purpose |
|-----------|---------|
| [`iiko_office_client.py`](app/integrations/iiko_office_client.py) | Async httpx client; `fetch_stock_balances()` |
| [`iiko_inventory_sync.py`](app/services/iiko_inventory_sync.py) | Map iiko rows → `InventoryStockSnapshot` (`source=iiko_office`) |
| `POST /api/admin/inventory/sync-iiko` | Manual sync per org session |
| `GET /api/admin/inventory/sync-status` | `last_inventory_sync_at`, ok/error |
| ARQ `iiko_inventory_sync` | Cron ~every 6 hours |

**Per-org config** (`Organization.integration_config_json`):

```json
{
  "iiko_office": {
    "host": "https://office.example/iiko",
    "login": "api_user",
    "password_enc": "<encrypted>",
    "store_id": "<UUID склада>",
    "department_id": "<опционально>",
    "location_id": 12,
    "store_location_map": { "<store_uuid>": 12, "<другой_склад>": 15 }
  }
}
```

- **Admin UI:** Настройки → Подключения → блок «iiko Office (склад / SupplyMind)»; `GET/PATCH /api/admin/organization/iiko-office`.
- **`location_id` / `store_location_map`:** при sync остатки пишутся в `inventory_stock_snapshots` с привязкой к точке RestoMind (мульти-location сети).
- **RBAC:** `POST /inventory/sync-iiko`, мутации SupplyMind — `admin`/`manager`; `operator` — только GET (алерты, статус).

Secrets: same encryption pattern as Cloud iiko credentials ([`org_iiko.py`](app/services/org_iiko.py), [`org_iiko_office.py`](app/services/org_iiko_office.py)).

### Checklist decision — no iiko purchase order export

| Option | Decision |
|--------|----------|
| Export draft to iiko Office as supplier invoice / internal transfer | **Rejected for v1** — API variance, double-entry risk, needs kitchen workflow sign-off |
| **Internal checklist + CSV** | **Chosen** — `SupplyPurchaseDraft` stays in RestoMind; operator marks `approved` → `completed`; CSV for WhatsApp/supplier/kitchen |

**Statuses:** `draft` → `approved` → `completed` | `cancelled` (string field on `supply_purchase_drafts.status`).

**API (implemented):**

| Method | Path | Notes |
|--------|------|-------|
| GET | `/supplymind/drafts/{id}` | Single checklist |
| PATCH | `/supplymind/drafts/{id}` | `{ "status": "approved" }` etc. |
| GET | `/supplymind/drafts/{id}/export?format=csv` | Download for external use |

**UI:** labels «Чеклист закупки», «Согласовать», «Выполнено», «Скачать CSV» — not «Отправить в iiko».

Future: optional iiko export behind feature flag after pilot with one venue.

## StaffMind

Scope: WhatsApp onboarding flow for staff shifts and tasks.

| Step | Deliverable | Status |
|------|-------------|--------|
| 1 | `StaffUser.meta_json` role metadata + `assigned_location_ids` | ✅ `GET/POST/PATCH /api/admin/staff` + team UI |
| 2 | Staff onboarding sessions over WhatsApp-compatible API | ✅ MVP |
| 3 | Knowledge-base Q&A for new staff | ✅ MVP |

Implemented bridge:

- `POST /api/admin/intelligence/staffmind/onboarding` — start session.
- `POST /api/admin/intelligence/staffmind/onboarding/{session_id}/message` — answers from `KnowledgeItem`.
- `GET /api/admin/intelligence/staffmind/onboarding` — list sessions.
- Admin UI: [`_tab_settings_team.html`](app/templates/screens/_tab_settings_team.html).

## Status summary

| Module | Backend MVP | Integration epic | Admin UI |
|--------|-------------|------------------|----------|
| SupplyMind | ✅ snapshots + sync + checklist lifecycle | live iiko Office smoke | ✅ final_mile panel (item checks session-local) |
| StaffMind | ✅ sessions + Q&A | — | ✅ settings team + tracker UI (metrics partial) |

**Known UI/API gaps:** SupplyMind item checkbox persist; StaffMind `test_passed` / `questions_asked` in API; Voice call log `location_id` in payload. See [`docs/ROADMAP.md`](ROADMAP.md) backlog lines.

Tests: [`tests/test_ultimate_platform_sprint.py`](../tests/test_ultimate_platform_sprint.py), [`tests/test_iiko_inventory_sync.py`](../tests/test_iiko_inventory_sync.py) (lifecycle + RBAC).

Ops checklist: [`docs/FINAL_MILE_OPS_SIGNOFF.md`](FINAL_MILE_OPS_SIGNOFF.md).
