"""Lightweight ETag helpers for admin GET endpoints (304 Not Modified)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response


def weak_etag_from_parts(*parts: Any) -> str:
    """Build a weak ETag from stable scalar / JSON-serializable parts."""
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, (dict, list)):
            chunks.append(json.dumps(part, sort_keys=True, default=str))
        else:
            chunks.append(str(part))
    digest = hashlib.sha256("|".join(chunks).encode()).hexdigest()[:16]
    return f'W/"{digest}"'


def json_with_etag(
    request: Request,
    payload: dict[str, Any],
    etag: str,
    *,
    max_age_sec: int = 30,
) -> Response:
    """Return JSON with ETag; honor If-None-Match → 304."""
    inm = (request.headers.get("if-none-match") or "").strip()
    headers = {
        "ETag": etag,
        "Cache-Control": f"private, max-age={max(0, int(max_age_sec))}",
    }
    if inm and inm == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(content=payload, headers=headers)
