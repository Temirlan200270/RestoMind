# Owner Dashboard — Техническое задание для реализации

> **[FULFILLED — 2026-05-19]** Все требования этого ТЗ реализованы в рамках Sprint A (Owner Dashboard) и Phase 5 (OS Behavior). Актуальное состояние: [`docs/OS_TRANSITION_PLAN.md`](OS_TRANSITION_PLAN.md), трекер задач: [`docs/ROADMAP.md`](ROADMAP.md). Этот документ сохранён как исторический артефакт — не редактировать.

---

## Контекст

RestoMind — мультитенантный FastAPI-бэкенд + Alpine.js/Jinja2 фронт.
Все изменения — только по `organization_id` (мультитенантная изоляция обязательна).
CSS: использовать `ds-*` классы из UI Design System, не утилиты `brand-*`.
Tailwind собирается через `npm run build:admin-css` из `src/css/admin-input.css` → `app/static/css/admin.css`.
Не редактировать `admin.css` напрямую.

Аудит показал: дашборд владельца отвечает на 4 вопроса частично.
Задача — довести каждый до рабочего состояния.

---

## Задача 1 — Прогноз выручки до конца недели (Q1: Деньги)

### Проблема
`/api/admin/stats` возвращает только ретроспективу: `today_revenue`, `yesterday_revenue`, `daily_series` (7 дней).
Прогноза «сколько заработаю до пятницы» нет вообще.

### Что сделать

#### 1.1 Бэкенд — новое поле в `/api/admin/stats`

Файл: `app/api/admin/analytics.py`, функция `dashboard_stats` (строка ~672).

В конец возвращаемого словаря добавить поле `week_forecast`:

```python
def _linear_week_forecast(daily_series: list[dict], org_tz: str = "Asia/Almaty") -> dict | None:
    """
    Линейная экстраполяция выручки до конца текущей недели (пн–вс).
    daily_series: список {"date": "YYYY-MM-DD", "revenue": float, "orders": int}
    Возвращает:
      {
        "forecast_revenue": float,      # прогноз суммарной выручки за неделю
        "earned_so_far": float,         # уже заработано с начала недели
        "days_remaining": int,          # дней до конца недели включая сегодня
        "days_elapsed": int,            # дней прошло (с данными)
        "confidence": "low"|"medium"|"high",  # low <3 дней, medium 3-5, high 5+
        "daily_avg": float,             # средний доход за прошедшие дни
      }
    """
    import zoneinfo
    from datetime import date, timedelta

    tz = zoneinfo.ZoneInfo(org_tz)
    today = date.today()
    # Найти начало текущей недели (понедельник)
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    # Фильтруем только дни текущей недели из daily_series
    week_days = {
        row["date"]: float(row.get("revenue") or 0)
        for row in daily_series
        if week_start.isoformat() <= row["date"] <= today.isoformat()
    }

    if not week_days:
        return None

    days_elapsed = len(week_days)
    earned = sum(week_days.values())
    daily_avg = earned / days_elapsed if days_elapsed else 0

    days_remaining = (week_end - today).days  # дней после сегодня
    forecast = earned + daily_avg * days_remaining

    confidence = "low" if days_elapsed < 3 else ("medium" if days_elapsed < 5 else "high")

    return {
        "forecast_revenue": round(forecast, 2),
        "earned_so_far": round(earned, 2),
        "days_remaining": days_remaining,
        "days_elapsed": days_elapsed,
        "confidence": confidence,
        "daily_avg": round(daily_avg, 2),
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
    }
```

В `dashboard_stats` после формирования `daily_series` добавить:

```python
result["week_forecast"] = _linear_week_forecast(result["daily_series"])
```

#### 1.2 Фронтенд — новая карточка на дашборде

Файл: `app/templates/screens/_tab_dashboard.html`, блок с KPI-карточками (строки ~52-78, грид `grid-cols-2 lg:grid-cols-4`).

