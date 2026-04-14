"""Накопление отказов по UUID в order_meta."""

from app.schemas.ai_schemas import AIBrainResponse
from app.services.intent_router import _merge_rejected_upsell_into_order_meta


def test_merge_rejected_upsell_dedupes_and_extends() -> None:
    ai = AIBrainResponse(
        intent="order",
        reply_text="Только плов, без салата",
        rejected_upsell_iiko_ids=["  AAA  ", "bbb", "aaa"],
    )
    base = {"order_meta": {"upsell_rejected_iiko_ids": ["ccc"]}}
    out = _merge_rejected_upsell_into_order_meta(base, ai)
    assert out["order_meta"]["upsell_rejected_iiko_ids"] == ["aaa", "bbb", "ccc"]


def test_merge_rejected_upsell_noop_when_empty() -> None:
    ai = AIBrainResponse(intent="order", reply_text="Ок", rejected_upsell_iiko_ids=[])
    base = {"items": [], "order_meta": {"foo": 1}}
    out = _merge_rejected_upsell_into_order_meta(base, ai)
    assert out == base
