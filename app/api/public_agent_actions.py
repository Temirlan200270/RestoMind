"""Public (signed-token) agent action confirmation — Telegram/digest deep links."""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.tenant_rls import apply_tenant_rls, reset_tenant_rls_context, set_tenant_rls_context
from app.services.agent_action_tokens import parse_agent_action_confirm_token
from app.services.agent_actions import confirm_agent_action_by_token

router = APIRouter(tags=["Public Agent Actions"])


def _confirm_page_html(
    *,
    title: str,
    body: str,
    token: str | None = None,
    applied: bool = False,
) -> str:
    safe_title = escape(title)
    safe_body = escape(body)
    form = ""
    if token and not applied:
        safe_token = escape(token, quote=True)
        form = f"""
<form method="post" action="/api/public/agent-actions/confirm?token={safe_token}" style="margin-top:1.5rem">
  <button type="submit" style="border:0;border-radius:12px;background:#4f46e5;color:white;padding:.75rem 1rem;font-weight:700;cursor:pointer">
    Подтвердить и применить
  </button>
</form>
<p style="color:#64748b;font-size:.9rem;margin-top:1rem">Открытие ссылки не применяет действие. Нажмите кнопку, чтобы подтвердить.</p>"""
    return f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><title>{safe_title}</title>
<meta name="robots" content="noindex,nofollow"></head>
<body style="font-family:system-ui,sans-serif;padding:2rem;max-width:40rem;margin:auto">
<h1>{safe_title}</h1><p>{safe_body}</p>{form}
<p style="margin-top:2rem"><a href="/admin">Открыть админку</a></p></body></html>"""


@router.get("/public/agent-actions/confirm", response_class=HTMLResponse)
async def public_agent_action_confirm_page(
    token: str = Query(..., min_length=8),
) -> HTMLResponse:
    claims = parse_agent_action_confirm_token(token)
    if claims is None:
        raise HTTPException(status_code=400, detail="Invalid or expired confirmation link")
    html = _confirm_page_html(
        title="Подтвердите действие",
        body="RestoMind подготовил действие. Оно будет применено только после нажатия кнопки подтверждения.",
        token=token,
    )
    return HTMLResponse(content=html)


@router.post("/public/agent-actions/confirm", response_class=HTMLResponse)
async def public_agent_action_confirm_apply(
    token: str = Query(..., min_length=8),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    claims = parse_agent_action_confirm_token(token)
    if claims is None:
        raise HTTPException(status_code=400, detail="Invalid or expired confirmation link")

    ctx_token = set_tenant_rls_context(claims.organization_id)
    try:
        await apply_tenant_rls(db)
        result = await confirm_agent_action_by_token(
            db,
            proposal_id=claims.proposal_id,
            organization_id=claims.organization_id,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Proposal not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not allowed") from None
    finally:
        reset_tenant_rls_context(ctx_token)

    title = "Действие применено"
    body = "RestoMind применил предложенное действие."
    if result.get("already_applied"):
        title = "Действие уже было применено"
        body = "Эта ссылка уже использована ранее."
    html = _confirm_page_html(title=title, body=body, applied=True)
    return HTMLResponse(content=html)
