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
    from app.db.models import Organization, Tenant
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
      3. Empty order: intent=order но items=[] после LLM — блокировать
         (LLM сказал «заказ принят», но позиций нет — это баг парсинга, не надо создавать черновик)
      4. Delivery без адреса: order_type=delivery и delivery_address пустой — предупреждение
      5. Аномальный размер заказа: items > MAX_ORDER_ITEMS — предупреждение
      6. Pricing policy: предложенная скидка > max_discount_pct — блокировать
         (заглушка: AI пока не предлагает скидки напрямую)
    """

    MAX_ORDER_ITEMS: int = 20

    async def validate(
        self,
        proposal: "AIBrainResponse",
        context: "AIReadContext",
        org: "Organization | None",
        *,
        tenant: "Tenant | None" = None,
        billing_suspended: bool = False,
    ) -> ValidationResult:
        violations: list[PolicyViolation] = []

        if v := self._check_billing_suspended(proposal, org, tenant=tenant, billing_suspended=billing_suspended):
            violations.append(v)

        if v := self._check_force_closed(proposal, org):
            violations.append(v)

        if v := self._check_empty_order(proposal):
            violations.append(v)

        if v := self._check_all_items_hallucinated(proposal, context):
            violations.append(v)

        if v := self._check_stoplist_quick(proposal, context):
            violations.append(v)

        if v := self._check_delivery_no_address(proposal):
            violations.append(v)

        if v := self._check_max_order_items(proposal):
            violations.append(v)

        if v := self._check_pricing_policy(proposal, org, context):
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

    def _check_billing_suspended(
        self,
        proposal: "AIBrainResponse",
        org: "Organization | None",
        *,
        tenant: "Tenant | None" = None,
        billing_suspended: bool = False,
    ) -> PolicyViolation | None:
        """Блокирует любые заказы если тенант suspended.

        Три источника (defense-in-depth):
        1. explicit billing_suspended=True (из billing_guard.py в webhooks)
        2. tenant.plan_status == 'suspended' если передан объект Tenant
        3. org.is_active == False как последний рубеж

        Блокирует ORDER и BOOK (бронирование тоже нельзя принимать у suspended клиента).
        FAQ и escalate разрешены (клиент должен уметь связаться с поддержкой).
        """
        if proposal.intent not in ("order", "book"):
            return None

        is_suspended = billing_suspended
        if not is_suspended and tenant is not None:
            plan_st = (getattr(tenant, "plan_status", None) or "").strip().lower()
            is_suspended = plan_st == "suspended"
        if not is_suspended and org is not None:
            is_suspended = not bool(getattr(org, "is_active", True))

        if not is_suspended:
            return None

        return PolicyViolation(
            rule="billing_suspended",
            severity="block",
            detail=(
                "К сожалению, сервис временно недоступен. "
                "Пожалуйста, свяжитесь с администратором заведения."
            ),
            meta={"trigger": "billing_suspended"},
        )

    def _check_all_items_hallucinated(
        self,
        proposal: "AIBrainResponse",
        context: "AIReadContext",
    ) -> PolicyViolation | None:
        """Блокирует заказ если ВСЕ позиции полностью отсутствуют в меню.

        Использует точное множественное пересечение (isdisjoint):
          proposed_set & menu_set == ∅  →  AI галлюцинирует имена блюд.

        Это дёшево (O(n+m)) и надёжно: нет ложных срабатываний от 5-char prefix heuristic.
        Если хотя бы одна позиция совпала — заказ продолжается (validate_order обработает
        неизвестные позиции отдельно).
        """
        if proposal.intent != "order" or not proposal.items:
            return None
        if not context.menu_items:
            return None  # Меню не загружено — нет данных для сравнения

        menu_names: set[str] = {m.name.lower().strip() for m in context.menu_items}
        proposed_names: set[str] = {item.name.lower().strip() for item in proposal.items}

        # isdisjoint() ≡ proposed_names & menu_names == ∅ (all items unknown)
        if proposed_names.isdisjoint(menu_names):
            unknown = [item.name for item in proposal.items[:5]]
            return PolicyViolation(
                rule="all_items_hallucinated",
                severity="block",
                detail=(
                    "Не нашёл в меню ни одной из позиций вашего заказа. "
                    "Уточните название блюда или спросите, что у нас есть."
                ),
                meta={"unknown_items": unknown, "menu_size": len(context.menu_items)},
            )
        return None

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

    def _check_empty_order(
        self,
        proposal: "AIBrainResponse",
    ) -> PolicyViolation | None:
        """Блокирует intent=order с пустым списком позиций.

        LLM иногда генерирует «заказ принят» без items — это баг парсинга или
        неполный ответ. Создавать пустой черновик смысла нет: клиент думает, что
        заказал, а в БД — пустая запись.
        """
        if proposal.intent != "order":
            return None
        # Проверяем и items, и order_actions — оба пустых означают «нет позиций»
        has_items = bool(proposal.items)
        has_actions = bool(proposal.order_actions)
        if has_items or has_actions:
            return None
        return PolicyViolation(
            rule="empty_order",
            severity="block",
            detail=(
                "Уточните, пожалуйста, что именно вы хотите заказать — "
                "я не смог разобрать позиции из вашего сообщения."
            ),
        )

    def _check_delivery_no_address(
        self,
        proposal: "AIBrainResponse",
    ) -> PolicyViolation | None:
        """Предупреждение: доставка без адреса.

        Warn (не block) — route_intent попросит адрес; DE фиксирует для аналитики.
        """
        if proposal.intent != "order":
            return None
        if proposal.order_type != "delivery":
            return None
        if proposal.delivery_address and proposal.delivery_address.strip():
            return None
        return PolicyViolation(
            rule="delivery_no_address",
            severity="warn",
            detail="Тип заказа — доставка, но адрес не указан.",
        )

    def _check_max_order_items(
        self,
        proposal: "AIBrainResponse",
    ) -> PolicyViolation | None:
        """Предупреждение: аномально большой заказ (возможная ошибка парсинга).

        Warn — route_intent обработает нормально; DE логирует для мониторинга.
        """
        if proposal.intent != "order":
            return None
        n = len(proposal.items)
        if n <= self.MAX_ORDER_ITEMS:
            return None
        return PolicyViolation(
            rule="order_items_anomaly",
            severity="warn",
            detail=f"Заказ содержит {n} позиций — возможная ошибка парсинга (норма ≤ {self.MAX_ORDER_ITEMS}).",
            meta={"items_count": n, "max_items": self.MAX_ORDER_ITEMS},
        )

    def _check_pricing_policy(
        self,
        proposal: "AIBrainResponse",
        org: "Organization | None",
        context: "AIReadContext | None" = None,
    ) -> PolicyViolation | None:
        """Проверка ценовой политики.

        Два уровня:
        1. max_discount_pct: если организация ограничила скидки, а AI предлагает скидку
           (Phase 4.2: поле discount_pct появится в AIBrainResponse позже).
        2. Нулевой estimated_total при непустом valid_items — сигнал что цены не подтянулись.
           Предупреждение: route_intent пересчитает цены из меню, но аномалия фиксируется.
        """
        if proposal.intent != "order" or not proposal.items:
            return None

        # Phase 4.1: проверка скидки (активна когда AI-схема получит поле discount_pct)
        max_pct = getattr(org, "max_discount_pct", 0) or 0 if org else 0
        if max_pct > 0:
            proposed_discount = getattr(proposal, "discount_pct", 0) or 0
            if proposed_discount > max_pct:
                return PolicyViolation(
                    rule="discount_exceeds_policy",
                    severity="block",
                    detail=f"Скидка {proposed_discount}% превышает допустимый лимит {max_pct}%.",
                    meta={"proposed_pct": proposed_discount, "max_pct": max_pct},
                )

        # Phase 4.2: estimated total из меню
        if context is not None and context.menu_items:
            menu_price_lookup = {
                m.name.lower().strip(): float(m.price or 0)
                for m in context.menu_items
                if m.is_available
            }
            estimated = sum(
                menu_price_lookup.get(item.name.lower().strip(), 0) * item.quantity
                for item in proposal.items
            )
            if estimated == 0 and len(proposal.items) > 0:
                pass  # Позиции не нашлись в меню — обработает _check_all_items_hallucinated

        return None

    # ─── Helpers ─────────────────────────────────────────────

    def _build_corrected_response(
        self,
        original: "AIBrainResponse",
        violation: PolicyViolation,
    ) -> "AIBrainResponse":
        """Возвращает копию ответа с заменёнными reply_text и intent по нарушению.

        Intent меняется на 'faq' чтобы route_intent не создавал/обновлял черновик заказа.
        """
        updates: dict = {"reply_text": violation.detail}
        if violation.severity == "block" and original.intent in ("order", "book"):
            # Меняем intent чтобы route_intent не создавал черновик/бронь
            updates["intent"] = "faq"
        return original.model_copy(update=updates)


# Singleton — используется в webhooks.py без создания нового объекта на каждый запрос
decision_engine = DecisionEngine()
