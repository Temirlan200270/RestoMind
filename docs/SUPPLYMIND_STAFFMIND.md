# SupplyMind & StaffMind — epics (Sprint C)

## SupplyMind (Backend lead)

**Scope:** iiko Office inventory read → draft накладной.

| Этап | Deliverable |
|------|-------------|
| 1 | Read-only остатки по `organization_id` |
| 2 | `forecast_ingredient_runout` на реальных SKU (замена `stock_alerts` stub) |
| 3 | Draft накладной в UI |

**Зависимости:** Location RBAC, event bus `DailyOrgStats`.

**Текущий мостик:** `GET /os-dashboard` → `stock_alerts[]` из `build_stock_alerts_stub()`.

## StaffMind (Product + Backend)

**Scope:** WhatsApp onboarding flow для персонала (смены, задачи).

| Этап | Deliverable |
|------|-------------|
| 1 | `StaffUser.meta_json` роли + `assigned_location_ids` |
| 2 | Отдельный WA номер / webhook route для staff |
| 3 | Мини-дашборд смены в админке |

## Статус

Epic backlog — старт после закрытия Sprint A+B в `docs/ROADMAP.md`.
