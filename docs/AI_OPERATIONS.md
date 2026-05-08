# AI Operations / Restaurant Intelligence

This document describes the first implementation slice for Restaurant Intelligence and Digital Twin.

## Product Names

- Code/API namespace: `intelligence`.
- Admin UI tabs: `AI-аналитик` and `Digital Twin`.
- We avoid hard-coding the product around the word "Copilot".

## API

```http
GET  /api/admin/intelligence/overview
POST /api/admin/intelligence/query
GET  /api/admin/intelligence/insights
PATCH /api/admin/intelligence/insights/{id}
GET  /api/admin/intelligence/digital-twin
POST /api/admin/intelligence/simulate
```

## Restaurant Intelligence MVP

The MVP focuses on revenue and orders:

- revenue;
- order count;
- average check;
- cancellations;
- top items;
- estimated lost revenue from cancellations.

Pipeline:

```text
manager question
  -> lightweight intent parser
  -> Python analytics summary
  -> deterministic explanation
  -> saved IntelligenceConversation / IntelligenceMessage
```

The LLM explanation layer can be added later, but the numeric calculations must remain in Python.

## Auto Insights

`OperationalInsight` stores insights displayed in the admin panel.

Initial insight types:

- `revenue_drop`;
- `orders_drop`;
- `cancellations_up`;
- `sales_stable`.

Statuses:

- `new`;
- `seen`;
- `resolved`;
- `dismissed`.

## Digital Twin MVP

`RestaurantStateSnapshot` stores a point-in-time operational state:

- active orders;
- draft orders;
- confirmed orders;
- cancellations today;
- revenue today;
- average check;
- queue size;
- operator load;
- kitchen load;
- stop-list count.

The first simulation estimates operator capacity:

```text
orders per hour + operators + average check
  -> load percent
  -> expected wait
  -> cancellation risk
  -> lost revenue estimate
```

No ML is used in the MVP. The first model is intentionally heuristic and explainable.
