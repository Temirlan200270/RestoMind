"""Единая нормализация телефона гостя (E.164) для WhatsApp, admin и БД."""

from __future__ import annotations

import re


def normalize_phone_e164(phone: str) -> str:
    """
    Приводит MSISDN к E.164: '+7705…'.
    iiko и WhatsApp ожидают '+' для KZ/RU.
    """
    raw = (phone or "").strip()
    if raw.startswith("+"):
        return raw
    digits = re.sub(r"\D+", "", raw)
    if not digits:
        return ""
    if len(digits) == 11 and digits.startswith("7"):
        return f"+{digits}"
    if len(digits) == 10:
        return f"+7{digits}"
    return f"+{digits}"


def canonical_user_phone(phone: str) -> str:
    """Ключ пользователя в БД/Redis — E.164 с «+» или ``tg:{id}`` для Telegram."""
    raw = (phone or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("tg:"):
        return raw.lower()
    normalized = normalize_phone_e164(raw)
    return normalized if normalized else raw


def phone_digits_key(phone: str) -> str:
    """Цифры MSISDN для сравнения дублей (77051310837)."""
    return re.sub(r"\D", "", phone or "")[-15:]


def phone_lookup_variants(phone: str) -> list[str]:
    """Варианты строки для поиска legacy-дублей в users.phone."""
    canon = canonical_user_phone(phone)
    if not canon:
        return []
    digits = phone_digits_key(canon)
    variants: set[str] = {canon}
    if digits:
        variants.add(digits)
        variants.add(f"+{digits}")
    if canon.startswith("+"):
        variants.add(canon[1:])
    return sorted(v for v in variants if v)
