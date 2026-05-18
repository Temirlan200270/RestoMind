"""Decision Engine — OS Phase 4.

Слой валидации между AI-ответом и исполнением:
  AI → Proposal → DecisionEngine.validate() → System executes (или корректирует).

Принцип Strangler Pattern: обёртка поверх route_intent, не замена.
Seed: validate_order в intent_router.py — тактический прецедент этого слоя.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.db.models import Organization
    from app.schemas.ai_schemas import AIBrainResponse
    from app.services.context_engine import AIReadContext

logger = logging.getLogger(__name__)


@dataclass
class PolicyViolation:
    """Нарушение бизнес-правила или политики ресторана."""

    rule: str
    severity: Literal["block", "warn"]
    detail: str
    meta: dict = field(default_factory=dict)


@dataclass
class ValidationResult:
    """Результат валидации AI-ответа через Decision Engine."""

    is_valid: bool
    violations: list[PolicyViolation] = field(default_factory=list)
    corrected_response: "AIBrainResponse | None" = None

    @property
    def has_blocks(self) -> bool:
        return any(v.severity == "block" for v in self.violations)

    @property
    def has_warnings(self) -> bool:
        return any(v.severity == "warn" for v in self.violations)

    @property
    def block_violations(self) -> list[PolicyViolation]:
        return [v for v in self.violations if v.severity == "block"]


class DecisionEngine:
    """
    AI → Proposal → DecisionEngine.validate() → System executes.

    Проверяет:
      1. Force-closed: ресторан временно закрыт — блокировать заказы
      2. Stoplist quick-check: предложенные позиции на стопе — предупреждение
         (route_intent всё равно обрабатывает, DE добавляет структурированную запись)
      3. Pricing policy: предложенная скидка > max_discount_pct — блокировать
         (заглушка: AI пока не предлагает скидки напрямую)
    """

    async def validate(
        self,
        proposal: "AIBrainResponse",
        context: "AIReadContext",
        org: "Organization | None",
    ) -> ValidationResult:
        violations: list[PolicyViolation] = []

        if v := self._check_force_closed(proposal, org):
            violations.append(v)

        if v := self._check_stoplist_quick(proposal, context):
            violations.append(v)

        if v := self._check_pricing_policy(proposal, org):
            violations.append(v)

        blocks = [v for v in violations if v.severity == "block"]
        is_valid = len(blocks) == 0

        corrected: "AIBrainResponse | None" = None
        if blocks:
            corrected = self._build_corrected_response(proposal, blocks[0])

        if violations:
            logger.info(
                "DecisionEngine: org=%s intent=%s violations=%s",
                getattr(org, "id", "?"),
                proposal.intent,
                [(v.rule, v.severity) for v in violations],
            )

        return ValidationResult(
            is_valid=is_valid,
            violations=violations,
            corrected_response=corrected,
        )

    # ─── Checks ──────────────────────────────────────────────

    def _check_force_closed(
        self,
        proposal: "AIBrainResponse",
        org: "Organization | None",
    ) -> PolicyViolation | None:
        """Заказ блокируется пока ресторан на экстренном закрытии."""
        if proposal.intent != "order":
            return None  # FAQ/book/escalate разрешены
        if org is None:
            return None
        fc_until = getattr(org, "force_closed_until", None)
        if fc_until is None:
            return None
        now = datetime.now(tz=timezone.utc)
        if fc_until.tzinfo is None:
            fc_until = fc_until.replace(tzinfo=timezone.utc)
        if now >= fc_until:
            return None  # Закрытие уже истекло
        reason = (getattr(org, "force_closed_reason", "") or "").strip()
        reply = f"К сожалению, сейчас мы временно не принимаем заказы"
        if reason:
            reply += f": {reason}"
        reply += ". Попробуйте позже или напишите нам снова после открытия."
        return PolicyViolation(
            rule="force_closed",
            severity="block",
            detail=reply,
            meta={"force_closed_until": fc_until.isoformat()},
        )

    def _check_stoplist_quick(
        self,
        proposal: "AIBrainResponse",
        context: "AIReadContext",
    ) -> PolicyViolation | None:
        """Быстрая проверка стоп-позиций без повторного DB-запроса.

        Только предупреждение (warn) — route_intent в intent_router.py
        всё равно обрабатывает стоп-лист и сообщает клиенту. DE добавляет
        структурированную запись для аналитики.
        """
        if proposal.intent != "order" or not proposal.items:
            return None
        stopped_names = {
            m.name.lower().strip()
            for m in context.menu_items
            if not m.is_available
        }
        blocked = [
            item.name
            for item in proposal.items
            if item.name.lower().strip() in stopped_names
        ]
        if not blocked:
            return None
        return PolicyViolation(
            rule="stoplist",
            severity="warn",
            detail=f"Позиции временно недоступны: {', '.join(blocked)}",
            meta={"stoplist_items": blocked},
        )

    def _check_pricing_policy(
        self,
        proposal: "AIBrainResponse",
        org: "Organization | None",
    ) -> PolicyViolation | None:
        """Проверка ценовой политики: предложенная скидка > max_discount_pct.

        Заглушка Phase 4.1: AIBrainResponse пока не содержит поля discount.
        Активируется когда AI-схема будет расширена для предложения скидок.
        """
        if org is None or proposal.intent != "order":
            return None
        max_pct = getattr(org, "max_discount_pct", 0) or 0
        if max_pct <= 0:
            return None
        # TODO Phase 4.1: проверить proposal.discount_pct > max_pct когда поле появится
        return None

    # ─── Helpers ─────────────────────────────────────────────

    def _build_corrected_response(
        self,
        original: "AIBrainResponse",
        violation: PolicyViolation,
    ) -> "AIBrainResponse":
        """Возвращает копию ответа с заменённым reply_text по нарушению."""
        return original.model_copy(update={"reply_text": violation.detail})


# Singleton — используется в webhooks.py без создания нового объекта на каждый запрос
decision_engine = DecisionEngine()
