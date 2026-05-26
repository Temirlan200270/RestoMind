"""Owner Intelligence weekly digest — ROI, upsell, QA, Kitchen Gate."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Organization
from app.services.owner_intelligence import build_owner_intelligence_summary
from app.services.owner_roi import _money_ru, _previous_week_bounds_local

logger = logging.getLogger(__name__)


def _format_top_actions(actions: list[dict[str, Any]], sym: str) -> list[str]:
    lines: list[str] = []
    for idx, action in enumerate(actions[:5], start=1):
        title = str(action.get("title") or action.get("label") or "Действие").strip()
        impact = action.get("impact_kzt")
        if impact is not None and float(impact) > 0:
            lines.append(f"{idx}. {title} (~{_money_ru(float(impact), sym)})")
        else:
            lines.append(f"{idx}. {title}")
    return lines


async def build_owner_intelligence_weekly_digest(
    db: AsyncSession,
    org: Organization,
) -> dict[str, Any] | None:
    """Сводка за прошлую календарную неделю для Telegram-дайджеста владельца."""
    summary = await build_owner_intelligence_summary(db, int(org.id), period="prev_week")
    lo_l, hi_l = _previous_week_bounds_local(org.timezone or "UTC")
    cur = (org.currency or "KZT").strip().upper() or "KZT"
    sym = "₸" if cur == "KZT" else cur

    accepted = float(summary.get("accepted_revenue") or 0)
    upsell = summary.get("upsell_impact") or {}
    upsell_rev = float(upsell.get("added_revenue") or upsell.get("revenue_kzt") or 0)
    recovered = float(summary.get("recovered_revenue") or 0)
    lost = float(summary.get("lost_revenue") or 0)
    net_roi = float(summary.get("net_roi") or 0)

    qa = summary.get("qa_risk_summary") or {}
    qa_open = int(qa.get("open_count") or 0)
    qa_high = int(qa.get("high_count") or 0)
    qa_critical = int(qa.get("critical_count") or 0)

    kg = summary.get("kitchen_gate_impact") or {}
    kg_blocked = int(kg.get("orders_blocked") or 0)

    top_actions = list(summary.get("top_actions") or [])

    lines = [
        f"RestoMind — неделя ({lo_l.strftime('%d.%m')}–{hi_l.strftime('%d.%m')})",
        f"• Принято заказов: {_money_ru(accepted, sym)}",
    ]
    if upsell_rev > 0:
        conv = upsell.get("conversion_rate") or upsell.get("conversion_pct")
        conv_txt = f", конверсия {conv}%" if conv is not None else ""
        lines.append(f"• Допродажи: {_money_ru(upsell_rev, sym)}{conv_txt}")
    if recovered > 0:
        lines.append(f"• Восстановлено: {_money_ru(recovered, sym)}")
    if lost > 0:
        lines.append(f"• Потери: {_money_ru(lost, sym)}")
    lines.append(f"• Net ROI: {_money_ru(net_roi, sym)}")
    if qa_open > 0 or qa_high > 0 or qa_critical > 0:
        qa_parts = [f"{qa_open} открыто"]
        if qa_high > 0:
            qa_parts.append(f"{qa_high} high")
        if qa_critical > 0:
            qa_parts.append(f"{qa_critical} critical")
        lines.append(f"• QA риски: {', '.join(qa_parts)}")
    if kg_blocked > 0:
        lines.append(f"• Kitchen Gate: заблокировано {kg_blocked} попыток заказа")
    action_lines = _format_top_actions(top_actions, sym)
    if action_lines:
        lines.append("• Топ действий:")
        lines.extend(f"  {row}" for row in action_lines)

    text = "\n".join(lines)
    return {
        "text": text,
        "summary": summary,
        "metrics": {
            "accepted_revenue": accepted,
            "upsell_revenue": upsell_rev,
            "recovered_revenue": recovered,
            "lost_revenue": lost,
            "net_roi": net_roi,
            "qa_open": qa_open,
            "qa_high": qa_high,
            "qa_critical": qa_critical,
            "kitchen_gate_blocked": kg_blocked,
            "top_actions_count": len(top_actions[:5]),
        },
    }
