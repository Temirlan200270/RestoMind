"""Серверный gastro_hint в order_meta.recommendation / recommendation_trace."""

from app.schemas.ai_schemas import AIBrainResponse
from app.services.intent_router import _merge_recommendation_into_order_meta


def test_merge_recommendation_includes_gastro_hint() -> None:
    ai = AIBrainResponse(
        intent="order",
        reply_text="Добавить чай?",
        is_recommendation=True,
        upsell_offered="Чай",
        upsell_reasoning="К заказу подойдёт напиток.",
    )
    items_json: dict = {"order_meta": {}}
    out = _merge_recommendation_into_order_meta(
        items_json,
        ai,
        gastro_hint="[Теги: spicy + drink]",
    )
    meta = out["order_meta"]
    assert isinstance(meta, dict)
    rec = meta.get("recommendation")
    assert isinstance(rec, dict)
    assert rec.get("gastro_hint") == "[Теги: spicy + drink]"
    tr = meta.get("recommendation_trace")
    assert isinstance(tr, list) and tr
    assert tr[-1].get("gastro_hint") == "[Теги: spicy + drink]"


def test_merge_recommendation_empty_gastro_hint_omits_field() -> None:
    ai = AIBrainResponse(
        intent="order",
        reply_text="Ок",
        is_recommendation=True,
        upsell_offered="Лепёшка",
    )
    out = _merge_recommendation_into_order_meta({"order_meta": {}}, ai, gastro_hint="")
    rec = out["order_meta"]["recommendation"]
    assert "gastro_hint" not in rec


def test_merge_recommendation_strategy_logic_custom_when_id_differs() -> None:
    ai = AIBrainResponse(
        intent="order",
        reply_text="Предлагаю другое",
        is_recommendation=True,
        upsell_offered="Кола",
        upsell_offered_id="uuid-other",
        upsell_reasoning="Клиенту так лучше",
    )
    out = _merge_recommendation_into_order_meta(
        {"order_meta": {}},
        ai,
        gastro_hint="[Теги: spicy + drink]",
        server_target_iiko_ids=["uuid-server-pick"],
    )
    rec = out["order_meta"]["recommendation"]
    assert rec.get("strategy_logic") == "Custom AI Choice"
    assert rec.get("gastro_hint") == "[Теги: spicy + drink]"


def test_merge_recommendation_no_custom_when_id_matches() -> None:
    ai = AIBrainResponse(
        intent="order",
        reply_text="Ок",
        is_recommendation=True,
        upsell_offered="Компот",
        upsell_offered_id="uuid-same",
    )
    out = _merge_recommendation_into_order_meta(
        {"order_meta": {}},
        ai,
        gastro_hint="[Теги: spicy + drink]",
        server_target_iiko_ids=["uuid-same", "uuid-alt"],
    )
    rec = out["order_meta"]["recommendation"]
    assert "strategy_logic" not in rec
