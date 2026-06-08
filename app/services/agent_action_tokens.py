"""Signed tokens for out-of-band agent action confirmation (Telegram/digest)."""

from __future__ import annotations

from dataclasses import dataclass

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import settings

_CONFIRM_MAX_AGE_SEC = 72 * 3600
_SALT = "restomind-agent-action-confirm-v1"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret_key, salt=_SALT)


@dataclass(frozen=True)
class AgentActionConfirmClaims:
    proposal_id: str
    organization_id: int


def create_agent_action_confirm_token(*, proposal_id: str, organization_id: int) -> str:
    return _serializer().dumps({"pid": str(proposal_id), "oid": int(organization_id)})


def parse_agent_action_confirm_token(token: str) -> AgentActionConfirmClaims | None:
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=_CONFIRM_MAX_AGE_SEC)
        pid = data.get("pid")
        oid = data.get("oid")
        if not pid or oid is None:
            return None
        return AgentActionConfirmClaims(proposal_id=str(pid), organization_id=int(oid))
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return None


def build_confirm_url(*, proposal_id: str, organization_id: int) -> str | None:
    base = (settings.public_base_url or "").strip().rstrip("/")
    if not base:
        return None
    if not base.startswith(("http://", "https://")):
        base = f"https://{base.lstrip('/')}"
    token = create_agent_action_confirm_token(proposal_id=proposal_id, organization_id=organization_id)
    return f"{base}/api/public/agent-actions/confirm?token={token}"
