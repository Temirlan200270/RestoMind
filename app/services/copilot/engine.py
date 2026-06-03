"""Tool-based owner copilot orchestration."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import async_session_factory
from app.services.copilot import tools
from app.services.copilot.business_questions import questions_for_role, tools_for_role_question

logger = logging.getLogger(__name__)


def _period_from_question(question: str) -> str:
    q = (question or "").lower()
    if "вчера" in q or "yesterday" in q:
        return "yesterday"
    if "месяц" in q or "30" in q or "month" in q:
        return "30d"
    if "сегодня" in q or "today" in q:
        return "today"
    return "7d"


def _select_tools(question: str, *, role: str | None = None) -> list[str]:
    q = (question or "").lower()
    selected: list[str] = tools_for_role_question(role, question)
    if any(x in q for x in ("выруч", "заработ", "чек", "продаж", "revenue", "sales")):
        selected.extend(["get_revenue_summary", "compare_periods"])
    if any(x in q for x in ("блюд", "позици", "товар", "самые", "топ", "dish", "item")):
        selected.append("get_top_dishes")
    if any(x in q for x in ("категор", "category")):
        selected.append("get_category_breakdown")
    if any(x in q for x in ("почему", "упал", "аномал", "причин", "insight", "anomaly")):
        selected.extend(["get_revenue_summary", "get_anomalies", "get_category_breakdown"])
    if any(x in q for x in ("закуп", "остат", "stock", "purchase", "склад")):
        selected.extend(["get_stock_alerts", "get_demand_forecast"])
    if any(x in q for x in ("прогноз", "forecast", "выходн", "спрос")):
        selected.append("get_demand_forecast")
    if any(x in q for x in ("час", "пик", "heatmap", "hour")):
        selected.append("get_hourly_heatmap")
    if any(x in q for x in ("офици", "waiter")):
        selected.append("get_waiter_kpi")
    if any(x in q for x in ("себест", "марж", "food cost", "margin", "прибыл")):
        selected.extend(["get_food_cost_margin", "get_low_margin_high_revenue_dishes"])
    if any(x in q for x in ("качество", "confidence", "уверен", "data quality", "данные")):
        selected.append("get_data_quality_status")
    if any(x in q for x in ("откуда", "источник", "checksum", "lineage", "доказ", "верить")):
        selected.append("get_data_lineage")
    if any(x in q for x in ("прямо сейчас", "сейчас", "live", "открыт", "текущ")):
        selected.append("get_live_sales_preview")
    if any(x in q for x in ("память", "истори", "замет", "акци", "цен", "campaign", "memory")):
        selected.append("find_related_memory_events")
    if any(x in q for x in ("поставщик", "supplier", "риск постав")):
        selected.append("get_supplier_exposure")
    if any(x in q for x in ("поднять цен", "изменить цен", "simulate", "симуляц")):
        selected.append("simulate_price_change")
    if any(x in q for x in ("сезон", "season")):
        selected.append("get_seasonal_dish_trends")
    if not selected:
        selected = ["get_revenue_summary", "get_anomalies"]
    selected.append("get_data_quality_status")

    out: list[str] = []
    for name in selected:
        if name not in out:
            out.append(name)
    return out[:7]


async def _run_tool(db: AsyncSession, org_id: int, name: str, period: str, question: str) -> dict[str, Any]:
    if name == "get_revenue_summary":
        return await tools.get_revenue_summary(db, org_id, period=period)
    if name == "compare_periods":
        return await tools.compare_periods(db, org_id, period=period)
    if name == "get_top_dishes":
        return await tools.get_top_dishes(db, org_id, period=period)
    if name == "get_category_breakdown":
        return await tools.get_category_breakdown(db, org_id, period=period)
    if name == "get_hourly_heatmap":
        return await tools.get_hourly_heatmap(db, org_id, period=period)
    if name == "get_waiter_kpi":
        return await tools.get_waiter_kpi(db, org_id, period=period)
    if name == "get_food_cost_margin":
        return await tools.get_food_cost_margin(db, org_id, period=period)
    if name == "get_anomalies":
        return await tools.get_anomalies(db, org_id)
    if name == "get_stock_alerts":
        return await tools.get_stock_alerts(db, org_id)
    if name == "get_demand_forecast":
        return await tools.get_demand_forecast(db, org_id)
    if name == "get_data_quality_status":
        return await tools.get_data_quality_status(db, org_id)
    if name == "get_data_lineage":
        return await tools.get_data_lineage(db, org_id)
    if name == "get_live_sales_preview":
        return await tools.get_live_sales_preview(db, org_id)
    if name == "get_org_memory":
        return await tools.get_org_memory(db, org_id)
    if name == "find_related_memory_events":
        return await tools.find_related_memory_events(db, org_id, query=question)
    if name == "get_low_margin_high_revenue_dishes":
        return await tools.get_low_margin_high_revenue_dishes(db, org_id)
    if name == "simulate_price_change":
        return await tools.simulate_price_change(db, org_id, product_name=question)
    if name == "get_supplier_exposure":
        return await tools.get_supplier_exposure(db, org_id)
    if name == "get_seasonal_dish_trends":
        return await tools.get_seasonal_dish_trends(db, org_id)
    raise ValueError(f"Tool is not allowed: {name}")


async def _collect_tool_results(
    db: AsyncSession,
    *,
    org_id: int,
    question: str,
    role: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    period = _period_from_question(question)
    results: list[dict[str, Any]] = []
    for name in _select_tools(question, role=role):
        results.append(await _run_tool(db, org_id, name, period, question))
    return period, results


async def run_owner_copilot_for_org(
    *,
    org_id: int,
    question: str,
    role: str | None = None,
) -> dict[str, Any]:
    """Read tools in a DB session, close it, then call the LLM."""
    async with async_session_factory() as db:
        period, results = await _collect_tool_results(db, org_id=org_id, question=question, role=role)

    fallback_answer = _compose_answer(question, results)
    answer = await _llm_answer_or_fallback(question=question, tool_results=results, fallback=fallback_answer)
    return {
        "answer": answer,
        "period": period,
        "tool_calls": [{"name": r.get("tool"), "result": r} for r in results],
        "data": {str(r.get("tool")): r for r in results},
        "llm_used": answer != fallback_answer,
        "business_questions": questions_for_role(role),
    }


async def run_owner_copilot(
    db: AsyncSession,
    *,
    org_id: int,
    question: str,
    role: str | None = None,
) -> dict[str, Any]:
    """Backward-compatible deterministic path for tests and local callers."""
    period, results = await _collect_tool_results(db, org_id=org_id, question=question, role=role)
    answer = _compose_answer(question, results)
    return {
        "answer": answer,
        "period": period,
        "tool_calls": [{"name": r.get("tool"), "result": r} for r in results],
        "data": {str(r.get("tool")): r for r in results},
        "llm_used": False,
        "business_questions": questions_for_role(role),
    }


async def _llm_answer_or_fallback(
    *,
    question: str,
    tool_results: list[dict[str, Any]],
    fallback: str,
) -> str:
    if not ((settings.openai_api_key or "").strip() or (settings.gemini_api_key or "").strip()):
        return fallback
    try:
        from app.services.ai_brain import get_ai_client

        prompt = (
            "You are an owner-facing restaurant AI analyst. Answer in the operator's language. "
            "Use only the JSON tool results below; do not invent numbers. "
            "Always mention low data confidence if present. Keep it concise and actionable.\n\n"
            f"Question: {question}\n"
            f"Tool results JSON: {json.dumps(tool_results, ensure_ascii=False, default=str)[:12000]}"
        )
        response = await get_ai_client().generate_response(
            history=[],
            user_text=prompt,
            menu_context="",
            kb_context="",
            model_tier="fast",
        )
        text = (getattr(response, "reply_text", "") or "").strip()
        lowered = text.lower()
        if "технические сложности" in lowered or "переключаю на оператора" in lowered:
            return fallback
        if text:
            return text
    except Exception:
        logger.info("copilot LLM answer failed; using deterministic fallback", exc_info=True)
    return fallback


def _compose_answer(question: str, results: list[dict[str, Any]]) -> str:
    by_tool = {str(r.get("tool")): r for r in results}
    parts: list[str] = []

    quality = by_tool.get("get_data_quality_status")
    if quality and float(quality.get("confidence_score") or 0) < 0.7:
        parts.append(
            "Данные частичные, уверенность низкая: "
            f"{float(quality.get('confidence_score') or 0) * 100:.0f}%. Цифры ниже стоит перепроверить."
        )
    lineage = by_tool.get("get_data_lineage")
    if lineage and lineage.get("snapshot"):
        snap = lineage["snapshot"]
        counts = lineage.get("counts") or {}
        parts.append(
            f"Lineage: snapshot #{snap.get('id')} checksum {str(snap.get('checksum') or '')[:10]}..., "
            f"canonical orders {counts.get('canonical_orders', 0)}, fact orders {counts.get('fact_orders', 0)}."
        )
    live = by_tool.get("get_live_sales_preview")
    if live:
        parts.append(
            f"Live preview: ожидаемая выручка {float(live.get('expected_revenue') or 0):.0f} ₸ "
            f"по {int(live.get('order_count') or 0)} открытым/текущим заказам. Это preliminary, не закрытый OLAP fact."
        )

    revenue = by_tool.get("get_revenue_summary")
    if revenue:
        parts.append(
            f"За период {revenue.get('date_from')}-{revenue.get('date_to')} выручка "
            f"{float(revenue.get('revenue') or 0):.0f} ₸, заказов {int(revenue.get('orders') or 0)}, "
            f"средний чек {float(revenue.get('avg_check') or 0):.0f} ₸."
        )
        dips = [x for x in revenue.get("daily", []) if x.get("delta_pct") is not None and float(x["delta_pct"]) < -10]
        if dips:
            worst = sorted(dips, key=lambda x: float(x["delta_pct"]))[0]
            parts.append(f"Самое заметное отклонение: {worst['date']} ({float(worst['delta_pct']):.1f}% к baseline).")

    comparison = by_tool.get("compare_periods")
    if comparison:
        changes = comparison.get("changes") or {}
        rev = changes.get("revenue_pct")
        orders = changes.get("orders_pct")
        if rev is not None or orders is not None:
            parts.append(f"К предыдущему периоду: выручка {rev if rev is not None else 'n/a'}%, заказы {orders if orders is not None else 'n/a'}%.")

    dishes = by_tool.get("get_top_dishes")
    if dishes and dishes.get("items"):
        top = dishes["items"][0]
        parts.append(f"Главный вклад по блюдам: {top['name']} на {float(top['revenue']):.0f} ₸.")

    cats = by_tool.get("get_category_breakdown")
    if cats and cats.get("items"):
        top_cat = cats["items"][0]
        parts.append(f"Лидирующая категория: {top_cat['category']} ({float(top_cat['revenue']):.0f} ₸).")

    waiter = by_tool.get("get_waiter_kpi")
    if waiter and waiter.get("items"):
        top_waiter = waiter["items"][0]
        parts.append(f"Лучший официант по выручке: {top_waiter['waiter']} ({float(top_waiter['revenue']):.0f} ₸).")

    margin = by_tool.get("get_food_cost_margin")
    if margin and margin.get("items"):
        first = margin["items"][0]
        parts.append(f"Маржа в категории {first['category']}: {float(first['margin']):.0f} ₸, food cost {float(first['food_cost_pct']):.1f}%.")

    anomalies = by_tool.get("get_anomalies")
    if anomalies and anomalies.get("items"):
        first = anomalies["items"][0]
        confidence = first.get("confidence_score")
        confidence_text = f" Уверенность {float(confidence) * 100:.0f}%." if confidence is not None else ""
        evidence = first.get("evidence") or {}
        evidence_text = ""
        if evidence.get("delta_pct") is not None:
            evidence_text = f" Основание: отклонение {float(evidence.get('delta_pct') or 0):.1f}% к baseline."
        parts.append(f"Последний инсайт: {first['title']} - {first['summary']}.{confidence_text}{evidence_text}")

    stock = by_tool.get("get_stock_alerts")
    if stock and stock.get("items"):
        first = stock["items"][0]
        parts.append(
            f"По закупкам первым проверьте {first.get('name') or first.get('sku')}: "
            f"рекомендовано докупить {float(first.get('recommended_order_quantity') or 0):.3f} {first.get('unit') or ''}."
        )

    forecast = by_tool.get("get_demand_forecast")
    if forecast:
        parts.append(
            f"Прогноз на {forecast.get('days_ahead')} дней: "
            f"{float(forecast.get('forecast_revenue') or 0):.0f} ₸, уверенность {forecast.get('confidence')}."
        )

    low_margin = by_tool.get("get_low_margin_high_revenue_dishes")
    if low_margin and low_margin.get("items"):
        first = low_margin["items"][0]
        parts.append(
            f"Маржинальный риск: {first.get('name')} дает {float(first.get('revenue_30d') or 0):.0f} ₸ за 30 дней, "
            f"маржа около {first.get('margin_pct')}%."
        )

    memory = by_tool.get("find_related_memory_events") or by_tool.get("get_org_memory")
    if memory and memory.get("items"):
        first = memory["items"][0]
        parts.append(f"Из памяти ресторана: {first.get('date')} - {first.get('summary')}")

    suppliers = by_tool.get("get_supplier_exposure")
    if suppliers and suppliers.get("items"):
        first = suppliers["items"][0]
        parts.append(f"Риск поставщика: {first.get('supplier')} связан с {first.get('dish_count')} блюдами.")

    price_sim = by_tool.get("simulate_price_change")
    if price_sim and price_sim.get("found"):
        parts.append(
            f"Симуляция цены для {price_sim.get('name')}: новая цена {float(price_sim.get('new_price') or 0):.0f} ₸, "
            f"оценка изменения выручки за 30 дней {float(price_sim.get('estimated_revenue_delta_30d') or 0):.0f} ₸."
        )

    if not parts:
        return "Пока недостаточно OLAP-данных. Запустите синхронизацию iiko OLAP и повторите вопрос."
    return " ".join(parts)