Заменить карточку «Время команды» (4-ю) или добавить 5-ю в отдельную строку:

```html
<!-- Прогноз до конца недели -->
<div class="ds-card p-4" x-show="dashStats.week_forecast" x-cloak>
  <div class="text-xs text-gray-500 mb-1">Прогноз до конца недели</div>
  <div class="text-2xl font-bold text-gray-900"
       x-text="fmt.money(dashStats.week_forecast?.forecast_revenue ?? 0)"></div>
  <div class="text-xs text-gray-400 mt-1">
    Заработано: <span x-text="fmt.money(dashStats.week_forecast?.earned_so_far ?? 0)"></span>
    · ещё <span x-text="dashStats.week_forecast?.days_remaining ?? 0"></span> дн.
  </div>
  <div class="text-xs mt-1"
       :class="{
         'text-amber-500': dashStats.week_forecast?.confidence === 'low',
         'text-gray-400': dashStats.week_forecast?.confidence !== 'low'
       }"
       x-text="{low: 'Мало данных (< 3 дней)', medium: 'Средняя точность', high: 'Высокая точность'}[dashStats.week_forecast?.confidence] ?? ''">
  </div>
</div>
```

`dashStats` уже загружается в `loadDashStats()` в `admin-app.js` — новое поле появится автоматически.

---

## Задача 2 — Вынести метрики эффективности бота на главный экран (Q2)

### Проблема
Метрики `ai_time_saved_hours`, `escalation_rate`, `bot_orders vs total_orders` живут в двух разных вкладках (Dashboard + Intelligence) и не дают сразу ответа «насколько бот разгрузил людей».

### Что сделать

#### 2.1 Бэкенд — обогатить `/api/admin/stats`

Файл: `app/api/admin/analytics.py`, функция `dashboard_stats`.

Уже возвращает: `ai_time_saved_hours`, `ai_messages_today`, `ai_revenue_share_pct`.
Добавить поля (данные уже есть в той же функции, просто не возвращаются):

```python
# В конец result добавить:
result["bot_handled_pct"] = round(
    100 * result.get("bot_orders", 0) / result["today_orders"], 1
) if result.get("today_orders") else None

result["escalations_today"] = result.get("takeover_orders", 0)
```

Если `bot_orders` и `takeover_orders` не считаются в `dashboard_stats` — добавить запрос:

```python
# Заказы за сегодня, созданные ботом (без escalation)
from app.db.models import EscalationEvent

esc_count = await db.scalar(
    select(func.count(EscalationEvent.id)).where(
        EscalationEvent.organization_id == org_id,
        EscalationEvent.created_at >= today_start,
    )
) or 0
result["escalations_today"] = esc_count
```

#### 2.2 Фронтенд — блок «ИИ vs Люди» на дашборде

Файл: `app/templates/screens/_tab_dashboard.html`.

После блока KPI-карточек (после строки ~78), перед блоком «Сейчас» (строка ~80), добавить:

```html
<!-- ИИ-эффективность: компактная строка -->
<div class="ds-card p-3 flex flex-wrap gap-4 items-center text-sm" x-show="dashStats.ai_time_saved_hours">
  <div class="flex items-center gap-1.5">
    <span class="text-gray-500">Бот обработал</span>
    <span class="font-bold text-gray-900" x-text="(dashStats.bot_handled_pct ?? '—') + '%'"></span>
    <span class="text-gray-400">заказов</span>
  </div>
  <div class="w-px h-4 bg-gray-200"></div>
  <div class="flex items-center gap-1.5">
    <span class="text-gray-500">Сэкономил команде</span>
    <span class="font-bold text-gray-900" x-text="(dashStats.ai_time_saved_hours ?? 0) + ' ч'"></span>
  </div>
  <div class="w-px h-4 bg-gray-200"></div>
  <div class="flex items-center gap-1.5">
    <span class="text-gray-500">Эскалаций сегодня</span>
    <span class="font-bold"
          :class="(dashStats.escalations_today ?? 0) > 3 ? 'text-amber-600' : 'text-gray-900'"
          x-text="dashStats.escalations_today ?? 0"></span>
  </div>
</div>
```

