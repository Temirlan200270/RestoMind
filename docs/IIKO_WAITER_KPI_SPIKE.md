# iiko Waiter KPI — API spike & field mapping

Документ фиксирует контракт ETL **KPI офiciантов** (P3 Growth). Обновлять после live smoke на staging org.

## Источники данных

| Источник | Когда доступен | KPI |
|----------|----------------|-----|
| **iiko Cloud** `POST /api/1/deliveries/by_delivery_date_and_status` | `Organization` + iiko Cloud creds | Оператор/courier доставки |
| **iiko Office** `GET /resto/api/v2/reports/sales/waiters` | `integration_config_json.iiko_office` | Официанты зала (hall) |

Graceful degradation: только Cloud → UI badge «KPI зала: подключите iiko Office».

## Cloud deliveries — mapping

Payload: `ordersByOrganizations[].orders[]` → entry с вложенным `order`.

| Поле iiko (приоритет) | Наша модель |
|----------------------|-------------|
| `order.operator.id` / `order.operator.name` | `waiter_iiko_id`, `waiter_name`, `source=cloud_delivery` |
| `order.waiter.id` / `order.waiter.name` | то же |
| `order.courierInfo.courier.id` / `.name` | fallback courier |
| `order.sum` / `order.total` / `order.payment.sum` | `total_revenue_kzt` |
| `order.status` = `Cancelled` | `cancelled_orders += 1`, revenue не считаем |
| `order.numberOfPersons` / `order.guestsCount` | `guests_count` |
| `order.completeBefore` − `order.whenCreated` | `avg_service_time_min` (если оба есть) |
| `order.deliveryDate` / `order.whenCreated` | локальная дата org TZ |

## iiko Office waiter report — mapping

Fixture: [`tests/fixtures/iiko_office/waiter_sales.json`](../tests/fixtures/iiko_office/waiter_sales.json)

| Поле iiko | Наша модель |
|-----------|-------------|
| `waiterId` / `employeeId` | `waiter_iiko_id` |
| `waiterName` / `employeeName` | `waiter_name`, `source=office_report` |
| `ordersCount` / `orders` | `orders_served` |
| `sum` / `revenue` | `total_revenue_kzt` |
| `guests` / `guestCount` | `guests_count` |
| `cancellations` / `cancelledOrders` | `cancelled_orders` |
| `avgServiceTimeMin` | `avg_service_time_min` |

## Live smoke checklist (ops)

- [ ] Cloud: сохранить 1-day raw JSON в `scripts/samples/iiko_deliveries_sample.json`
- [ ] Office: сохранить waiter report sample (или подтвердить 404 → delivery-only MVP)
- [ ] Обновить таблицы mapping выше реальными ключами
- [ ] Sign-off в [`FINAL_MILE_OPS_SIGNOFF.md`](FINAL_MILE_OPS_SIGNOFF.md) §A (рядом с inventory)

## Код

- Sync: [`app/services/iiko_waiter_kpi_sync.py`](../app/services/iiko_waiter_kpi_sync.py)
- Models: `waiter_registry`, `waiter_kpi_daily`, `iiko_sync_runs`
- API: [`app/api/admin/waiter_kpi.py`](../app/api/admin/waiter_kpi.py)