---

## Задача 3 — Воронка потерь и отток клиентов (Q3: Проблемы)

### Проблема
Нет анализа: «где теряем клиентов». Есть только счётчик отмен.
Нужно:
- **Воронка drop-off**: диалогов → черновиков → завершённых заказов
- **Отток**: постоянные клиенты, не заказывавшие N дней

### Что сделать

#### 3.1 Бэкенд — новый endpoint `/api/admin/funnel`

Файл: `app/api/admin/analytics.py` (добавить новую функцию).

```python
@router.get("/funnel")
async def admin_funnel(
    request: Request,
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Воронка потерь и отток клиентов.
    Возвращает:
      - funnel: диалогов → черновиков → подтверждённых заказов
      - churn: клиенты без заказов N+ дней (из тех, у кого был хотя бы 1 заказ)
    """
    org_id = request.session.get("organization_id")
    if not org_id:
        raise HTTPException(403)

    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # 1. Уникальных пользователей с хотя бы 1 сообщением за период
    dialogs_count = await db.scalar(
        select(func.count(func.distinct(ChatLog.user_id))).where(
            ChatLog.organization_id == org_id,
            ChatLog.created_at >= cutoff,
            ChatLog.role == "user",
        )
    ) or 0

    # 2. Уникальных пользователей с черновиком за период
    drafts_count = await db.scalar(
        select(func.count(func.distinct(Order.user_id))).where(
            Order.organization_id == org_id,
            Order.created_at >= cutoff,
        )
    ) or 0

    # 3. Уникальных пользователей с завершённым заказом
    completed_count = await db.scalar(
        select(func.count(func.distinct(Order.user_id))).where(
            Order.organization_id == org_id,
            Order.created_at >= cutoff,
            Order.status.in_(["confirmed", "sent_to_iiko", "completed"]),
        )
    ) or 0

    # 4. Отток: у кого был хотя бы 1 завершённый заказ, но не заказывал days+ дней
    churn_cutoff = datetime.now(timezone.utc) - timedelta(days=30)  # порог — 30 дней
    # Пользователи с заказами вообще (за всё время)
    ever_ordered = select(func.distinct(Order.user_id)).where(
        Order.organization_id == org_id,
        Order.status.in_(["confirmed", "sent_to_iiko", "completed"]),
    )
    # Из них — те, кто заказывал недавно
    recently_ordered = select(func.distinct(Order.user_id)).where(
        Order.organization_id == org_id,
        Order.created_at >= churn_cutoff,
        Order.status.in_(["confirmed", "sent_to_iiko", "completed"]),
    )
    churned_count = await db.scalar(
        select(func.count()).select_from(
            select(func.distinct(Order.user_id))
            .where(
                Order.organization_id == org_id,
                Order.status.in_(["confirmed", "sent_to_iiko", "completed"]),
                Order.user_id.not_in(recently_ordered),
            )
            .subquery()
        )
    ) or 0

    # Конверсии
    dialog_to_draft = round(100 * drafts_count / dialogs_count, 1) if dialogs_count else None
    draft_to_order = round(100 * completed_count / drafts_count, 1) if drafts_count else None
    dialog_to_order = round(100 * completed_count / dialogs_count, 1) if dialogs_count else None

    return {
        "ok": True,
        "period_days": days,
        "funnel": {
            "dialogs": dialogs_count,
            "drafts": drafts_count,
            "completed": completed_count,
            "dialog_to_draft_pct": dialog_to_draft,
            "draft_to_order_pct": draft_to_order,
            "dialog_to_order_pct": dialog_to_order,
        },
        "churn": {
            "churned_count": churned_count,
            "churn_threshold_days": 30,
            "label": f"Не заказывали 30+ дней",
        },
    }
```

Зарегистрировать роут там же, где остальные (`@router.get`). Проверить, что `ChatLog` и `Order` импортированы.

#### 3.2 Фронтенд — блок «Где теряем» в дашборде

Файл: `app/templates/screens/_tab_dashboard.html`.

Добавить после блока эффективности (Задача 2):

```html
<!-- Воронка потерь -->
<div class="ds-card p-4" x-data="{ funnelData: null }"
     x-init="fetch('/api/admin/funnel?days=7', {credentials:'include'})
       .then(r=>r.json()).then(d=>{ funnelData = d })">
  <div class="text-sm font-semibold text-gray-700 mb-3">Где теряем клиентов (7 дней)</div>

  <template x-if="funnelData?.funnel">
    <div class="space-y-2">
      <!-- Воронка: 3 шага -->
      <div class="flex items-center gap-2 text-sm">
        <span class="w-28 text-gray-500">Диалогов</span>
        <span class="font-bold text-gray-900" x-text="funnelData.funnel.dialogs"></span>
      </div>
      <div class="flex items-center gap-2 text-sm">
        <span class="w-28 text-gray-500">→ Черновиков</span>
        <span class="font-bold" x-text="funnelData.funnel.drafts"></span>
        <span class="text-xs px-1.5 py-0.5 rounded bg-amber-50 text-amber-700"
              x-show="funnelData.funnel.dialog_to_draft_pct !== null"
              x-text="(funnelData.funnel.dialog_to_draft_pct ?? 0) + '%'"></span>
      </div>
      <div class="flex items-center gap-2 text-sm">
        <span class="w-28 text-gray-500">→ Заказов</span>
        <span class="font-bold text-gray-900" x-text="funnelData.funnel.completed"></span>
        <span class="text-xs px-1.5 py-0.5 rounded"
              :class="(funnelData.funnel.draft_to_order_pct ?? 0) < 50 ? 'bg-red-50 text-red-700' : 'bg-emerald-50 text-emerald-700'"
              x-show="funnelData.funnel.draft_to_order_pct !== null"
              x-text="(funnelData.funnel.draft_to_order_pct ?? 0) + '%'"></span>
      </div>

      <!-- Отток -->
      <div class="mt-3 pt-3 border-t border-gray-100 flex items-center gap-2 text-sm"
           x-show="funnelData.churn?.churned_count > 0">
        <span class="text-red-500">⚠</span>
        <span class="text-gray-600">Отток:</span>
        <span class="font-bold text-red-600" x-text="funnelData.churn?.churned_count"></span>
        <span class="text-gray-500" x-text="funnelData.churn?.label"></span>
      </div>
    </div>
  </template>

  <template x-if="!funnelData">
    <div class="text-sm text-gray-400">Загрузка…</div>
  </template>
</div>
```

---

## Задача 4 — Рекомендации с ROI-ранжированием на главном экране (Q4: Действия)

### Проблема
Рекомендации (`/admin/intelligence/recommendations`) уже есть: `product_boost`, `pricing_adj`, `geo_expansion`, `stoplist_impact`.
Но они живут только во вкладке Intelligence. На главном дашборде — только инциденты (`hero_actions`).
Нет ранжирования по ожидаемому ROI.

### Что сделать

#### 4.1 Бэкенд — добавить `top_actions` в `/api/admin/stats`

Файл: `app/api/admin/analytics.py`, функция `dashboard_stats`.

В конец функции, перед `return result`, добавить:

```python
# Top-3 рекомендации, отсортированные по expected_impact_kzt desc
from app.db.models import BusinessRecommendation  # уже должна быть в models
recs = (await db.execute(
    select(BusinessRecommendation)
    .where(
        BusinessRecommendation.organization_id == org_id,
        BusinessRecommendation.status.in_(["new", "viewed"]),
    )
    .order_by(
        BusinessRecommendation.expected_impact_kzt.desc().nulls_last(),
        BusinessRecommendation.created_at.desc(),
    )
    .limit(3)
)).scalars().all()

result["top_actions"] = [
    {
        "id": r.id,
        "type": r.recommendation_type,
        "title": r.title,
        "body": r.body,
        "impact_kzt": r.expected_impact_kzt,
        "confidence_pct": r.confidence_pct,
    }
    for r in recs
]
```

Если модель называется иначе — проверить в `app/db/models.py` по `recommendation_type`.

#### 4.2 Фронтенд — блок «Что сделать сейчас» на дашборде

Файл: `app/templates/screens/_tab_dashboard.html`.

Существующий блок «Сейчас» (`hero_actions`, строки ~80-105) дополнить рекомендациями под инцидентами:

```html
<!-- Рекомендации с ROI (под hero_actions) -->
<template x-if="(dashStats.top_actions || []).length > 0">
  <div class="mt-3 space-y-2">
    <div class="text-xs font-semibold text-gray-400 uppercase tracking-wide">Рекомендации</div>
    <template x-for="action in (dashStats.top_actions || [])" :key="action.id">
      <div class="flex items-start gap-3 p-2.5 rounded-xl border border-gray-100 bg-gray-50">
        <div class="flex-1 min-w-0">
          <div class="text-sm font-medium text-gray-800 truncate" x-text="action.title"></div>
          <div class="text-xs text-gray-500 mt-0.5 line-clamp-1" x-text="action.body"></div>
        </div>
        <div x-show="action.impact_kzt" class="text-right shrink-0">
          <div class="text-xs font-bold text-emerald-700"
               x-text="'+' + fmt.money(action.impact_kzt)"></div>
          <div class="text-[10px] text-gray-400">прогноз</div>
        </div>
      </div>
    </template>
    <button type="button" @click="switchTab('intelligence')"
            class="w-full text-xs text-gray-400 hover:text-gray-600 text-center py-1">
      Все рекомендации →
    </button>
  </div>
</template>
```

---

## Проверка реализации

После внесения изменений — проверить по каждой задаче:

**Задача 1 (прогноз):**
```
GET /api/admin/stats
→ week_forecast.forecast_revenue: число > 0
→ week_forecast.confidence: "low" | "medium" | "high"
→ На дашборде появляется карточка "Прогноз до конца недели"
```

**Задача 2 (эффективность):**
```
GET /api/admin/stats
→ bot_handled_pct: число 0–100
→ escalations_today: целое число
→ На дашборде виден блок "Бот обработал X% заказов"
```

**Задача 3 (воронка):**
```
GET /api/admin/funnel?days=7
→ funnel.dialogs >= funnel.drafts >= funnel.completed
→ dialog_to_draft_pct + draft_to_order_pct — числа
→ churn.churned_count — целое
→ На дашборде виден блок "Где теряем клиентов"
```

**Задача 4 (рекомендации):**
```
GET /api/admin/stats
→ top_actions: массив 0–3 элементов
→ Каждый: id, type, title, impact_kzt
→ На дашборде под hero_actions видны рекомендации с "+ X ₸"
```

**Тесты (если есть pytest + db_with_menu фикстура):**
```bash
pytest tests/test_admin_readiness.py -v
```
Новые тесты писать в `tests/test_owner_dashboard.py` по аналогии с `test_admin_readiness.py`.

---

## Архитектурные ограничения (обязательно соблюдать)

1. Все SQL-запросы фильтровать по `organization_id` — никогда не возвращать данные чужого тенанта.
2. LLM не вызывать внутри DB-сессии — здесь LLM не нужен, только SQL + арифметика.
3. Новые миграции не создавать — новых колонок нет, только новые запросы к существующим таблицам.
4. Не трогать `app/api/payment_webhook.py`.
5. CSS: только `ds-*` классы и Tailwind-утилиты, не `brand-*`. После правки шаблонов запустить `npm run build:admin-css`.
